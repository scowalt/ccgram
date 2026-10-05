import asyncio
import io
import structlog
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_RE_ANSI_SGR = re.compile(r"\x1b\[([0-9;]*)m")

_RE_NON_SGR = re.compile(
    r"\x1b\](?:[^\x07\x1b]|\x1b(?!\\))*(?:\x07|\x1b\\)"
    r"|\x1b\[[\x30-\x3f]*[\x20-\x2f]*[\x40-\x6c\x6e-\x7e]"
    r"|\x1b[^\[\]]"
)

logger = structlog.get_logger()

_FONTS_DIR = Path(__file__).parent / "fonts"

_FONT_PATHS: list[Path] = [
    _FONTS_DIR / "JetBrainsMono-Regular.ttf",
    _FONTS_DIR / "NotoSansMonoCJKsc-Regular.otf",
    _FONTS_DIR / "Symbola.ttf",
]

_NOTO_CODEPOINTS: set[int] = {
    0x23BF,
}
_SYMBOLA_CODEPOINTS: set[int] = {
    0x23EA,
    0x23F5,
    0x2714,
    0x274C,
    0x1F3B2,
    0x1F4A5,
    0x1F7E1,
}

_ANSI_COLORS: dict[int, tuple[int, int, int]] = {
    0: (0, 0, 0),
    1: (205, 49, 49),
    2: (13, 188, 121),
    3: (229, 229, 16),
    4: (36, 114, 200),
    5: (188, 63, 188),
    6: (17, 168, 205),
    7: (229, 229, 229),
    8: (102, 102, 102),
    9: (241, 76, 76),
    10: (35, 209, 139),
    11: (245, 245, 67),
    12: (59, 142, 234),
    13: (214, 112, 214),
    14: (41, 184, 219),
    15: (255, 255, 255),
}

_DEFAULT_FG = (212, 212, 212)
_DEFAULT_BG = (30, 30, 30)


@dataclass
class TextStyle:
    fg_color: tuple[int, int, int] = _DEFAULT_FG
    bg_color: tuple[int, int, int] | None = None


@dataclass
class StyledSegment:
    text: str
    style: TextStyle
    font_tier: int


_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (str(path), size)
    if key not in _font_cache:
        try:
            _font_cache[key] = ImageFont.truetype(str(path), size)
        except OSError:
            logger.warning("Failed to load font %s, using Pillow default", path)
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _font_tier(ch: str) -> int:
    cp = ord(ch)
    if cp in _SYMBOLA_CODEPOINTS:
        return 2
    if (
        cp in _NOTO_CODEPOINTS
        or cp >= 0x2E80
        and (
            cp <= 0x9FFF
            or 0xF900 <= cp <= 0xFAFF
            or 0xFE30 <= cp <= 0xFE4F
            or 0xFF00 <= cp <= 0xFFEF
            or 0x20000 <= cp <= 0x2FA1F
        )
    ):
        return 1
    return 0


def _parse_ansi_line(line: str) -> list[StyledSegment]:
    segments: list[StyledSegment] = []
    current_style = TextStyle()
    pos = 0

    for match in _RE_ANSI_SGR.finditer(line):
        text_before = line[pos : match.start()]
        if text_before:
            for seg_text, tier in _split_line_segments_plain(text_before):
                if seg_text:
                    segments.append(StyledSegment(seg_text, current_style, tier))

        codes = match.group(1)
        if codes:
            current_style = _apply_ansi_codes(current_style, codes)
        else:
            current_style = TextStyle()

        pos = match.end()

    text_after = line[pos:]
    if text_after:
        for seg_text, tier in _split_line_segments_plain(text_after):
            if seg_text:
                segments.append(StyledSegment(seg_text, current_style, tier))

    return segments if segments else [StyledSegment("", TextStyle(), 0)]


