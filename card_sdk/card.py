import asyncio
import base64
from datetime import datetime
from io import BytesIO
import math
import pathlib
import os
import re
import aiofiles
import aiohttp
from color_cli import color_text
from requests_cache import Optional
from card_sdk._svg2pdf import svg2pdf_py
import requests
from pydantic import BaseModel
import json
import pymupdf
from cool_qrcode import make_cool_qrcode
from PIL import Image
from card_sdk.base64qrcode import base64_qrcode
from jload import jsave
import ui
from super_progress_bar import Progress
from aiocache import cached
from .cardgen.cardgen import card

progress = Progress(
    min_value=0,
    max_value=100,
    unit="items",
    colors=[(0, 0, 0), (255, 255, 0), (255, 255, 0), (0, 0, 0)],
    single_color=False,
)


base_dir = pathlib.Path(__file__).parent

# Final card PDFs are written OUTSIDE the installed package. Default = the
# folder the developer is running the SDK from (./output). Override anytime
# with set_output_dir() or the KAA_OUTPUT_DIR environment variable.
_output_dir = pathlib.Path(os.environ.get("KAA_OUTPUT_DIR", pathlib.Path.cwd() / "output"))


def get_output_dir() -> pathlib.Path:
    """Resolve the configured card output directory (absolute path)."""
    return _output_dir.expanduser().resolve()


def set_output_dir(path) -> None:
    """Set where final card PDFs are written.

    Accepts a ``str``/``pathlib.Path`` (relative paths resolve against the
    current working directory) or ``None`` to reset to the default ``./output``.
    """
    global _output_dir
    _output_dir = (
        pathlib.Path.cwd() / "output" if path is None else pathlib.Path(path)
    )


# CMYK printing: when enabled, every final card PDF is rewritten with 4-channel
# CMY+K colors so printers output the card design as-is instead of letting the
# printer driver guess an RGB→CMYK conversion. Enable with set_print_cmyk(True)
# or the KAA_PRINT_CMYK environment variable.
_print_cmyk = os.environ.get("KAA_PRINT_CMYK", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def set_print_cmyk(enable: bool) -> None:
    """Enable/disable CMYK print output (True = printer-compatible colors)."""
    global _print_cmyk
    _print_cmyk = bool(enable)


def get_print_cmyk() -> bool:
    """Whether final card PDFs are converted to CMYK for physical printing."""
    return _print_cmyk


def convert_pdf_to_cmyk(pdf_path, dpi: int = 300) -> bool:
    """Rewrite a card PDF with CMY+K (4-channel) colors — for physical printing.

    Each page is rasterized at ``dpi`` (default 300, override with the
    ``KAA_PRINT_CMYK_DPI`` env var) into an ICC-based/DeviceCMYK image and
    placed on a clean CMYK page, so print shops get true subtractive colors
    instead of an RGB→CMYK conversion in the printer driver. The original file
    is replaced in place. Returns True on success (False if it could not open
    or convert the file).
    """
    target = pathlib.Path(pdf_path)
    if not target.is_file():
        return False
    if dpi is None:
        dpi = int(os.environ.get("KAA_PRINT_CMYK_DPI", "300"))
    src = pymupdf.open(pdf_path)
    tmp = target.with_name(f"{target.stem}.cmyk{target.suffix}.tmp")
    out = pymupdf.open()
    try:
        matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
        for page in src:
            pix = page.get_pixmap(
                matrix=matrix, colorspace=pymupdf.csCMYK, alpha=False
            )
            # Encode the CMYK raster as a 4:4:4 JPEG (no chroma subsampling) so
            # small text / barcodes / QR codes stay crisp, then embed it on a
            # clean CMYK page — keeps the file small and the colors print-true.
            jpeg_buffer = BytesIO()
            Image.frombytes("CMYK", (pix.width, pix.height), pix.samples).save(
                jpeg_buffer, "JPEG", quality=90, subsampling=0
            )
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=jpeg_buffer.getvalue())
        out.save(str(tmp))
        out.close()
        src.close()
        os.replace(tmp, target)
        return True
    except Exception as error:
        try:
            out.close()
        except Exception:
            pass
        src.close()
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        print(f":: CMYK conversion failed for {pdf_path}: {error}")
        return False


# Load fonts
svg = svg2pdf_py.FontDatabase()
svg.load_font_file(f"{base_dir}/fonts/minigap.otf")
svg.load_font_file(f"{base_dir}/fonts/FHLecturis-Bold.ttf")
# Gilroy fonts used by BLUE_TOPBAR_CARD template — add font files to card_sdk/fonts/
# NOTE: svg2pdf_py resolves font-family by a font's *internal* family name, not the
# file name. Known internal names: "Minigap" (minigap.otf), "FH Lecturis"
# (FHLecturis-Bold.ttf), "Gilroy" (Gilroy-*.ttf).
_loaded_font_names = {"Minigap", "FH Lecturis"}
for _gilroy in ("Gilroy-UltraBold.ttf", "Gilroy-Light.ttf"):
    try:
        svg.load_font_file(f"{base_dir}/fonts/{_gilroy}")
        _loaded_font_names.add("Gilroy")
    except Exception:
        pass
# Credit Card font used by BANK_INSPIRE for card numbers / student IDs
for _cc in ("CreditCard-26Me.ttf",):
    try:
        svg.load_font_file(f"{base_dir}/fonts/{_cc}")
        _loaded_font_names.add("Credit Card")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Gradient-on-text flattening.
#
# svg2pdf_py ignores *any* `url(...)` fill on a <text> element (the glyphs come
# out invisible), and it maps gradient fills through the wrong coordinate space
# when the painted shape carries a transform attribute. The one combination it
# handles faithfully is an *untransformed* path whose outline is already in
# absolute user-space coordinates, filled with a userSpaceOnUse gradient.
#
# So for every <text> whose effective fill is a linear-gradient `url(#...)` we
# convert the glyph outlines to an absolute-coordinate <path> (using the same
# font files svg2pdf is loaded with) and paint it with a freshly materialised
# userSpaceOnUse copy of that gradient. The design file is left untouched; the
# original <text> (invisible to svg2pdf anyway) is removed.
# ---------------------------------------------------------------------------

_XF_M = re.compile(r"([A-Za-z]+)\(([^)]*)\)")


def _xf_mul(m1, m2):
    """Compose transforms: apply m2 to a point first, then m1."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _xf_parse(transform: str) -> tuple:
    if not transform:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    total = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in _XF_M.findall(transform):
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(nums) >= 6:
            m = (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5])
        elif name == "translate" and nums:
            tx = nums[0]
            ty = nums[1] if len(nums) > 1 else 0.0
            m = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale" and nums:
            sx = nums[0]
            sy = nums[1] if len(nums) > 1 else sx
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and nums:
            ang = math.radians(nums[0])
            cx, cy = (nums[1], nums[2]) if len(nums) > 2 else (0.0, 0.0)
            r = (math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0.0, 0.0)
            m = _xf_mul(_xf_parse(f"translate({cx} {cy})"), _xf_mul(r, _xf_parse(f"translate({-cx} {-cy})")))
        elif name == "skewX" and nums:
            m = (1.0, 0.0, math.tan(math.radians(nums[0])), 1.0, 0.0, 0.0)
        elif name == "skewY" and nums:
            m = (1.0, math.tan(math.radians(nums[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        total = _xf_mul(total, m)
    return total


_CMD_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-\d.eE]+")


def _path_affine(d: str, m) -> str:
    """Rewrite a path's absolute coordinates through the affine m."""
    a, b, c, dd, e, f = m
    tokens = _CMD_RE.findall(d)
    out, i = [], 0
    cur_x = cur_y = 0.0
    start_x = start_y = 0.0
    while i < len(tokens):
        tok = tokens[i]
        if tok[0].isalpha():
            cmd, i = tok, i + 1
        else:
            err = "number without a command in path"
            raise ValueError(err)
        if cmd == "Z" or cmd == "z":
            out.append("Z")
            cur_x, cur_y = start_x, start_y
            continue
        nums = []
        while i < len(tokens) and tokens[i][0] not in "MmLlHhVvCcSsQqTtAaZz":
            nums.append(float(tokens[i]))
            i += 1
        if cmd in ("M", "L"):
            for j in range(0, len(nums), 2):
                x, y = nums[j], nums[j + 1]
                nx, ny = a * x + c * y + e, b * x + dd * y + f
                out.append(("M" if (cmd == "M" and j == 0) else "L") + f" {nx:g} {ny:g}")
                cur_x, cur_y = nx, ny
                if j == 0 and cmd == "M":
                    start_x, start_y = nx, ny
        elif cmd == "m":
            continue
        elif cmd == "H":
            x = nums[0]
            nx = a * x + c * cur_y + e
            out.append("L" + f" {nx:g} {cur_y:g}")
            cur_x = nx
        elif cmd == "h":
            continue
        elif cmd == "V":
            y = nums[0]
            ny = b * cur_x + dd * y + f
            out.append("L" + f" {cur_x:g} {ny:g}")
            cur_y = ny
        elif cmd == "v":
            continue
        elif cmd == "C":
            for j in range(0, len(nums), 6):
                seg = []
                for k in range(6):
                    seg.append(nums[j + k])
                x1, y1, x2, y2, x, y = seg
                out.append(
                    "C"
                    + f" {a * x1 + c * y1 + e:g} {b * x1 + dd * y1 + f:g}"
                    + f" {a * x2 + c * y2 + e:g} {b * x2 + dd * y2 + f:g}"
                    + f" {a * x + c * y + e:g} {b * x + dd * y + f:g}"
                )
                cur_x, cur_y = a * x + c * y + e, b * x + dd * y + f
        elif cmd == "Q":
            for j in range(0, len(nums), 4):
                qx, qy, x, y = nums[j], nums[j + 1], nums[j + 2], nums[j + 3]
                out.append(
                    "Q"
                    + f" {a * qx + c * qy + e:g} {b * qx + dd * qy + f:g}"
                    + f" {a * x + c * y + e:g} {b * x + dd * y + f:g}"
                )
                cur_x, cur_y = a * x + c * y + e, b * x + dd * y + f
        else:
            # Unsupported letters (A/S/T, any relative commands) — bail out.
            raise ValueError(f"unsupported path command {cmd}")
    return "".join(out)


