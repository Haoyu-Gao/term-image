"""ITerm2Image-specific tests"""

import io
from base64 import standard_b64decode
from random import random

import pytest
from PIL import Image
from PIL.GifImagePlugin import GifImageFile
from PIL.PngImagePlugin import PngImageFile

from term_image.exceptions import ITerm2ImageError
from term_image.image.iterm2 import LINES, START, WHOLE, ITerm2Image
from term_image.utils import CSI, ST

from . import common
from .common import _size, get_actual_render_size, python_img, setup_common

ITerm2Image.READ_FROM_FILE = False

for name, obj in vars(common).items():
    if name.endswith(("_All", "_Graphics")):
        globals()[name] = obj


@pytest.mark.order("first")
def test_setup_common():
    setup_common(ITerm2Image)


def test_set_render_method():
    try:
        assert ITerm2Image._render_method == ITerm2Image._default_render_method == LINES
        image = ITerm2Image(python_img)
        assert image._render_method == ITerm2Image._default_render_method

        # Case-insensitivity
        assert ITerm2Image.set_render_method(WHOLE.upper()) is None
        assert ITerm2Image.set_render_method(WHOLE.lower()) is None
        assert ITerm2Image.set_render_method(LINES.upper()) is None
        assert ITerm2Image.set_render_method(LINES.lower()) is None

        assert ITerm2Image.set_render_method(WHOLE) is None
        assert ITerm2Image._render_method == WHOLE
        assert image._render_method == WHOLE

        assert image.set_render_method(LINES) is None
        assert image._render_method == LINES

        assert image.set_render_method() is None
        assert image._render_method == WHOLE

        assert ITerm2Image.set_render_method(LINES) is None
        assert ITerm2Image._render_method == LINES
        assert image._render_method == LINES

        assert image.set_render_method(WHOLE) is None
        assert image._render_method == WHOLE

        assert image.set_render_method() is None
        assert image._render_method == LINES

        assert ITerm2Image.set_render_method(WHOLE) is None
        assert ITerm2Image._render_method == WHOLE
        assert image._render_method == WHOLE

        assert ITerm2Image.set_render_method() is None
        assert ITerm2Image._render_method == ITerm2Image._default_render_method
        assert image._render_method == ITerm2Image._default_render_method
    finally:
        ITerm2Image._render_method = ITerm2Image._default_render_method


def test_style_format_spec():
    for spec in (
        " ",
        "x",
        "LW",
        "WN",
        "c1m0",
        "0c",
        "m2",
        "m01",
        "c-1",
        "c10",
        "c4m1",
        " c1",
        "m0 ",
        "  m1c3  ",
    ):
        with pytest.raises(ITerm2ImageError, match="format spec"):
            ITerm2Image._check_style_format_spec(spec, spec)

    for spec, args in (
        ("", {}),
        ("L", {"method": LINES}),
        ("W", {"method": WHOLE}),
        ("N", {"native": True}),
        ("m0", {}),
        ("m1", {"mix": True}),
        ("c4", {}),
        ("c0", {"compress": 0}),
        ("c9", {"compress": 9}),
        ("Wm1c9", {"method": WHOLE, "mix": True, "compress": 9}),
    ):
        assert ITerm2Image._check_style_format_spec(spec, spec) == args


class TestStyleArgs:
    def test_unknown(self):
        for args in ({"c": 1}, {"m": True}, {" ": None}, {"xxxx": True}):
            with pytest.raises(ITerm2ImageError, match="Unknown style-specific"):
                ITerm2Image._check_style_args(args)

    def test_method(self):
        for value in (None, 1.0, (), [], 2):
            with pytest.raises(TypeError):
                ITerm2Image._check_style_args({"method": value})
        for value in ("", " ", "cool"):
            with pytest.raises(ValueError):
                ITerm2Image._check_style_args({"method": value})

        for value in (LINES, WHOLE):
            assert ITerm2Image._check_style_args({"method": value}) == {"method": value}
        assert ITerm2Image._check_style_args({"native": False}) == {}
        assert ITerm2Image._check_style_args({"native": True}) == {"native": True}

    def test_mix(self):
        for value in (0, 1.0, (), [], "2"):
            with pytest.raises(TypeError):
                ITerm2Image._check_style_args({"mix": value})

        assert ITerm2Image._check_style_args({"mix": False}) == {}
        assert ITerm2Image._check_style_args({"mix": True}) == {"mix": True}

    def test_compress(self):
        for value in (1.0, (), [], "2"):
            with pytest.raises(TypeError):
                ITerm2Image._check_style_args({"compress": value})
        for value in (-1, 10):
            with pytest.raises(ValueError):
                ITerm2Image._check_style_args({"compress": value})

        assert ITerm2Image._check_style_args({"compress": 4}) == {}
        for value in range(1, 10):
            if value != 4:
                assert (
                    ITerm2Image._check_style_args({"compress": value})
                    == {"compress": value}  # fmt: skip
                )


