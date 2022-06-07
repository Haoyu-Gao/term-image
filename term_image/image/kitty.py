from __future__ import annotations

__all__ = ("KittyImage",)

import io
import re
import sys
from base64 import standard_b64encode
from dataclasses import asdict, dataclass
from math import ceil
from operator import mul
from typing import Any, Dict, Generator, Optional, Set, Tuple, Union
from zlib import compress, decompress

import PIL

from ..exceptions import _style_error
from ..utils import get_cell_size, lock_tty, query_terminal
from .common import GraphicsImage

FORMAT_SPEC = re.compile(r"([^z]*)(z(-?\d+)?)?(.*)", re.ASCII)
# Constants for ``KittyImage`` render method
LINES = "lines"
WHOLE = "whole"


class KittyImage(GraphicsImage):
    """A render style using the Kitty terminal graphics protocol.

    See :py:class:`GraphicsImage` for the complete description of the constructor.

    **Render Methods**

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

         * Render results are less in number of characters compared to the
           ``lines`` method since the entire image is encoded at once.
         * Better for non-animated images that are large in resolution and pixel
           density as images are drawn once.

    The render method can be set with
    :py:meth:`set_render_method() <BaseImage.set_render_method>` using the names
    specified above.


    **Format Specification**

    ::

        [z [index] ]

    * ``z``: Image/Text stacking order.

      * ``index``: Image z-index. An integer in the **signed 32-bit range**.

        Images drawn in the same location with different z-index values will be
        blended if they are semi-transparent. If ``index`` is:

        * ``>= 0``, the image will be drawn above text.
        * ``< 0``, the image will be drawn below text.
        * ``< -(2 ** 31) / 2``, the image will be drawn below non-default text
          background colors.

      * ``z`` without ``index`` is currently only used internally.

    ATTENTION:
        Currently supported terminal emulators include:

          * `Kitty <https://sw.kovidgoyal.net/kitty/>`_ >= 0.20.0.
          * `Konsole <https://konsole.kde.org>`_ >= 22.04.0.
    """

    _render_methods: Set[str] = {LINES, WHOLE}
    _default_render_method: str = LINES
    _render_method: str = LINES
    _style_args = {
        "z_index": (
            (
                lambda x: x is None or isinstance(x, int),
                "z-index must be `None` or an integer",
            ),
            (
                lambda x: x is None or -(2**31) <= x < 2**31,
                "z-index must be within the 32-bit signed integer range",
            ),
        )
    }

    _KITTY_VERSION: Tuple[int, int, int] = ()
    _KONSOLE_VERSION: Tuple[int, int, int] = ()

    # Only defined for the purpose of proper self-documentation
    def draw(self, *args, z_index: Optional[int] = 0, **kwargs) -> None:
        """Draws an image to standard output.

        Extends the common interface with style-specific parameters.

        Args:
            args: Positional arguments passed up the inheritance chain.
            z_index: The stacking order of images and text **for non-animations**.

              Images drawn in the same location with different z-index values will be
              blended if they are semi-transparent. If *z_index* is:

              * ``>= 0``, the image will be drawn above text.
              * ``< 0``, the image will be drawn below text.
              * ``< -(2 ** 31) / 2``, the image will be drawn below non-default text
                background colors.
              * ``None``, deletes any directly overlapping image.

              .. note::
                Currently, ``None`` is **only used internally** as it's buggy on
                Kitty <= 0.25.0. It's only mentioned here for the sake of completeness.

                Also, inter-mixing text with an image requires writing the text after
                drawing the image, as any text within the region covered by the image is
                overwritten when the image is drawn.

            kwargs: Keyword arguments passed up the inheritance chain.

        See the ``draw()`` method of the parent classes for full details, including the
        description of other parameters.
        """
        arguments = locals()
        super().draw(
            *args,
            **kwargs,
            **{
                var: arguments[var]
                for var, default in __class__.draw.__kwdefaults__.items()
                if arguments[var] != default
            },
        )

    @classmethod
    @lock_tty
    def is_supported(cls):
        if cls._supported is None:
            # Kitty graphics query + terminal attribute query
            # The second query is to speed up the query since most (if not all)
            # terminals should support it and most terminals treat queries as FIFO
            response = query_terminal(
                (
                    f"{_START}a=q,t=d,i=31,f=24,s=1,v=1,C=1,c=1,r=1;AAAA{_END}\033[c"
                ).encode(),
                lambda s: not s.endswith(b"c"),
            )
            # Not supported if it doesn't respond to either query
            # or responds to the second but not the first
            cls._supported = response and (
                response.rpartition(b"\033")[0] == f"{_START}i=31;OK{_END}".encode()
            )

            # Currently, only kitty >= 0.20.0 and Konsole 22.04.0 implement the
            # protocol features utilized
            if cls._supported:
                response = query_terminal(
                    b"\033[>q", lambda s: not s.endswith(b"\033\\")
                ).decode()
                match = re.match(
                    r"\033P>\|(\w+)[( ]?([^)\033]+)\)?\033\\", response, re.ASCII
                )
                if match:
                    name, version = match.groups()
                    if name.casefold() == "kitty":
                        cls._KITTY_VERSION = tuple(map(int, version.split(".")))
                    elif name.casefold() == "konsole":
                        cls._KONSOLE_VERSION = tuple(map(int, version.split(".")))

                # fmt: off
                cls._supported = (
                    cls._KITTY_VERSION >= (0, 20, 0)
                    or cls._KONSOLE_VERSION >= (22, 4, 0)
                )
                # fmt: on

        return cls._supported

    @classmethod
    def _check_style_format_spec(cls, spec: str, original: str) -> Dict[str, Any]:
        parent, z, z_index, invalid = FORMAT_SPEC.fullmatch(spec).groups()
        if invalid:
            raise _style_error(cls)(
                f"Invalid style-specific format specification {original!r}"
            )

        args = {}
        if parent:
            args.update(super()._check_style_format_spec(parent, original))
        if z:
            args["z_index"] = z_index and int(z_index)

        return cls._check_style_args(args)

    @staticmethod
    def _clear_images():
        _stdout_write(b"\033_Ga=d;\033\\")
        return True

    @classmethod
    def _clear_frame(cls):
        if cls._KITTY_VERSION and cls._KITTY_VERSION <= (0, 25, 0):
            cls._clear_images()
            return True
        return False

    def _display_animated(self, *args, **kwargs) -> None:
        if self._KITTY_VERSION > (0, 25, 0):
            kwargs["z_index"] = None
        else:
            try:
                del kwargs["z_index"]
            except KeyError:
                pass

        super()._display_animated(*args, **kwargs)

    def _get_render_size(self) -> Tuple[int, int]:
        return tuple(map(mul, self.rendered_size, get_cell_size() or (1, 2)))

    @staticmethod
    def _pixels_cols(
        *, pixels: Optional[int] = None, cols: Optional[int] = None
    ) -> int:
        return (
            ceil(pixels // (get_cell_size() or (1, 2))[0])
            if pixels is not None
            else cols * (get_cell_size() or (1, 2))[0]
        )

    @staticmethod
    def _pixels_lines(
        *, pixels: Optional[int] = None, lines: Optional[int] = None
    ) -> int:
        return (
            ceil(pixels // (get_cell_size() or (1, 2))[1])
            if pixels is not None
            else lines * (get_cell_size() or (1, 2))[1]
        )

    def _render_image(
        self,
        img: PIL.Image.Image,
        alpha: Union[None, float, str],
        z_index: Optional[int] = 0,
    ) -> str:
        # Using `c` and `r` ensures that an image always occupies the correct amount
        # of columns and lines even if the cell size has changed when it's drawn.
        # Since we use `c` and `r` control data keys, there's no need upscaling the
        # image on this end; ensures minimal payload.

        render_size = self._get_render_size()
        r_width, r_height = self.rendered_size
        width, height = (
            render_size
            if mul(*render_size) < mul(*self._original_size)
            else self._original_size
        )

        # When `_original_size` is used, ensure the height is a multiple of the rendered
        # height, so that pixels can be evenly distributed among all lines.
        # If r_height == 0, height == 0, extra == 0; Handled in `_get_render_data()`.
        extra = height % (r_height or 1)
        if extra:
            # Incremented to the greater multiple to avoid losing any data
            height = height - extra + r_height

        img = self._get_render_data(img, alpha, size=(width, height))[0]
        format = getattr(f, img.mode)
        raw_image = img.tobytes()

        # clean up
        if img is not self._source:
            img.close()

        return getattr(self, f"_render_image_{self._render_method}")(
            raw_image,
            ControlData(f=format, s=width, c=r_width, z=z_index),
            height,
            r_height,
        )

    @staticmethod
    def _render_image_lines(
        raw_image: bytes,
        control_data: ControlData,
        height: int,
        r_height: int,
    ) -> str:
        # NOTE:
        # It's more efficient to write separate strings to the buffer separately
        # than concatenate and write together.

        cell_height = height // r_height
        bytes_per_line = control_data.s * cell_height * (control_data.f // 8)
        vars(control_data).update(dict(v=cell_height, r=1))
        fill = " " * control_data.c
        if control_data.z is None:
            delete = f"{_START}a=d,d=c;{_END}"
            clear = f"{delete}\0337\033[{control_data.c}C{delete}\0338"

        with io.StringIO() as buffer, io.BytesIO(raw_image) as raw_image:
            trans = Transmission(control_data, raw_image.read(bytes_per_line))
            control_data.z is None and buffer.write(clear)
            buffer.write(trans.get_chunked())
            # Writing spaces clears any text under transparent areas of an image
            for _ in range(r_height - 1):
                buffer.write(fill)
                buffer.write("\n")
                trans = Transmission(control_data, raw_image.read(bytes_per_line))
                control_data.z is None and buffer.write(clear)
                buffer.write(trans.get_chunked())
            buffer.write(fill)

            return buffer.getvalue()

    @staticmethod
    def _render_image_whole(
        raw_image: bytes,
        control_data: ControlData,
        height: int,
        r_height: int,
    ) -> str:
        vars(control_data).update(dict(v=height, r=r_height))
        fill = " " * control_data.c
        if control_data.z is None:
            delete = f"{_START}a=d,d=c;{_END}"
            clear = f"{delete}\0337\033[{control_data.c}C{delete}\0338"
        return "".join(
            (
                control_data.z is None and clear or "",
                Transmission(control_data, raw_image).get_chunked(),
                (fill + "\n") * (r_height - 1),
                fill,
            )
        )


@dataclass
class Transmission:
    """An abstraction of the kitty terminal graphics escape code.

    Args:
        control: The control data.
        payload: The payload.
    """

    control: ControlData
    payload: bytes

    def __post_init__(self):
        self._compressed = False
        if self.control.o == o.ZLIB:
            self.compress()

    def compress(self):
        if self.control.t == t.DIRECT and not self._compressed:
            self.payload = compress(self.payload)
            self.control.o = o.ZLIB
            self._compressed = True

    def decompress(self):
        if self.control.t == t.DIRECT and self._compressed:
            self.control.o = None
            self.payload = decompress(self.payload)
            self._compressed = False

    def encode(self) -> bytes:
        return standard_b64encode(self.payload)

    def get_chunked(self) -> str:
        return "".join(self.get_chunks())

    def get_chunks(self, size: int = 4096) -> Generator[str, None, None]:
        payload = self.get_payload()

        chunk, next_chunk = payload.read(size), payload.read(size)
        yield f"\033_G{self.get_control_data()},m={bool(next_chunk):d};{chunk}\033\\"

        chunk, next_chunk = next_chunk, payload.read(size)
        while next_chunk:
            yield f"\033_Gm=1;{chunk}\033\\"
            chunk, next_chunk = next_chunk, payload.read(size)

        if chunk:  # false if there was never a next chunk
            yield f"\033_Gm=0;{chunk}\033\\"

    def get_control_data(self) -> str:
        return ",".join(
            f"{key}={value}"
            for key, value in asdict(self.control).items()
            if value is not None
        )

    def get_payload(self) -> io.StringIO:
        return io.StringIO(self.encode().decode("ascii"))


# Values for control data keys with limited set of values


class a:
    TRANS = "t"
    TRANS_DISP = "T"
    QUERY = "q"
    PLACE = "p"
    DELETE = "d"
    TRANS_FRAMES = "f"
    CONTROL_ANIM = "a"
    COMPOSE_FRAMES = "c"


class C:
    MOVE = 0
    STAY = 1


class f:
    RGB = 24
    RGBA = 32
    PNG = 100


class o:
    ZLIB = "z"


class t:
    DIRECT = "d"
    FILE = "f"
    TEMP = "t"
    SHARED = "s"


class z:
    BEHIND = -1
    IN_FRONT = 0


@dataclass
class ControlData:
    """Represents a portion of the kitty terminal graphics protocol control data"""

    a: Optional[str] = a.TRANS_DISP  # action
    f: Optional[int] = f.RGBA  # data format
    t: Optional[str] = t.DIRECT  # transmission medium
    s: Optional[int] = None  # image width
    v: Optional[int] = None  # image height
    z: Optional[int] = z.IN_FRONT  # z-index
    o: Optional[str] = o.ZLIB  # compression
    C: Optional[int] = C.STAY  # cursor movement policy

    # # Image display size in columns and rows/lines
    # # The image is shrunk or enlarged to fit
    c: Optional[int] = None  # columns
    r: Optional[int] = None  # rows

    def __post_init__(self):
        if self.f == f.PNG:
            self.s = self.v = None


class _ControlData:  # Currently Unused

    i: Optional[int] = None  # image ID
    d: Optional[str] = None  # delete images
    m: Optional[int] = None  # payload chunk
    O: Optional[int] = None  # data start offset; with t=s or t=f
    S: Optional[int] = None  # data size in bytes; with f=100,o=z or t=s or t=f

    # Origin offset (px) within the current cell; Must be less than the cell size
    # (0, 0) == Top-left corner of the cell; Not used with `c` and `r`.
    X: Optional[int] = None
    Y: Optional[int] = None

    # Image crop (px)
    # # crop origin; (0, 0) == top-left corner of the image
    x: Optional[int] = None
    y: Optional[int] = None
    # # crop rectangle size
    w: Optional[int] = None
    h: Optional[int] = None


_START = "\033_G"
_END = "\033\\"
_FMT = f"{_START}%(control)s;%(payload)s{_END}"
_stdout_write = sys.stdout.buffer.write