_FONT_DIR = base_dir / "fonts"


def _pick_font_file(family: str, weight: int) -> str | None:
    low = family.lower()
    if low == "minigap":
        cand = _FONT_DIR / "minigap.otf"
    elif low == "fh lecturis":
        cand = _FONT_DIR / "FHLecturis-Bold.ttf"
    elif low == "credit card":
        cand = _FONT_DIR / "CreditCard-26Me.ttf"
    elif low == "gilroy":
        cand = _FONT_DIR / ("Gilroy-UltraBold.ttf" if weight >= 700 else "Gilroy-Light.ttf")
    else:
        cand = None
    if cand is not None and cand.is_file():
        return str(cand)
    return None


_font_tool_cache: dict = {}


def _gradient_flatten(svg_text: str) -> str:
    """See module docstring above. Returns the (possibly unchanged) SVG text."""
    if "fill:url(#" not in svg_text and 'fill="url(#' not in svg_text:
        return svg_text
    try:
        import math  # noqa: F401  (used by _xf_parse via global math)
    except Exception:
        pass
    try:
        import xml.etree.ElementTree as ET
        from fontTools.pens.svgPathPen import SVGPathPen
    except Exception:
        return svg_text
    try:
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.ttLib import TTFont
    except Exception:
        return svg_text

    svg_ns = "http://www.w3.org/2000/svg"
    xlink_ns = "http://www.w3.org/1999/xlink"

    for prefix, uri in re.findall(r'xmlns:(\w+)="([^"]+)"', svg_text):
        try:
            ET.register_namespace(prefix, uri)
        except Exception:
            pass
    ET.register_namespace("", svg_ns)

    def _local(tag):
        return tag.split("}", 1)[-1] if isinstance(tag, str) and "}" in tag else tag

    def _style_match(el, prop):
        style = el.get("style") or ""
        m = re.search(rf"{prop}\s*:\s*((?:[^;'\"()]|\([^)]*\))*)", style)
        return m.group(1).strip() if m else None

    def _fill_value(el):
        # nearest fill (style or attribute), walking ancestors since fill inherits
        node = el
        while node is not None:
            v = _style_match(node, "fill") or (node.get("fill") or "").strip()
            if v:
                return v
            node = _parents.get(node)
        return None

    def _font_style(el):
        fam = wgt = size = None
        node = el
        while node is not None:
            if fam is None:
                m = re.search(r"font-family\s*:\s*([^;'\"\s]+)", node.get("style") or "")
                if m:
                    fam = m.group(1)
            if wgt is None:
                m = re.search(r"font-weight\s*:\s*(\d+)", node.get("style") or "")
                if m:
                    wgt = int(m.group(1))
            if size is None:
                m = re.search(r"font-size\s*:\s*([\d.]+)px", node.get("style") or "")
                if m:
                    size = float(m.group(1))
            if fam and wgt is not None and size is not None:
                break
            node = _parents.get(node)
        return fam or "Minigap", wgt if wgt is not None else 400, size if size is not None else 6.0

    def _resolve_gradient(gid, gradients):
        seen = set()
        stops = []
        units = None
        axis = None
        any_lin = None
        while gid and gid not in seen:
            seen.add(gid)
            g = gradients.get(gid)
            if g is None:
                break
            if g["stops"]:
                stops = g["stops"]
            if g["linear"] is not None:
                any_lin = g["linear"]
                if units is None:
                    units = g["linear"].get("gradientUnits")
                if axis is None and g["linear"].get("x1") is not None:
                    axis = g["linear"]
            gid = g["href"].lstrip("#") if g["href"] else None
        return {
            "stops": stops,
            "units": units or "objectBoundingBox",
            "axis": axis if axis is not None else any_lin,
        }

    def _run_path(gs, font, run_text, cx, cy, xf, size):
        upm = font["head"].unitsPerEm
        cmap = font.getBestCmap()
        dd = ""
        s = size / upm
        for ch in run_text:
            if ord(ch) not in cmap:
                continue
            gn = cmap[ord(ch)]
            pen = SVGPathPen(gs)
            gs[gn].draw(pen)
            # F = xf · translate(cx,cy) · scale(s,-s) applied to font-unit outlines
            F = _xf_mul(xf, (s, 0.0, 0.0, -s, cx, cy))
            dd += _path_affine(pen.getCommands(), F)
            adv = font["hmtx"][gn][0]
            cx += adv * s
        return dd

    try:
        root = ET.fromstring(svg_text)
    except Exception:
        return svg_text

    # parent map + gradient definitions
    _parents = {}
    for p in root.iter():
        for c in p:
            _parents[c] = p

    gradients = {}
    for g in root.iter():
        if _local(g.tag) not in ("linearGradient", "radialGradient"):
            continue
        gid = g.get("id")
        if not gid:
            continue
        href = g.get(f"{{{xlink_ns}}}href") or g.get("xlink:href")
        stops = []
        for st in g:
            if _local(st.tag) != "stop":
                continue
            style = st.get("style") or ""
            color = re.search(r"(?:stop-)?color\s*:\s*([^;\"')\s]+)", style)
            if not color and st.get("stop-color"):
                color = re.search(r"[^#\s]+", st.get("stop-color"))
            cval = color.group(1) if color else "#000000"
            off = st.get("offset")
            if off is None:
                cm = re.search(r"offset\s*:\s*([\d.%]+)", style)
                off = cm.group(1) if cm else None
            stops.append((off, cval))
        gradients[gid] = {
            "href": href,
            "stops": stops,
            "linear": g if _local(g.tag) == "linearGradient" else None,
        }

    used_ids = {g.get("id") for g in root.iter() if g.get("id")}
    counter = [0]
    painted = False

    for text_el in list(root.iter()):
        if _local(text_el.tag) != "text":
            continue
        fill = _fill_value(text_el)
        gm = re.search(r"url\(\s*#([^)\s]+)\s*\)", fill) if fill else None
        if not gm:
            continue
        base = _resolve_gradient(gm.group(1), gradients)
        base_lin = base["axis"]
        if base_lin is None or not base["stops"]:
            continue  # leave for the solid-fill fallback pass
        if base["units"] == "objectBoundingBox":
            continue  # only user-space gradients need the painted-coords fix
        # accumulated transform: root -> element (SVG applies the list in order)
        xf = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        chain = []
        node = text_el
        while node is not None:
            chain.append(node)
            node = _parents.get(node)
        for node in reversed(chain):
            if node is text_el:
                xf = _xf_mul(xf, _xf_parse(node.get("transform")))
            elif _local(node.tag) == "g" and node.get("transform"):
                xf = _xf_mul(xf, _xf_parse(node.get("transform")))

        family, weight, size = _font_style(text_el)
        font_file = _pick_font_file(family, weight)
        if font_file is None:
            continue
        if font_file not in _font_tool_cache:
            _font_tool_cache[font_file] = TTFont(font_file)
        font = _font_tool_cache[font_file]

        elx = text_el.get("x")
        ely = text_el.get("y")
        cx = float(elx) if elx is not None else 0.0
        cy = float(ely) if ely is not None else 0.0
        dd = ""
        for run in text_el.itertext():
            dd += _run_path(font.getGlyphSet(), font, run, cx, cy, xf, size)

        if not dd:
            continue

        # materialise a userSpaceOnUse copy of the source gradient
        counter[0] += 1
        new_id = f"kaascan-grad-{counter[0]}"
        while new_id in used_ids:
            counter[0] += 1
            new_id = f"kaascan-grad-{counter[0]}"
        used_ids.add(new_id)

        defs_el = ET.SubElement(root, f"{{{svg_ns}}}defs")
        lin = ET.SubElement(defs_el, f"{{{svg_ns}}}linearGradient")
        lin.set("id", new_id)
        lin.set("gradientUnits", "userSpaceOnUse")
        lin.set("x1", base_lin.get("x1", "0%"))
        lin.set("y1", base_lin.get("y1", "0%"))
        lin.set("x2", base_lin.get("x2", "100%"))
        lin.set("y2", base_lin.get("y2", "0%"))
        total = len(base["stops"])
        for idx, (off, color) in enumerate(base["stops"]):
            if off is None:
                off = f"{idx * 100.0 / max(total - 1, 1):g}%"
            stop = ET.SubElement(lin, f"{{{svg_ns}}}stop")
            stop.set("offset", off)
            stop.set("stop-color", color)

        fill_op = _style_match(text_el, "fill-opacity") or "1"
        path_el = ET.SubElement(root, f"{{{svg_ns}}}path")
        path_el.set("style", f"stroke:none;fill:url(#{new_id});fill-opacity:{fill_op}")
        path_el.set("d", dd)

        parent = _parents.get(text_el)
        if parent is not None:
            parent.remove(text_el)
        painted = True

    if not painted:
        return svg_text
    return ET.tostring(root, encoding="unicode")