def expand_control_data(control_data):
    control_data = control_data.split(";")
    control_codes = {tuple(code.split("=")) for code in control_data}
    assert len(control_codes) == len(control_data)

    return control_codes


def decode_image(data, term="", jpeg=False, native=False, read_from_file=False):
    fill_1, start, data = data.partition(START)
    assert start == START
    if term == "konsole":
        assert fill_1 == ""

    transmission, end, fill_2 = data.rpartition(ST)
    assert end == ST
    if term != "konsole":
        assert fill_2 == ""

    control_data, image_data = transmission.split(":", 1)
    control_codes = expand_control_data(control_data)
    assert (
        code in control_codes
        for code in expand_control_data(
            "preserveAspectRatio=0;inline=1"
            + ";doNotMoveCursor=1" * (term == "konsole")
        )
    )

    image_data = standard_b64decode(image_data.encode())
    img = Image.open(io.BytesIO(image_data))
    if native:
        assert isinstance(img, (GifImageFile, PngImageFile))
        assert img.is_animated

    if read_from_file or native:
        pass
    else:
        image_data = img.tobytes()
        if jpeg and img.mode != "RGBA":
            assert img.format == "JPEG"
            assert img.mode == "RGB"
        else:
            assert img.format == "PNG"
            assert img.mode in {"RGB", "RGBA"}

    return (
        control_codes,
        img.format,
        img.mode,
        image_data,
        fill_2 if term == "konsole" else fill_1,
    )