def _apply_ansi_codes(style: TextStyle, codes: str) -> TextStyle:
    new_style = TextStyle(
        fg_color=style.fg_color,
        bg_color=style.bg_color,
    )

    parts = [int(c) for c in codes.split(";") if c]
    i = 0
    while i < len(parts):
        code = parts[i]

        if code == 0:
            new_style = TextStyle()
        elif 30 <= code <= 37:
            new_style.fg_color = _ANSI_COLORS[code - 30]
        elif code == 38:
            if i + 1 < len(parts) and parts[i + 1] == 5:
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.fg_color = _ANSI_COLORS[color_idx]
                    else:
                        new_style.fg_color = _approximate_256_color(color_idx)
                    i += 2
            elif i + 1 < len(parts) and parts[i + 1] == 2 and i + 4 < len(parts):
                new_style.fg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 39:
            new_style.fg_color = _DEFAULT_FG
        elif 40 <= code <= 47:
            new_style.bg_color = _ANSI_COLORS[code - 40]
        elif code == 48:
            if i + 1 < len(parts) and parts[i + 1] == 5:
                if i + 2 < len(parts):
                    color_idx = parts[i + 2] % 256
                    if color_idx < 16:
                        new_style.bg_color = _ANSI_COLORS[color_idx]
                    else:
                        new_style.bg_color = _approximate_256_color(color_idx)
                    i += 2
            elif i + 1 < len(parts) and parts[i + 1] == 2 and i + 4 < len(parts):
                new_style.bg_color = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 49:
            new_style.bg_color = None
        elif 90 <= code <= 97:
            new_style.fg_color = _ANSI_COLORS[code - 90 + 8]
        elif 100 <= code <= 107:
            new_style.bg_color = _ANSI_COLORS[code - 100 + 8]

        i += 1

    return new_style


def _approximate_256_color(idx: int) -> tuple[int, int, int]:
    if idx < 16:
        return _ANSI_COLORS[idx]
    elif idx < 232:
        idx -= 16
        r = (idx // 36) * 51
        g = ((idx % 36) // 6) * 51
        b = (idx % 6) * 51
        return (r, g, b)
    else:
        gray = 8 + (idx - 232) * 10
        return (gray, gray, gray)


def _split_line_segments_plain(line: str) -> list[tuple[str, int]]:
    if not line:
        return [("", 0)]
    segments: list[tuple[str, int]] = []
    cur_tier = _font_tier(line[0])
    start = 0
    for i in range(1, len(line)):
        tier = _font_tier(line[i])
        if tier != cur_tier:
            segments.append((line[start:i], cur_tier))
            cur_tier = tier
            start = i
    segments.append((line[start:], cur_tier))
    return segments


def strip_non_sgr(text: str) -> str:
    return _RE_NON_SGR.sub("", text)


async def text_to_image(
    text: str,
    font_size: int = 28,
    with_ansi: bool = True,
    *,
    live_mode: bool = False,
) -> bytes:
    effective_font_size = 20 if live_mode else font_size

    def _render_image() -> bytes:
        fonts = [_load_font(p, effective_font_size) for p in _FONT_PATHS]

        cleaned = strip_non_sgr(text)
        lines = cleaned.split("\n")
        padding = 16

        if with_ansi:
            line_segments = [_parse_ansi_line(line) for line in lines]
        else:
            line_segments_plain = [_split_line_segments_plain(line) for line in lines]
            line_segments = [
                [
                    StyledSegment(seg_text, TextStyle(), tier)
                    for seg_text, tier in segments
                ]
                for segments in line_segments_plain
            ]

        dummy = Image.new("RGB", (1, 1))
        draw = ImageDraw.Draw(dummy)
        line_height = int(effective_font_size * 1.4)
        max_width = 0
        for segments in line_segments:
            w = 0
            for seg in segments:
                bbox = draw.textbbox((0, 0), seg.text, font=fonts[seg.font_tier])
                w += bbox[2] - bbox[0]
            max_width = max(max_width, w)

        img_width = max(int(max_width) + padding * 2, 200)
        img_height = max(
            line_height * len(lines) + padding * 2, line_height + padding * 2
        )

        img = Image.new("RGB", (img_width, img_height), _DEFAULT_BG)
        draw = ImageDraw.Draw(img)

        y = padding
        for segments in line_segments:
            x = padding
            for seg in segments:
                f = fonts[seg.font_tier]

                if seg.style.bg_color:
                    bbox = draw.textbbox((x, y), seg.text, font=f)
                    draw.rectangle(
                        [bbox[0], y, bbox[2], y + line_height], fill=seg.style.bg_color
                    )

                draw.text((x, y), seg.text, fill=seg.style.fg_color, font=f)

                bbox = draw.textbbox((0, 0), seg.text, font=f)
                x += bbox[2] - bbox[0]
            y += line_height

        buf = io.BytesIO()
        if live_mode:
            img = img.quantize(colors=32)
            img.save(buf, format="PNG", optimize=True, compress_level=9)
        else:
            img.save(buf, format="PNG")
        return buf.getvalue()

    return await asyncio.to_thread(_render_image)