def _sanitize_font_families(svg_text: str) -> str:
    """Replace font-family references that svg2pdf can't resolve.

    When ``svg2pdf_py`` encounters a ``font-family`` name not loaded in the
    ``FontDatabase`` it silently drops **all** text from the entire SVG.
    This function swaps out any unrecognised font names for a known fallback so
    text is never lost.

    The fallback is weight-aware: bold/Ultra-Bold text maps to the loaded bold
    face (``FH Lecturis``) and normal text to ``Minigap``, otherwise bold styling
    would be silently flattened to a regular weight.
    """
    _REGULAR = "Minigap"
    _BOLD = "FH Lecturis"
    _BOLD_HINTS = ("bold", "ultra-bold", "extrabold", "800", "700", "900")
    _RE_FONT_FAMILY = re.compile(
        r'''font-family\s*:\s*(?:'([^']+)'|"([^"]+)"|&quot;([^&]+)&quot;|([^\s;"'}]+))'''
    )
    _RE_FONT_WEIGHT = re.compile(
        r'''font-weight\s*:\s*([^;]+)'''
    )
    _RE_INKSCAPE_SPEC = re.compile(
        r"""-inkscape-font-specification\s*:\s*'([^']+)'"""
    )

    def _is_bold(style_slot: str, family: str) -> bool:
        hint = _RE_FONT_WEIGHT.search(style_slot)
        weight = hint.group(1).strip().lower() if hint else ""
        if weight and (weight.isdigit() and int(weight) >= 700) or weight in ("bold", "bolder"):
            return True
        low_fam = family.lower()
        return any(h in low_fam or h in weight for h in _BOLD_HINTS)

    def _fallback(name: str) -> str:
        return _BOLD if _is_bold(style_slot_cache.get(name, ""), name) else _REGULAR

    # First pass: rewrite font-family for any family that isn't loaded.
    style_slot_cache = {}

    def _replace_family(m):
        name = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        name = name.strip()
        if name in _loaded_font_names:
            return m.group(0)
        # Look backwards from the match to find the enclosing style="..." slot
        # so we can honour font-weight.
        start = max(0, m.start() - 400)
        prev = svg_text[start:m.start()]
        style_slot = prev.rsplit('style="', 1)[-1] if 'style="' in prev else ""
        style_slot_cache[name] = style_slot
        return f"font-family:{_fallback(name)}"

    svg_text = _RE_FONT_FAMILY.sub(_replace_family, svg_text)

    def _replace_ink(m):
        spec = m.group(1)
        family = spec.split(",")[0].strip()
        if family in _loaded_font_names:
            return m.group(0)
        bold = _is_bold(style_slot_cache.get(family, ""), family) or any(
            b in spec.lower() for b in _BOLD_HINTS
        )
        face = _BOLD if bold else _REGULAR
        style = "Bold" if bold else "Normal"
        return f"-inkscape-font-specification:'{face}, {style}'"

    svg_text = _RE_INKSCAPE_SPEC.sub(_replace_ink, svg_text)

    # Final safety pass: a <text> element with *no* font-family at all renders
    # as dropped/black text because svg2pdf has no default face in its
    # FontDatabase. Give every such element an explicit loaded face.
    def _ensure_face(m):
        tag = m.group(0)
        if "font-family" in tag:
            return tag
        style_open = re.search(r"style=['\"]", tag)
        if style_open:
            return tag[: style_open.end()] + "font-family:Minigap;" + tag[style_open.end():]
        if tag.rstrip().endswith("/>"):
            return tag.rstrip()[:-2] + ' style="font-family:Minigap"/>'
        return tag[:-1] + ' style="font-family:Minigap">'

    svg_text = re.sub(r"<text\b[^>]*>", _ensure_face, svg_text)

    # Pass 2b: normalise text elements for svg2pdf compatibility.
    # svg2pdf ignores text-anchor when set only in CSS style (needs XML attr),
    # mishandles font-variant-position:sub, leaves editor scale transforms on
    # <text> that shift the rendered position, and does not honour tspan x/y
    # overrides for line breaks.  This pass applies at render time so the
    # template SVG stays untouched.

    def _fix_text_for_svg2pdf(m):
        open_tag = m.group(1)
        inner = m.group(2)
        close_tag = m.group(3)
        tag = open_tag

        # Bake the transform into x/y coordinates instead of leaving it
        # on the element — svg2pdf mishandles scale/rotate transforms on
        # <text> elements, causing position drift vs the template editor.
        sx = sy = tx = ty = 0.0
        has_transform = False
        xform_m = re.search(r'\btransform="([^"]*)"', tag)
        if xform_m:
            has_transform = True
            xform_str = xform_m.group(1)
            for op_m in re.finditer(
                r'(translate|scale)\s*\(\s*([^)]+)\)', xform_str
            ):
                kind = op_m.group(1)
                nums = [float(v) for v in op_m.group(2).replace(",", " ").split()]
                if kind == "translate":
                    tx = nums[0] if len(nums) > 0 else 0
                    ty = nums[1] if len(nums) > 1 else 0
                elif kind == "scale":
                    sx = nums[0] if len(nums) > 0 else 1
                    sy = nums[1] if len(nums) > 1 else 1
            # Apply to parent x/y
            x_m = re.search(r'\bx="([^"]+)"', tag)
            y_m = re.search(r'\by="([^"]+)"', tag)
            if x_m and y_m:
                new_x = float(x_m.group(1)) * sx + tx
                new_y = float(y_m.group(1)) * sy + ty
                tag = tag[:x_m.start()] + f'x="{new_x:.6g}"' + tag[x_m.end():]
                y_m2 = re.search(r'\by="([^"]+)"', tag)
                if y_m2:
                    tag = tag[:y_m2.start()] + f'y="{new_y:.6g}"' + tag[y_m2.end():]
            tag = re.sub(r'\s*transform="[^"]*"', '', tag)

        # Compensate for font-variant-position:sub — the template editor
        # renders this as a visible downward shift, but svg2pdf ignores it
        # entirely.  Nudge y down by ~0.35× font-size so the gap between
        # text and surrounding elements (e.g. barcode) matches the editor.
        sub_offset = 0.0
        if 'font-variant-position:sub' in tag:
            fs_m = re.search(r'font-size:\s*([\d.]+)px', tag)
            if fs_m:
                sub_offset = float(fs_m.group(1)) * 0.35
                y_m3 = re.search(r'\by="([^"]+)"', tag)
                if y_m3:
                    new_y2 = float(y_m3.group(1)) + sub_offset
                    tag = (tag[:y_m3.start()]
                           + f'y="{new_y2:.6g}"'
                           + tag[y_m3.end():])

        # Promote text-anchor from CSS style to XML attribute (svg2pdf only
        # reads the XML attribute, not the CSS property).
        anchor_m = re.search(r'text-anchor\s*:\s*([^;\s"]+)', tag)
        if anchor_m:
            val = anchor_m.group(1)
            tag = re.sub(r'\btext-anchor\s*:\s*[^;\s"]+\s*;?', '', tag)
            close_idx = tag.rfind('>')
            tag = tag[:close_idx] + f' text-anchor="{val}"' + tag[close_idx:]

        # If the text had a transform, bake it into child tspan x/y too
        # and strip their explicit x/y so _split_multiline can re-split.
        if has_transform and inner:
            def _fix_tspan(ts_m):
                ts_attrs = ts_m.group(1)
                ts_text = ts_m.group(2)
                tx_m = re.search(r'\bx="([^"]+)"', ts_attrs)
                ty_m = re.search(r'\by="([^"]+)"', ts_attrs)
                if tx_m and ty_m:
                    nx = float(tx_m.group(1)) * sx + tx
                    ny = float(ty_m.group(1)) * sy + ty + sub_offset
                    ts_attrs = ts_attrs[:tx_m.start()] + f'x="{nx:.6g}"' + ts_attrs[tx_m.end():]
                    ty_m2 = re.search(r'\by="([^"]+)"', ts_attrs)
                    if ty_m2:
                        ts_attrs = ts_attrs[:ty_m2.start()] + f'y="{ny:.6g}"' + ts_attrs[ty_m2.end():]
                    # Strip x/y so _split_multiline can handle positioning
                    ts_attrs = re.sub(r'\s+x="[^"]*"', '', ts_attrs)
                    ts_attrs = re.sub(r'\s+y="[^"]*"', '', ts_attrs)
                return f'<tspan{ts_attrs}>{ts_text}</tspan>'
            inner = re.sub(r'<tspan\b([^>]*)>([^<]*)</tspan>', _fix_tspan, inner)

        return tag + inner + close_tag

    svg_text = re.sub(r'(<text\b[^>]*>)(.*?)(</text>)', _fix_text_for_svg2pdf, svg_text)

    # Pass 2c: split multi-tspan <text> elements into separate <text> tags so
    # each line renders independently (svg2pdf does not honour tspan y overrides
    # for line breaks).  Only applies when tspans lack explicit x/y attrs
    # (i.e. the editor handled visual wrapping).
    _RE_TEXT_BLOCK = re.compile(
        r'(<text\b[^>]*>)(.*?)(</text>)', re.DOTALL
    )
    _RE_TSPAN = re.compile(
        r'<tspan\b([^>]*)>([^<]*)</tspan>', re.DOTALL
    )

    def _split_multiline(m):
        open_tag = m.group(1)
        inner = m.group(2)
        close_tag = m.group(3)

        tspans = _RE_TSPAN.findall(inner)
        if len(tspans) <= 1:
            return m.group(0)  # single tspan or no tspan — leave alone

        # Check whether any tspan already has absolute x/y — if so, respect
        # the author's positioning and don't split.
        if any(' x=' in attrs or ' y=' in attrs for attrs, _ in tspans):
            return m.group(0)

        # Extract parent x, y and font-size.
        px_m = re.search(r'\bx="([^"]+)"', open_tag)
        py_m = re.search(r'\by="([^"]+)"', open_tag)
        if not px_m or not py_m:
            return m.group(0)
        px = float(px_m.group(1))
        py = float(py_m.group(1))

        fs_m = re.search(r'font-size\s*:\s*([\d.]+)', open_tag)
        font_size = float(fs_m.group(1)) if fs_m else 1.98825
        line_height = font_size * 1.122

        # Build one <text> per tspan.
        # Keep all attributes from the parent <text> but give each its own y.
        # Strip existing y from the opening tag so we don't duplicate it.
        base_tag = re.sub(r'\s+y="[^"]*"', '', open_tag)
        base_tag = base_tag.rstrip(">").rstrip()
        parts = []
        for i, (attrs, text) in enumerate(tspans):
            if not text.strip():
                continue
            new_y = py + i * line_height
            parts.append(
                f'{base_tag} y="{new_y}">'
                f'<tspan{attrs}>{text}</tspan>'
                f'{close_tag}'
            )
        return ''.join(parts) if parts else m.group(0)

    svg_text = _RE_TEXT_BLOCK.sub(_split_multiline, svg_text)

    # Pass 3: a card side authored/saved in Inkscape can carry a *pixel* page
    # size (width="85.6px") instead of mm. svg2pdf interprets that as
    # 85.6 points, which makes the page ~25% of the front card. Normalise the
    # root page width/height to mm (the numeric value stays the same; it's the
    # same unit of measure as the viewBox).
    svg_start = svg_text.find("<svg")
    root_end = svg_text.find(">", svg_start if svg_start != -1 else 0)
    if root_end != -1 and svg_start != -1:
        root_tag = svg_text[svg_start: root_end + 1]
        root_tag = re.sub(
            r'(width|height)="([^"]+)"',
            lambda m: f'{m.group(1)}="{float(m.group(2)[:-2]):g}mm"'
            if m.group(2).lower().endswith("px")
            else m.group(0),
            root_tag,
        )
        svg_text = svg_text[:svg_start] + root_tag + svg_text[root_end + 1:]

    # Pass 4: gradient text -> flattened gradient paths (see module docs above).
    # Runs before the solid-fill pass so url() fills are consumed here; anything
    # left over (radial / objectBoundingBox gradients) is handled below.
    svg_text = _gradient_flatten(svg_text)

    # Pass 5: svg2pdf (i) refuses gradient fills on text elements and
    # (ii) ignores a bare `fill` attribute on text (it only reads the style).
    # Gradients used on text are resolved to their first solid stop colour and
    # any attribute fill is promoted into the style so colours always apply.
    gradient_stops: dict[str, list[str]] = {}
    gradient_attrs: dict[str, str] = {}
    _GRAD_TAG = re.compile(
        r"<(linear|radial)Gradient(?:\s[^>]*?)?/?>|"
        r"</(?:linear|radial)Gradient>"
    )

    def _collect_gradients() -> None:
        probe = 0
        while True:
            match = _GRAD_TAG.search(svg_text, probe)
            if not match:
                break
            token = match.group(0)
            if token.endswith("/>"):  # self-closed gradient (no body)
                tag = token
                gid = re.search(r'\bid="([^"]+)"', tag)
                if gid:
                    gradient_stops[gid.group(1)] = []
                    gradient_attrs[gid.group(1)] = tag
                probe = match.end()
                continue
            # opening tag: scan for its balanced closing tag, tolerating defs
            # that *contain* nested gradient definitions.
            depth = 1
            segment_start = match.start()
            scan = match.end()
            while depth:
                inner = _GRAD_TAG.search(svg_text, scan)
                if not inner:
                    depth = 0
                    break
                inner_token = inner.group(0)
                if inner_token.endswith("/>"):
                    pass
                elif inner_token.startswith("</"):
                    depth -= 1
                else:
                    depth += 1
                scan = inner.end()
            block = svg_text[segment_start:scan]
            tag = block.split(">", 1)[0] + ">"
            gid = re.search(r'\bid="([^"]+)"', tag)
            if gid:
                stops = re.findall(
                    r"""<stop\b[^>]*?stop-color\s*:\s*([^;"'\s]+)""", block
                ) or re.findall(r'''<stop\b[^>]*?stop-color="([^"]+)"''', block)
                gradient_stops[gid.group(1)] = stops
                gradient_attrs[gid.group(1)] = tag
            probe = scan

    _collect_gradients()

    def _first_gradient_colour(gid: str) -> str | None:
        seen: set[str] = set()

        def _resolve(current: str) -> str | None:
            if current in seen or current not in gradient_stops:
                return None
            seen.add(current)
            href = re.search(
                r'(?:xlink:)?href="[^"]*#([^"]+)"', gradient_attrs.get(current, "")
            )
            if href and gradient_stops[current]:
                return gradient_stops[current][0]
            if href:
                return _resolve(href.group(1))
            return gradient_stops[current][0] if gradient_stops[current] else None

        return _resolve(gid)

    def _fix_text_fill(m):
        tag = m.group(0)

        def _to_solid(cm):
            colour = _first_gradient_colour(cm.group(1))
            return f"fill:{colour}" if colour else cm.group(0)

        tag = re.sub(r"fill\s*:\s*url\(\s*#([^)\"']+)\s*\)", _to_solid, tag)
        if "fill:" not in tag:
            fill_match = re.search(r'\bfill="([^"]*)"', tag)
            if fill_match:
                fill_value = fill_match.group(1)
                if fill_value.startswith("url("):
                    inner = re.search(r"#([^)]+)", fill_value)
                    colour = _first_gradient_colour(inner.group(1)) if inner else None
                    if colour is None:
                        return tag
                    fill_value = colour
                tag = re.sub(r'\s+fill="[^"]*"', "", tag, count=1)
                style_open = re.search(r"style=['\"]", tag)
                if style_open:
                    insert_at = style_open.end()
                    tag = tag[:insert_at] + f"fill:{fill_value};" + tag[insert_at:]
                else:
                    if tag.rstrip().endswith("/>"):
                        tag = tag.rstrip()[:-2] + f' style="fill:{fill_value}"/>'
                    else:
                        tag = tag[:-1] + f' style="fill:{fill_value}">'
            elif 'style="' not in tag and "style='" not in tag:
                if tag.rstrip().endswith("/>"):
                    tag = tag.rstrip()[:-2] + f' style="fill:#000000"/>'
                else:
                    tag = tag[:-1] + ' style="fill:#000000">'
        return tag

    svg_text = re.sub(r"<(?:text|tspan)\b[^>]*>", _fix_text_fill, svg_text)
    return svg_text