class TestRenderLines:
    # Fully transparent image
    # It's easy to predict it's pixel values
    trans = ITerm2Image.from_file("tests/images/trans.png")
    trans.height = _size
    trans.set_render_method(LINES)

    def render_image(self, alpha=0.0, *, N=False, m=False, c=4):
        return self.trans._renderer(
            lambda im: self.trans._render_image(im, alpha, native=N, mix=m, compress=c)
        )

    def _test_image_size(self, image, term="", read_from_file=False):
        w, h = get_actual_render_size(image)
        cols, lines = image.rendered_size
        bytes_per_line = w * (h // lines) * 4
        size_control_data = f"width={cols},height=1"
        render = str(image)

        assert render.count("\n") + 1 == lines
        for n, line in enumerate(render.splitlines(), 1):
            control_codes, format, mode, image_data, fill = decode_image(
                line, term=term, read_from_file=read_from_file
            )
            assert (
                code in control_codes for code in expand_control_data(size_control_data)
            )
            if not read_from_file:
                assert len(image_data) == bytes_per_line
            assert fill == (
                jump_right.format(cols=cols)
                if term == "konsole"
                else erase.format(cols=cols)
                if term == "wezterm"
                else ""
            )

    def test_minimal_render_size(self):
        image = ITerm2Image.from_file("tests/images/trans.png")
        image.set_render_method(LINES)
        lines_for_original_height = ITerm2Image._pixels_lines(
            pixels=image.original_size[1]
        )

        # Using render size
        image.height = lines_for_original_height // 2
        w, h = image._get_render_size()
        assert get_actual_render_size(image) == (w, h)
        for ITerm2Image._TERM in supported_terminals:
            self._test_image_size(image, term=ITerm2Image._TERM)

        # Using original size
        image.height = lines_for_original_height * 2
        w, h = image._original_size
        extra = h % (image.height or 1)
        if extra:
            h = h - extra + image.height
        assert get_actual_render_size(image) == (w, h)
        for ITerm2Image._TERM in supported_terminals:
            self._test_image_size(image, term=ITerm2Image._TERM)

    def test_size(self):
        self.trans.scale = 1.0
        for ITerm2Image._TERM in supported_terminals:
            self._test_image_size(self.trans, term=ITerm2Image._TERM)

    def test_image_data_and_transparency(self):
        ITerm2Image._TERM = ""
        self.trans.scale = 1.0
        w, h = get_actual_render_size(self.trans)
        pixels_per_line = w * (h // _size)

        # Transparency enabled
        render = self.render_image()
        assert render == str(self.trans) == f"{self.trans:1.1}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGBA"
            assert len(image_data) == pixels_per_line * 4
            assert image_data.count(b"\0" * 4) == pixels_per_line
        # Transparency disabled
        render = self.render_image(None)
        assert render == f"{self.trans:1.1#}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGB"
            assert len(image_data) == pixels_per_line * 3
            assert image_data.count(b"\0\0\0") == pixels_per_line

    def test_image_data_and_background_colour(self):
        ITerm2Image._TERM = ""
        self.trans.scale = 1.0
        w, h = get_actual_render_size(self.trans)
        pixels_per_line = w * (h // _size)

        # red
        render = self.render_image("#ff0000")
        assert render == f"{self.trans:1.1#ff0000}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGB"
            assert len(image_data) == pixels_per_line * 3
            assert image_data.count(b"\xff\0\0") == pixels_per_line
        # green
        render = self.render_image("#00ff00")
        assert render == f"{self.trans:1.1#00ff00}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGB"
            assert len(image_data) == pixels_per_line * 3
            assert image_data.count(b"\0\xff\0") == pixels_per_line
        # blue
        render = self.render_image("#0000ff")
        assert render == f"{self.trans:1.1#0000ff}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGB"
            assert len(image_data) == pixels_per_line * 3
            assert image_data.count(b"\0\0\xff") == pixels_per_line
        # white
        render = self.render_image("#ffffff")
        assert render == f"{self.trans:1.1#ffffff}"
        for line in render.splitlines():
            control_codes, format, mode, image_data, _ = decode_image(line)
            assert format == "PNG"
            assert mode == "RGB"
            assert len(image_data) == pixels_per_line * 3
            assert image_data.count(b"\xff" * 3) == pixels_per_line

    def test_mix(self):
        ITerm2Image._TERM = ""
        self.trans.scale = 1.0
        cols = self.trans.rendered_width

        for ITerm2Image._TERM in supported_terminals:
            # mix = False (default)
            render = self.render_image()
            assert render == str(self.trans) == f"{self.trans:1.1+m0}"
            for line in render.splitlines():
                assert decode_image(line, term=ITerm2Image._TERM)[-1] == (
                    jump_right.format(cols=cols)
                    if ITerm2Image._TERM == "konsole"
                    else erase.format(cols=cols)
                    if ITerm2Image._TERM == "wezterm"
                    else ""
                )

            # mix = True
            render = self.render_image(None, m=True)
            assert render == f"{self.trans:1.1#+m1}"
            for line in render.splitlines():
                assert decode_image(line, term=ITerm2Image._TERM)[-1] == (
                    jump_right.format(cols=cols)
                    if ITerm2Image._TERM == "konsole"
                    else ""
                )

    def test_compress(self):
        ITerm2Image._TERM = ""
        self.trans.scale = 1.0

        # compress = 4  (default)
        assert self.render_image() == str(self.trans) == f"{self.trans:1.1+c4}"
        # compress = 0
        assert self.render_image(None, c=0) == f"{self.trans:1.1#+c0}"
        # compress = {1-9}
        for value in range(1, 10):
            assert self.render_image(None, c=value) == f"{self.trans:1.1#+c{value}}"

        # Data size relativity
        assert (
            len(self.render_image(c=0))
            > len(self.render_image(c=1))
            > len(self.render_image(c=9))
        )

    def test_scaled(self):
        # At varying scales
        for self.trans.scale in map(lambda x: x / 100, range(10, 101, 10)):
            for ITerm2Image._TERM in supported_terminals:
                self._test_image_size(self.trans, term=ITerm2Image._TERM)

        # Random scales
        for _ in range(20):
            scale = random()
            if scale == 0.0:
                continue
            self.trans.scale = scale
            if 0 in self.trans.rendered_size:
                continue
            for ITerm2Image._TERM in supported_terminals:
                self._test_image_size(self.trans, term=ITerm2Image._TERM)


supported_terminals = {"iterm2", "wezterm", "konsole"}
erase = f"{CSI}{{cols}}X"
jump_right = f"{CSI}{{cols}}C"
fill_fmt = f"{CSI}{{cols}}X{jump_right}"
