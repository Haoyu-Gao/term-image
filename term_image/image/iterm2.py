from __future__ import annotations

__all__ = ("ITerm2Image",)

import io
import re
import sys
from base64 import standard_b64encode
from typing import Set, Union

import PIL

from ..utils import lock_tty, query_terminal, read_tty
from .common import GraphicsImage

# Constants for render methods
LINES = "lines"
WHOLE = "whole"


class ITerm2Image(GraphicsImage):
    """A render style using the iTerm2 inline image protocol.

    See :py:class:`GraphicsImage` for the complete description of the constructor.

    **Render Methods:**

    :py:class:`KittyImage` provides two methods of :term:`rendering` images, namely:

    lines
       Renders an image line-by-line i.e the image if evenly split up across
       the number of line it should occupy and all portions is joined together by
       ``\\n`` (newline sequence) to give the whole image.

       Pros:

         * Good for use cases where it might be required to trim some lines of the
           image.

    whole
       Renders an image all at once i.e the entire image data is encoded into the first
       line of the :term:`rendered` output, such that the entire image is drawn once
       by the terminal and still occupies the proper amount of lines and columns.

       Pros:

         * Renders faster and results are more compact (i.e less in character count)
           compared to the ``lines`` method since the entire image is encoded at once.
         * Better for images that are large in resolution and pixel density.

    The render method can be set with
    :py:meth:`set_render_method() <BaseImage.set_render_method>` using the names
    specified above.

    ATTENTION:
        Currently supported terminal emulators include:

          * `iTerm2 <https://iterm2.com>`_.
          * `Konsole <https://konsole.kde.org>`_ >= 22.04.0.
          * `WezTerm <https://wezfurlong.org/wezterm/>`_.
    """

    _render_methods: Set[str] = {LINES, WHOLE}
    _default_render_method: str = LINES
    _render_method: str = LINES

    _TERM: str = ""
    _TERM_VERSION: str = ""

    @classmethod
    @lock_tty  # the terminal's response to the query is not read all at once
    def is_supported(cls):
        if cls._supported is None:
            # Terminal name/version query + terminal attribute query
            # The latter is to speed up the entirequery since most (if not all)
            # terminals should support it and most terminals treat queries as FIFO
            response = query_terminal(
                b"\033[>q\033[c", lambda s: not s.endswith(b"\033[?6")
            ).decode()
            read_tty()  # The rest of the response to `CSI c`

            # Not supported if the terminal doesn't respond to either query
            # or responds to the second but not the first
            if response:
                match = re.fullmatch(
                    r"\033P>\|(\w+)[( ]([^\033]+)\)?\033\\",
                    response.rpartition("\033")[0],
                )
                if match and match.group(1).lower() in {"iterm2", "konsole", "wezterm"}:
                    name, version = map(str.lower, match.groups())
                    try:
                        if name == "konsole" and (
                            tuple(map(int, version.split("."))) < (22, 4, 0)
                        ):
                            cls._supported = False
                        else:
                            cls._supported = True
                            cls._TERM, cls._TERM_VERSION = name, version
                    except ValueError:  # version string not "understood"
                        cls._supported = False
            else:
                cls._supported = False

        return cls._supported

    @classmethod
    def _clear_images(cls):
        if cls._TERM == "konsole":
            # Only works and required on Konsole, as text doesn't overwrite image cells.
            # Seems Konsole utilizes the same image rendering implementation as it
            # uses for the kiity graphics protocol.
            _stdout_write(b"\033_Ga=d;\033\\")
            return True
        return False

    @staticmethod
    def _handle_interrupted_draw():
        """Performs neccessary actions when image drawing is interrupted.

        If drawing is interruped while transmiting an image, it causes terminal to
        wait for more data (while consuming any output following) until the output
        reaches the expected payload size or ST (String Terminator) is written.
        """

        # End last transmission (does no harm if there wasn't an unterminated
        # transmission)
        # Konsole sometimes requires ST to be written twice.
        print(f"{ST * 2}", end="", flush=True)

    def _render_image(
        self, img: PIL.Image.Image, alpha: Union[None, float, str]
    ) -> str:
        # Using `width=<columns>`, `height=<lines>` and `preserveAspectRatio=0` ensures
        # that an image always occupies the correct amount of columns and lines even if
        # the cell size has changed when it's drawn.
        # Since we use `width` and `height` control data keys, there's no need
        # upscaling an image on this end; ensures minimal payload.

        r_width, r_height = self.rendered_size
        width, height = self._get_minimal_render_size()

        img = self._get_render_data(img, alpha, size=(width, height), pixel_data=False)[
            0
        ]
        format = "jpeg" if img.mode == "RGB" else "png"
        if self._render_method == LINES:
            raw_image = io.BytesIO(img.tobytes())
            compressed_image = io.BytesIO()
        else:
            compressed_image = io.BytesIO()
            img.save(compressed_image, format)

        # clean up
        if img is not self._source:
            img.close()

        # Workarounds
        is_on_konsole = self._TERM == "konsole"
        jump_right = f"\033[{r_width}C"

        if self._render_method == LINES:
            # NOTE: It's more efficient to write separate strings to the buffer
            # separately than concatenate and write together.

            cell_height = height // r_height
            bytes_per_line = width * cell_height * (len(img.mode))
            control_data = (
                f";width={r_width};height=1;preserveAspectRatio=0;inline=1"
                f"{';doNotMoveCursor=1' * is_on_konsole}:"
            )

            with io.StringIO() as buffer, raw_image, compressed_image:
                for line in range(1, r_height + 1):
                    compressed_image.seek(0)
                    compressed_image.truncate()
                    with PIL.Image.frombytes(
                        img.mode, (width, cell_height), raw_image.read(bytes_per_line)
                    ) as img:
                        img.save(compressed_image, format)

                    buffer.write(f"\033]1337;File=size={compressed_image.tell()}")
                    buffer.write(control_data)
                    buffer.write(
                        standard_b64encode(compressed_image.getvalue()).decode()
                    )
                    buffer.write(ST)
                    is_on_konsole and buffer.write(jump_right)
                    line < r_height and buffer.write("\n")

                return buffer.getvalue()
        else:
            with compressed_image:
                control_data = "".join(
                    (
                        f"size={compressed_image.tell()};width={r_width}"
                        f";height={r_height};preserveAspectRatio=0;inline=1"
                        f"{';doNotMoveCursor=1' * is_on_konsole}:"
                    )
                )
                return "".join(
                    (
                        "\033]1337;File=",
                        control_data,
                        standard_b64encode(compressed_image.getvalue()).decode(),
                        ST,
                        f"{jump_right}\n" * (r_height - 1) if is_on_konsole else "",
                        jump_right * is_on_konsole,
                    )
                )


ST = "\033\\"
_stdout_write = sys.stdout.buffer.write