# defined just variables for the student data


class Student(BaseModel):
    photo: str
    name: str
    Class: str
    school_name: str
    data_qrcode: str = ""  # optional: empty dict "{}" when the card uses a barcode instead
    student_id: str
    student_code: str = ""  # student code / card number, e.g. "5298 7601 2345 6789"
    valid_thru: str = "09/30"  # card validity/expiry shown under "VALID THRU"
    school_type: str = "HIGH SCHOOL"  # school category, e.g. "HIGH SCHOOL"
    slogan: str = ""  # school motto/slogan shown under the school name
    template_name: str
    
    # Additional card variables
    stamp: Optional[str] = None  # Base64 encoded stamp image
    signature: Optional[str] = None  # Base64 encoded signature image
    barcode: Optional[str] = None  # Base64 encoded barcode image
    school_logo: Optional[str] = None  # Base64 encoded school logo
    director_name: str = ""  # school head / director name (back of the card)
    director_contact: str = ""  # director contact shown next to the name
    side_2_color: str = "#00897B"  # Card back color
    snFontsize: float = 2.4932  # Student name font size
    snx: float = -10.523738  # Student name x axis position
    sny: float = -0.8769781  # Student name y axis position
    color: Optional[str] = None  # primary/brand color override
    background_color: Optional[str] = None  # card body background override
    accent_color: Optional[str] = None  # secondary accent override
    color_overrides: Optional[dict] = None  # {"existing-hex-or-role": "new-hex"}
    colors: Optional[dict] = None  # full color map {role-or-hex: new-hex}; default = get_template_colors()


class CardConfig(BaseModel):
    """Structured configuration for card generation.
    
    This class provides a clean API for specifying all card variables,
    including stamps, signatures, barcodes, and other customization options.
    """
    # Student data
    student_name: str
    student_class: str
    school_name: str
    student_id: str
    photo: str  # Base64 encoded student photo
    
    # QR code data
    data_qrcode: str = ""  # optional: empty dict "{}" when the card uses a barcode instead
    
    # Template selection
    template_name: str
    
    # Card number / student code
    student_code: str = ""  # e.g. "5298 7601 2345 6789"
    valid_thru: str = "09/30"  # card expiry shown under "VALID THRU"
    school_type: str = "HIGH SCHOOL"  # school category, e.g. "HIGH SCHOOL"
    
    # Branding elements
    slogan: str = ""  # school motto/slogan shown under the school name
    school_logo: Optional[str] = ""  # Base64 encoded school logo
    stamp: Optional[str] = ""  # Base64 encoded stamp image
    signature: Optional[str] = ""  # Base64 encoded signature image
    barcode: Optional[str] = ""  # Base64 encoded barcode image
    
    # Card styling
    side_2_color: str = "#00897B"  # Card back color
    
    # Back-of-the-card school head (director) info
    director_name: str = ""  # school head / director name
    director_contact: str = ""  # director contact shown next to the name
    
# Typography settings
    snFontsize: float = 2.4932  # Student name font size
    snx: float = -10.523738  # Student name x axis position
    sny: float = -0.8769781  # Student name y axis position

    # Card color overrides (None keeps the template's own colors)
    color: Optional[str] = None  # primary/brand color
    background_color: Optional[str] = None  # card body background
    accent_color: Optional[str] = None  # secondary accent
    color_overrides: Optional[dict] = None  # {"existing-hex-or-role": "new-hex"}
    colors: Optional[dict] = None  # full color map {role-or-hex: new-hex}; default = get_template_colors()

    def to_student(self) -> Student:
        """Convert CardConfig to Student model for backward compatibility."""
        return Student(
            photo=self.photo,
            name=self.student_name,
            Class=self.student_class,
            school_name=self.school_name,
            data_qrcode=self.data_qrcode,
            student_id=self.student_id,
            student_code=self.student_code,
            valid_thru=self.valid_thru,
            school_type=self.school_type,
            template_name=self.template_name,
            slogan=self.slogan,
            stamp=self.stamp,
            signature=self.signature,
            barcode=self.barcode,
            school_logo=self.school_logo,
            side_2_color=self.side_2_color,
            snFontsize=self.snFontsize,
            snx=self.snx,
            sny=self.sny,
            director_name=self.director_name,
            director_contact=self.director_contact,
            color=self.color,
            background_color=self.background_color,
            accent_color=self.accent_color,
            color_overrides=self.color_overrides,
            colors=self.colors,
        )

    def generate_card_content(self) -> dict:
        """Generate card content using this configuration."""
        return generate_card(
            template_name=self.template_name,
            image_base64=self.photo,
            student_name=self.student_name,
            student_class=self.student_class,
            school_name=self.school_name,
            student_id=self.student_id,
            student_code=self.student_code,
            valid_thru=self.valid_thru,
            school_type=self.school_type,
            data_qrcode=self.data_qrcode,
            school_slogan=self.slogan,
            side_2_color=self.side_2_color,
            school_logo=self.school_logo,
            stamp_base64=self.stamp,
            student_barcode=self.barcode,
            signature_base64=self.signature,
            snFontsize=self.snFontsize,
            snx=self.snx,
            sny=self.sny,
            director_name=self.director_name,
            director_contact=self.director_contact,
            color=self.color,
            background_color=self.background_color,
            accent_color=self.accent_color,
            color_overrides=self.color_overrides,
            colors=self.colors,
        )


def image_file_to_base64(file_path: str) -> str:
    """Convert an image file to base64 string.
    
    Args:
        file_path: Path to the image file (PNG, JPG, etc.)
        
    Returns:
        Base64 encoded string of the image
    """
    with open(file_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def image_url_to_base64(url: str) -> str:
    """Convert an image URL to base64 string.
    
    Args:
        url: URL of the image
        
    Returns:
        Base64 encoded string of the image
    """
    response = requests.get(url)
    return base64.b64encode(response.content).decode('utf-8')


def resize_image_base64(image_base64: str, max_width: int = 100, max_height: int = 100) -> str:
    """Resize a base64 encoded image to fit within specified dimensions.
    
    Args:
        image_base64: Base64 encoded image string
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
        
    Returns:
        Resized base64 encoded image string
    """
    image_data = base64.b64decode(image_base64)
    image = Image.open(BytesIO(image_data))
    
    # Calculate new dimensions while maintaining aspect ratio
    ratio = min(max_width / image.width, max_height / image.height)
    new_width = int(image.width * ratio)
    new_height = int(image.height * ratio)
    
    # Resize image
    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Convert back to base64
    buffer = BytesIO()
    resized_image.save(buffer, format=image.format or 'PNG')
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def save_card(card_content: dict, card_id: str, class_folder: str, school_name: str):

    card_templates = card_content
    front_card = _sanitize_font_families(card_templates["front"]["card"])
    back_card = _sanitize_font_families(card_templates["back"]["card"])

    output_dir = get_output_dir() / school_name / class_folder
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = output_dir / f"final_student_card_{card_id}.pdf"

    # Render both faces straight from the in-memory SVG strings — no wasted
    # disk write + re-read of every card's template files.
    try:
        pdf_pages = svg2pdf_py.svg_pages_to_pdfs([front_card, back_card], svg)
    except Exception as error:
        print(f":: failed converting svg of card_id: {card_id} to pdf: {error}")
        pdf_pages = []

    if not pdf_pages:
        print(f":: warning: svg-to-pdf produced no pages for card_id: {card_id}")

    doc = pymupdf.open()
    for pdf_bytes in pdf_pages:
        part = pymupdf.open("pdf", pdf_bytes)
        doc.insert_pdf(part)
        part.close()

    # garbage=0 / deflate=False: each per-card PDF is tiny (2 pages). The heavy
    # GC + stream re-compression only pays off on the final merged document, so
    # skip it here to avoid a full PDF rebuild per card.
    doc.save(str(output_pdf), garbage=0, deflate=False)
    doc.close()

    # CMYK print option: convert the freshly written PDF in place so print shops
    # receive true subtractive colors (see set_print_cmyk / KAA_PRINT_CMYK).
    if get_print_cmyk():
        convert_pdf_to_cmyk(str(output_pdf))


def _decode_urlencoded_placeholders(svg_text: str) -> str:
    """Decode Inkscape's URL-encoded curly braces back to normal format placeholders.

    Inkscape encodes ``{`` as ``%7B`` and ``}`` as ``%7D`` inside ``xlink:href``
    attributes, which prevents Python's ``str.format()`` from recognising them.
    This helper reverses that encoding so template substitution works correctly.
    """
    return svg_text.replace("%7B", "{").replace("%7D", "}")


def _safe_format(svg_text: str, fmt_args: dict) -> str:
    """Substitute ``{name}`` placeholders for the supplied keys, leaving any
    other ``{...}`` group intact.

    Templates may legitimately contain CSS rules such as ``{ color:#dedede; }``
    or stray braces. ``str.format()`` would mis-parse those as field names and
    raise ``KeyError`` (e.g. ``' color'``), crashing card generation. A regex
    only substitutes names we actually know, so stray braces survive untouched.

    Two substitution channels:

    * inline ``{name}`` text / ``xlink:href="{name}"`` — the classic form used
      when the template object *is* the placeholder (the design shows braces).
    * ``data-format="{name}"`` anchors on a ``<text>`` / ``<image>`` element —
      the design object keeps nice demo text (or the bought-in artwork) while
      the anchor says which variable is meant to land here at card time. The
      visible content is replaced by the real value, so finished-looking
      templates stay clean AND stay live.
    """
    def sub(m):
        prefix = m.group(1) or ""
        name = m.group(2)
        if name in fmt_args:
            value = str(fmt_args[name])
            # Inkscape-authored templates often anchor images with a "..\\" path
            # prefix (xlink:href="../{photo}"). If the injected value is an
            # absolute URI, that prefix corrupts it ("../data:..." never loads),
            # so drop it — otherwise the image silently vanishes from the card.
            is_uri = value.startswith("data:") or value.startswith("http://") or value.startswith("https://")
            if is_uri and prefix:
                return value
            return prefix + value
        return m.group(0)  # unknown/ambiguous group: leave as-is

    # Anchors first: their marker braces are attributes the classic pass must
    # never consume (``{name}`` inside ``data-format="..."`` would be replaced
    # by the raw value, turning the anchor into a dead marker).
    svg_text = _substitute_format_anchors(svg_text, fmt_args)
    return re.sub(r"(\.\./|\./)?\{([A-Za-z_][\w]*)\}", sub, svg_text)


def _escape_xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _substitute_format_anchors(svg_text: str, fmt_args: dict) -> str:
    """Fill ``data-format=\"{name}\"`` anchors with the supplied values.

    The anchor keeps the design looking like a finished card (demo text stays
    put until generation) while still carrying the variable it should resolve
    to. A ``<text>`` gets its demo run replaced; an ``<image>`` gets a new
    ``xlink:href``. Unknown anchors are left untouched.
    """

    def text_sub(m):
        block = m.group(0)
        name = re.search(r'data-format="\{([A-Za-z_][\w]*)\}"', block)
        if not name or name.group(1) not in fmt_args:
            return block
        name = name.group(1)
        value = _escape_xml_text(str(fmt_args[name]))
        inner = re.search(r"<text\b[^>]*>(.*)</text>", block, flags=re.S).group(1)
        span = re.search(r"<tspan\b[^>]*>", inner)
        if span:
            pad = inner[: span.start()]
            inner = pad + span.group(0) + value + "</tspan>"
        else:
            inner = value
        open_tag = re.match(r"<(text)\b[^>]*>", block).group(0)
        open_tag = re.sub(r' data-format="[^"]*"', f' data-format="{{{name}}}"', open_tag, count=1)
        return open_tag + inner + "</text>"

    def image_sub(m):
        tag = m.group(0)
        name = re.search(r'data-format="\{([A-Za-z_][\w]*)\}"', tag)
        if not name or name.group(1) not in fmt_args:
            return tag
        name = name.group(1)
        value = _escape_xml_text(str(fmt_args[name])).replace("\n", "").replace("\r", "")
        tag = re.sub(r'data-format="[^"]*"', f' data-format="{{{name}}}"', tag, count=1)
        if re.search(r'xlink:href="', tag):
            tag = re.sub(r'xlink:href="[^"]*"', f'xlink:href="{value}"', tag, count=1)
        else:
            tag = tag[:-2] + f' xlink:href="{value}"/>'
        return tag

    svg_text = re.sub(
        r"<text\b[^>]*data-format=\"\{([A-Za-z_][\w]*)\}\"[^>]*>.*?</text>",
        text_sub, svg_text, flags=re.S,
    )
    svg_text = re.sub(
        r"<image\b[^>]*data-format=\"\{([A-Za-z_][\w]*)\}\"[^>]*?/>",
        image_sub, svg_text, flags=re.S,
    )
    return svg_text


def _fmt(value) -> str:
    """Never let a None reach the SVG: empty string instead."""
    return "" if value is None else str(value)


def _img_href(value):
    """Only safe image references reach xlink:href; anything else renders invisibly.

    Accepts data-URIs, http(s) URLs, and bare base64 strings (which are
    promoted to ``data:image/png;base64,`` so images are never silently
    dropped).
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value.strip()
    if value.startswith("data:image") or value.startswith("http"):
        return value
    # bare base64 → data URI (default to PNG; harmless for most stamps/logos)
    return f"data:image/png;base64,{value}"


# Template-name → {role: role's hex in the template}. A role override therefore
# swaps exactly that color. Unknown templates keep every literal color untouched.
# Constants like this also give every color a *name* — see get_template_colors().
_TEMPLATE_COLOR_ROLES = {
    "BLUE_TOPBAR_CARD": {
        "primary": "#0000ff",        # banner / top bar
        "background": "#f9f9f9",     # card front body
        "back_bg": "#f9f9f9",        # card back body (same hex on this template)
        "panel": "#e6e6e6",          # gray photo panel / info box
        "text_head": "#28220b",      # headings (student name etc.)
        "text_body": "#1a1a1a",      # body text
        "black": "#000000",          # strokes / dark lines
        "green": "#008000",          # status stripe / badge
        "white": "#ffffff",          # white elements
        "ink": "#010101",            # near-black ink
        "text_muted": "#333333",     # secondary gray text
        "scheme": "#dedede",         # color-scheme token
        "slot_photo": "#ff7f2a",     # photo placeholder highlight
        "slot_text": "#ff5200",      # text placeholder highlight
        "slot_alert": "#ff2a2a",     # placeholder alert highlight
    },
    "BANK_INSPIRE": {
        "primary": "#0000ff",        # logo / brand accents
        "accent": "#2eb1ff",         # light-blue accents
        "background": "#f9f9f9",     # card body
        "back_bg": "#f9f9f9",        # card back body
        "text_body": "#1a1a1a",      # main text
        "white": "#ffffff",          # white fills / strokes
        "black": "#000000",          # strokes / dark lines
        "gray": "#999999",           # muted gray text
        "gray_light": "#b3b3b3",     # light gray text / dividers
        "scheme": "#e0e0e0",         # color-scheme token
        "alert": "#ffaaaa",          # placeholder alert highlight
    },
}


def get_template_colors(template_name: str) -> dict:
    """Return {role: template-hex} for every color the template defines.

    Values are the template's own default colors — pass any of them to
    ``colors=`` (or ``color_overrides=``) to recolor the card and everything
    else keeps the template default.
    """
    return dict(_TEMPLATE_COLOR_ROLES.get(template_name, {}))


def _apply_color_overrides(svg_text: str, overrides: dict) -> str:
    """Rewrite literal hex colors in rendered SVG content.

    ``overrides`` maps ``existing-hex-or-role`` → ``new-hex``. Roles are the
    keys of ``_TEMPLATE_COLOR_ROLES``; hex keys are matched regardless of case.
    """
    if not overrides:
        return svg_text
    for old, new in overrides.items():
        if not (old and new):
            continue
        old = str(old).lower()
        if not old.startswith("#"):
            continue
        svg_text = re.sub(r"(?i)" + re.escape(old), str(new).lower(), svg_text)
    return svg_text


def _resolve_color_overrides(template_name: str, *, color, background_color, accent_color, color_overrides, colors) -> dict:
    """Combine role params + explicit overrides into a {hex: new_hex} map."""
    roles = _TEMPLATE_COLOR_ROLES.get(template_name, {})
    overrides = dict(color_overrides or {})
    for param, role in (("color", "primary"), ("background_color", "background"), ("accent_color", "accent")):
        value = {"color": color, "background_color": background_color, "accent_color": accent_color}[param]
        if value:
            old = roles.get(role)
            if old:
                overrides.setdefault(old, str(value))
    for role, value in (colors or {}).items():
        old = str(role).lower()
        if old.startswith("#"):
            overrides.setdefault(old, str(value))
        elif roles.get(old):
            overrides.setdefault(roles[old], str(value))
    resolved = {}
    for old, new in overrides.items():
        old = str(old).lower()
        if not old.startswith("#"):
            old = roles.get(old)  # allow role-name keys like {"primary": "#123456"}
        if old:
            resolved.setdefault(old, str(new))
    return resolved


def generate_card(
    template_name: str,
    image_base64: str,
    student_name: str,
    student_class: str,
    school_name: str,
    student_id: str,
    student_code: str = "",  # student code / card number (e.g. "5298 7601 2345 6789")
    valid_thru: str = "09/30",  # card expiry shown under "VALID THRU"
    school_type: str = "HIGH SCHOOL",  # school category, e.g. "HIGH SCHOOL"
    school_slogan: str = "",  # school motto/slogan shown under the school name
    # qrcode
    data_qrcode: str = "",  # optional: leave empty dict "{}" when the card uses a barcode instead
    side_2_color: str = "#00897B",
    # card colors — None keeps the template's own colors; give a hex/tuple to recolor
    color: str = None,  # primary/brand color (e.g. the banner bar)
    background_color: str = None,  # card body background
    accent_color: str = None,  # secondary accent color (if the template defines one)
    color_overrides: dict = None,  # generic {"existing-hex-or-role": "new-hex"}
    colors: dict = None,  # full color map: {role-or-hex: new-hex}; defaults = get_template_colors()
    # branding
    school_logo: str = "",
    stamp_base64: str = "",
    student_barcode: str = "",
    signature_base64: str = "",
    director_name: str = "",  # school head / director name (back of the card)
    director_contact: str = "",  # director contact shown next to the name
    # styling
    snFontsize=2.4932,  # student name font size
    snx=-10.523738,  # student name x axis position
    sny=-0.8769781,  # student name y axis position
) -> str:  # the card content is a string so it has a string return type declared

    # The barcode always carries the *digits* of the student code — never a
    # UUID or a raw id. If no real barcode image was supplied (or the supplied
    # one looks like a UUID, which its dashes betray), generate it here from
    # the student code so the card never shows a non-digit value.
    if not (student_barcode and str(student_barcode).strip() and "-" not in student_barcode):
        from card_sdk.base64qrcode import base64_barcode

        _digits = re.sub(r"[^0-9]", "", str(student_code or ""))
        if _digits:
            student_barcode = base64_barcode({"code": _digits})

    fmt_args = {
        "color": "#0d0000",
        "academic_year": f"ACADEMIC YEAR {datetime.now().year}",
        "school_subtitle": "subtitle here",
        "snFontsize": _fmt(snFontsize),
        "name": _fmt(student_name),
        "student_names": _fmt(student_name),
        "snx": _fmt(snx),
        "sny": _fmt(sny),
        "Class": _fmt(student_class),
        "student_class": _fmt(student_class),
        "school_name": _fmt(school_name),
        "student_id": _fmt(student_id),
        "student_code": _fmt(student_code),
        "valid_thru": _fmt(valid_thru),
        "school_type": _fmt(school_type),
        "school_slogan": _fmt(school_slogan),
        "school_logo": _img_href(school_logo),
        "image_base64": _img_href(image_base64),
        "student_photo": _img_href(image_base64),
        "side_2_color": _fmt(side_2_color) or "#00897B",
        "data_qrcode": _img_href(data_qrcode),
        "stamp_base64": _img_href(stamp_base64),
        "student_barcode": _img_href(student_barcode),
        "signature_base64": _img_href(signature_base64),
        "director_name": _fmt(director_name),
        "director_contact": _fmt(director_contact),
    }

    # Auto-heal: an Inkscape save / CardFly round-trip / re-export can strip the
    # variable wiring from a template's visible text (demo text freezes on every
    # card). Re-attach the anchors before rendering; missing vars are restored.
    try:
        from card_sdk import template_variables as _tv

        _tv.restore_template_variables(template_name)
    except Exception:
        pass

    with open(f"{base_dir}/templates/templates_base/{template_name}/front.card.kaascan", "r", encoding="utf-8") as f:
        front = _safe_format(_decode_urlencoded_placeholders(f.read()), fmt_args)

    with open(f"{base_dir}/templates/templates_base/{template_name}/back.card.kaascan", "r", encoding="utf-8") as f:
        back = _safe_format(_decode_urlencoded_placeholders(f.read()), fmt_args)

    # Per-role color override: side_2_color recolors the card *back* only, the
    # rest apply to both faces. Colors already on the template keep their own
    # value unless an override targets them.
    overrides = _resolve_color_overrides(
        template_name,
        color=color,
        background_color=background_color,
        accent_color=accent_color,
        color_overrides=color_overrides,
        colors=colors,
    )
    front = _apply_color_overrides(front, overrides)
    back_overrides = dict(overrides)
    back_roles = _TEMPLATE_COLOR_ROLES.get(template_name, {})
    # "background" drives the front body; the back body color is side_2_color.
    if back_roles.get("background"):
        back_overrides.pop(back_roles["background"], None)
    if side_2_color and back_roles.get("back_bg"):
        back_overrides[back_roles["back_bg"]] = str(side_2_color)
    back = _apply_color_overrides(back, back_overrides)

    card_content = {"front": {"card": front}, "back": {"card": back}}

    # Report any required element that was not supplied so it doesn't silently
    # vanish from the card (stamp / signature / barcode / logo / QR / photo).
    # ``data_qrcode`` is an alternative to ``student_barcode``: a card that
    # carries a barcode does not need QR data, so skip that check when a
    # barcode is present.
    _missing = [
        name
        for name, raw in (
            ("stamp_base64", stamp_base64),
            ("signature_base64", signature_base64),
            ("student_barcode", student_barcode),
            ("school_logo", school_logo),
            ("data_qrcode", data_qrcode),
            ("image_base64", image_base64),
        )
        if not (raw and str(raw).strip())
        and not (name == "data_qrcode" and student_barcode and str(student_barcode).strip())
    ]
    if _missing:
        report_missing(
            card_id=student_id,
            school_name=school_name,
            student_class=student_class,
            missing=_missing,
        )

    output_dir = get_output_dir() / school_name / student_class
    # parents=True so a brand-new school folder is created too — cards
    # always land inside the student's Class folder.
    output_dir.mkdir(parents=True, exist_ok=True)

    save_card(
        card_content,
        card_id=student_id,
        class_folder=student_class,
        school_name=school_name,
    )

    from card_sdk.terminal_kid import Tkd

    Tkd.inform_user(f"[ok] {student_id[:5]} card saved ::")
    Tkd.hello()
    print(color_text(text="(saving cards) Please wait...", color="blue"))

    return f"(completed output folder) : {get_output_dir()}/"


_REPORT_CTR = 0
_report_ctr_initialized = False
_report_lock: object = None


def _report_path() -> str:
    return f"{base_dir}/report/cards_report.json"


def _init_report_counter() -> int:
    """Return the number of records already in cards_report.json (O(1) read)."""
    global _REPORT_CTR, _report_ctr_initialized, _report_lock
    import threading
    if _report_lock is None:
        _report_lock = threading.Lock()
    if not _report_ctr_initialized:
        _report_ctr_initialized = True
        try:
            import json as _json
            with open(_report_path(), "r", encoding="utf-8") as fh:
                _REPORT_CTR = len(_json.load(fh))
        except Exception:
            _REPORT_CTR = 0
    return _REPORT_CTR


async def cards_report(user_id: str, status: str) -> any:
    """Record a card event *cheaply*.

    The old implementation called jsave(append=True) which re-parsed and
    rewrote the whole JSON array on every single card — O(n²) disk churn that
    can turn a large batch into minutes. We append one JSON line (JSONL) per
    event and keep an in-memory counter for count_cards(), so a batch of N
    cards costs O(1) file I/O each instead of O(n).
    """
    global _REPORT_CTR
    if _report_lock is None:
        _init_report_counter()
    try:
        with _report_lock:
            with open(_report_path() + "l", "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {"user_id": user_id, "task": "generate_card", "status": status}
                    )
                    + "\n"
                )
            _REPORT_CTR += 1
    except Exception:
        pass
    return None


def current_report_count() -> int:
    """Sync in-memory counter with disk (throttled) and return it."""
    _init_report_counter()
    return _REPORT_CTR


def report_missing(card_id: str, school_name: str = "", student_class: str = "", missing: list = None) -> None:
    """Record that one or more card elements were missing.

    Prints a visible warning and appends a JSONL line to the report so the run
    can be audited afterwards. ``missing`` is a list of element names such as
    ``["stamp", "signature", "barcode", "school_logo"]``.
    """
    _init_report_counter()
    missing = missing or []
    if not missing:
        return
    context = ""
    if school_name or student_class:
        context = f" ({school_name} / {student_class})"
    try:
        from color_cli import color_text as _ct
        for name in missing:
            print(_ct(f"⚠ missing: {name}", "yellow") + f"  → card {card_id}{context}")
    except Exception:
        pass
    try:
        import threading as _threading
        lock = _report_lock if _report_lock is not None else _threading.Lock()
        with lock:
            with open(_report_path() + "l", "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "user_id": card_id,
                            "task": "generate_card",
                            "status": "missing",
                            "missing": missing,
                            "school_name": school_name,
                            "student_class": student_class,
                        }
                    )
                    + "\n"
                )
    except Exception:
        pass


async def check_image_cache(user_id: str) -> dict:

    cache_image_path = (
        pathlib.Path(__file__).parent / "tmp" / "cache_images" / f"{user_id}.png"
    )
    if cache_image_path.exists():
        with open(cache_image_path, "rb") as cache_image:
            image_bytes = cache_image.read()
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            return {
                "status": True,
                "base64_image": f"data:image/png;base64,{base64_image}",
            }
    else:
        return {"status": False}


async def cache_image(id: str, image_bytes: bytes) -> None:

    cache_path = pathlib.Path(__file__).parent / "tmp" / "cache_images" / f"{id}.png"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as image_file:
        image_file.write(image_bytes)


# here we got a lot of work to do so the app work faster cause images are taking long to download
async def async_image_url_to_base64(
    session: aiohttp.ClientSession, image_url: str, user_id: str, counter: int
) -> Optional[str]:
    """
    Fetch image from URL and convert to base64 data URL

    Args:
        session: aiohttp ClientSession
        image_url: URL of the image

    Returns:
        Base64 encoded data URL or None if failed
    """
    try:

        # GET the image with timeout
        async with session.get(
            image_url, timeout=aiohttp.ClientTimeout(total=200)
        ) as response:

            # Check if request was successful
            if response.status != 200:
                print(f"[*] HTTP Error: {response.status} for {image_url}")
                return None
            else:

                from card_sdk.card_agent import count_cards

                inc = count_cards()

                from card_sdk.terminal_kid import Tkd

                with open(f"{base_dir}/report/users.json") as users_report:
                    total_users = json.loads(users_report.read())["total_users"]

                Tkd.inform_user(
                    f"{color_text(user_id[:4],"green")}"
                    + color_text(
                        "... Generating user profile photo format=Baseb64 \n _________________________________________________________________________",
                        "dark_grey",
                    )
                )
                Tkd.hello()

                print(
                    color_text(
                        f"\n [-] Please wait ....                       "
                        + color_text(
                            f"{(total_users - inc)} students / {total_users} students",
                            "green",
                        )
                        + "\n",
                        "yellow",
                    )
                )

                progress.update((inc / total_users) * 100)

                await cards_report(user_id, status="completed")

            # Get content type
            content_type = response.headers.get("Content-Type", "image/jpeg")

            # CRITICAL: Read the content as bytes
            image_bytes = await response.read()

            # Check if we got data
            if not image_bytes:
                print(f"[*] Empty response for {image_url}")
                return None
            # cance the image to cache folder Folder: /tmp/cache_images
            await cache_image(user_id, image_bytes)

            # Encode to base64
            image_base64 = base64.b64encode(image_bytes).decode("utf-8")

            # Return as data URL
            return f"data:{content_type};base64,{image_base64}"

    except asyncio.TimeoutError:
        print(f"[*] Timeout fetching {image_url}")
        return None
    except aiohttp.ClientError as e:
        print(f"[*] Client error fetching {image_url}: {e}")
        return None
    except Exception as e:
        print(f"[*] Unexpected error fetching {image_url}: {e}")
        return None


async def single_image_url_to_base64(image_url: str, user_id: str) -> Optional[str]:
    """
    Fetch image from URL and convert to base64 data URL

    Args:
        image_url: URL of the image
        user_id: User ID for reporting

    Returns:
        Base64 encoded data URL or None if failed
    """
    try:
        # GET the image with timeout
        response = requests.get(image_url, timeout=200)

        # Check if request was successful
        if response.status_code != 200:
            print(f"[*] HTTP Error: {response.status_code} for {image_url}")
            await cards_report(user_id, status="completed")  # ❌ Can't use await here!
            return None

        # Get content type
        content_type = response.headers.get("Content-Type", "image/jpeg")

        # Get the content as bytes
        image_bytes = response.content

        # Check if we got data
        if not image_bytes:
            print(f"[*] Empty response for {image_url}")
            return None

        # Encode to base64
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")

        # Return as data URL
        return f"data:{content_type};base64,{image_base64}"

    except requests.Timeout:
        print(f"[*] Timeout fetching {image_url}")
        return None
    except requests.RequestException as e:
        print(f"[*] Request error fetching {image_url}: {e}")
        return None
    except Exception as e:
        print(f"[*] Unexpected error fetching {image_url}: {e}")
        return None


def clear_report_file():
    global _REPORT_CTR, _report_ctr_initialized
    _REPORT_CTR = 0
    _report_ctr_initialized = False
    try:
        open(_report_path(), "w", encoding="utf-8").write("[]")
        open(_report_path() + "l", "w", encoding="utf-8").close()
    except OSError:
        pass


if __name__ == "__main__":
    pass
