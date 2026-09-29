"""Detect missing template variables and put them back.

Card templates are designed with finished-looking *demo text* on their visible
``<text>`` nodes, wired to the renderer through ``data-format="{var}"`` anchors
or classic ``{var}`` placeholders. An Inkscape save, a CardFly round-trip or a
re-export can strip that wiring, leaving the demo text frozen on every card.

This module re-attaches the anchors automatically so student data re-applies:

    python -m card_sdk.template_variables check              # report drift
    python -m card_sdk.template_variables check BANK_INSPIRE # one template
    python -m card_sdk.template_variables restore            # fix everything
    python -m card_sdk.template_variables restore BANK_INSPIRE

``restore_template_variables()`` is also called (best-effort) right before a
card renders, so a lost variable heals itself on any machine.
"""

import pathlib
import re
import sys

# Variable name -> visible demo text that occupies that slot. A template is
# "demo locked" when its visible node carries this text but no anchor, meaning
# the variable would never reach the card.
DEMO_SLOTS = {
    "BANK_INSPIRE": {
        "student_names": ["TUYISHIME EMMANUEL"],
        "student_class": ["LEVEL 5 SOFTWARE DEV"],
        "student_code": ["5298 7601 2345 6789"],
        "student_id": ["123455876732389"],
        "school_name": ["GREEN HILLS ACADEMY", "DEMO SCHOOL", "Lycee Nyaza"],
        "school_slogan": ["wisdom focused school"],
        "school_type": ["HIGH SCHOOL"],
        "valid_thru": ["09/30"],
        "director_name": ["BIZIMANA COLAUDE"],
        "director_contact": ["+2507f88666666"],
    },
}

# Image variables that must reach the card as real pictures. An image slot is
# *anchor* type — an existing ``<image>`` element that already carries its own
# geometry, just needs a ``data-format`` anchor so the renderer swaps the demo
# artwork for the real picture — or *overlay* type — a ``<image>`` that has to
# be added (the template has no visible box for it, e.g. stamp / signature).
#
# Slots are keyed by variable name so ``check``/``restore`` cover images exactly
# like they cover the demo text.
IMAGE_SLOTS = {
    "BANK_INSPIRE": {
        "student_barcode": [
            {"type": "anchor", "id": "barcode", "faces": "front,back"},
        ],
        "image_base64": [
            {"type": "anchor", "id": "image1-9", "faces": "front,back"},
        ],
"school_logo": [
            {"type": "overlay", "x": "18.48", "y": "4.0", "w": "14.95", "h": "14.95",
             "pa": "xMidYMid meet", "faces": "front", "replaces": "school_logo"},
        ],
        "stamp_base64": [
            {"type": "overlay", "x": "29.7", "y": "18.5", "w": "16.1", "h": "15.9",
             "pa": "xMidYMid meet", "faces": "back", "replaces": "stamp"},
        ],
        "signature_base64": [
            {"type": "overlay", "x": "40.5", "y": "26.2", "w": "6.6", "h": "9.8",
             "pa": "none", "faces": "back", "replaces": "signature"},
        ],
    },
}

# Baseline variables every card template should be able to fill.
# (Unused: only templates with a registered demo-slot map get precise checks —
# the generic key-list produced false positives on templates using other names.)
MINIMUM_VARIABLES = [
    "student_names",
    "student_class",
    "school_name",
]

_IMAGE_SLOT_MARKER = re.compile(r'id="slot-([A-Za-z_][\w]*)"')

base_dir = pathlib.Path(__file__).parent
TEMPLATES_BASE = base_dir / "templates" / "templates_base"

_TEXT_NODE = re.compile(r"<text\b[^>]*>.*?</text>", re.S)
_OPEN_TAG = re.compile(r"<\s*(text)\b[^>]*?>")
_ANCHOR = re.compile(r'data-format="\{([A-Za-z_][\w]*)\}"')
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][\w]*)\}")

# Placeholder-doodle boxes, memoised per (document, element id) — see _doodle_box.
_DOODLE_BOX_CACHE: dict = {}


def _inner_text(block: str) -> str:
    """The visible run of a ``<text>`` block, tags removed."""
    return re.sub(r"<[^>]+>", "", block).strip()


def _strip_attrs(block: str) -> str:
    """The open tag of a ``<text>`` block, stripped for comparisons."""
    m = _OPEN_TAG.match(block)
    return m.group(0) if m else ""


def _anchored_vars(svg: str) -> set:
    return set(_ANCHOR.findall(svg))


def _placeholder_vars(svg: str) -> set:
    return set(_PLACEHOLDER.findall(svg))


def template_files(template_name: str):
    folder = TEMPLATES_BASE / template_name
    return [folder / "front.card.kaascan", folder / "back.card.kaascan"]


def _image_slot_wired(svg: str, var: str) -> bool:
    """True when *var* lands on the card as a real image.

    An image slot counts as wired when a ``data-format="{var}"`` anchor sits on
    an ``<image>`` element or when the dedicated ``slot-<var>`` overlay marker
    is present (that marker is our own, never produced by an editor round-trip).
    """
    if f'data-format="{{{var}}}"' in svg:
        return True
    return any(m.group(1) == var for m in _IMAGE_SLOT_MARKER.finditer(svg))


def _ensure_anchor(svg: str, elem_id: str, var: str):
    """Attach ``data-format="{var}"`` to the existing ``<image id=elem_id>``."""
    tag = re.search(r"<image\b[^>]*\bid=\"" + re.escape(elem_id) + r"\"[^>]*>", svg)
    if not tag:
        # self-closing form
        tag = re.search(r"<image\b[^>]*\bid=\"" + re.escape(elem_id) + r"\"[^>]*/>", svg)
    if not tag:
        return svg, False
    block = tag.group(0)
    if _ANCHOR.search(block) or f' data-format="{{{var}}}"' in block:
        return svg, False
    if block.rstrip().endswith("/>"):
        new_block = block[: block.rfind("/>")] + f' data-format="{{{var}}}"/>'
    elif block.rstrip().endswith(">"):
        new_block = block.rstrip()[:-1] + f' data-format="{{{var}}}">'
    else:
        new_block = block + f' data-format="{{{var}}}"'
    return svg.replace(block, new_block, 1), True


def _ensure_overlay(svg: str, var: str, spec: dict):
    """Insert the ``<image data-format="{var}">`` overlay just before </svg>."""
    if any(m.group(1) == var for m in _IMAGE_SLOT_MARKER.finditer(svg)):
        return svg, False
    marker = "{" + var + "}"
    replaces = spec.get("replaces", "")
    overlay = (
        f'<image id="slot-{var}" x="{spec["x"]}" y="{spec["y"]}" '
        f'width="{spec["w"]}" height="{spec["h"]}" '
        f'preserveAspectRatio="{spec["pa"]}" data-replaces="{replaces}" '
        f'data-format="{marker}"/>'
    )
    if "</svg>" not in svg:
        return svg, False
    svg = svg.replace("</svg>", overlay + "</svg>", 1)
    return svg, True


def _path_bbox(d: str, transform: str = ""):
    """Drawn box ``(x0, y0, x1, y1)`` of an SVG path, in the element's own
    coordinate frame (its transform already applied).

    Editor exports use relative commands (``m``, ``c``, …) and a per-element
    ``transform``; taking raw min/max of the numbers would be meaningless, so
    the pen is walked in absolute coordinates. Returns ``None`` when nothing
    can be resolved so callers fall back to configured geometry.
    """
    import math

    tokens = re.findall(
        r'[MmZzLlHhVvCcSsQqTtAa]|[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?',
        d,
    )
    pts = []
    x = y = sx = sy = 0.0
    i, n, cmd = 0, len(tokens), None

    def num():
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    while i < n:
        t = tokens[i]
        if len(t) == 1 and t.isalpha():
            cmd = t; i += 1
            if cmd in "Zz":
                x, y = sx, sy
                pts.append((x, y))
                continue
        if cmd is None:
            i += 1
            continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            px, py = num(), num()
            x = x + px if rel else px
            y = y + py if rel else py
            sx, sy = x, y
            pts.append((x, y))
            cmd = "l" if rel else "L"
        elif c == "L":
            px, py = num(), num()
            x = x + px if rel else px
            y = y + py if rel else py
            pts.append((x, y))
        elif c == "H":
            px = num(); x = x + px if rel else px
            pts.append((x, y))
        elif c == "V":
            py = num(); y = y + py if rel else py
            pts.append((x, y))
        elif c == "C":
            x1, y1, x2, y2, px, py = (num() for _ in range(6))
            x1 = x + x1 if rel else x1; y1 = y + y1 if rel else y1
            x2 = x + x2 if rel else x2; y2 = y + y2 if rel else y2
            px = x + px if rel else px; py = y + py if rel else py
            pts += [(x1, y1), (x2, y2), (px, py)]
            x, y = px, py
        elif c == "S":
            x2, y2, px, py = (num() for _ in range(4))
            x2 = x + x2 if rel else x2; y2 = y + y2 if rel else y2
            px = x + px if rel else px; py = y + py if rel else py
            pts += [(x2, y2), (px, py)]
            x, y = px, py
        elif c == "Q":
            cx, cy, px, py = (num() for _ in range(4))
            cx = x + cx if rel else cx; cy = y + cy if rel else cy
            px = x + px if rel else px; py = y + py if rel else py
            pts += [(cx, cy), (px, py)]
            x, y = px, py
        elif c == "T":
            px, py = num(), num()
            px = x + px if rel else px; py = y + py if rel else py
            pts.append((px, py))
            x, y = px, py
        elif c == "A":
            vals = [num() for _ in range(7)]
            px, py = vals[5], vals[6]
            px = x + px if rel else px; py = y + py if rel else py
            pts.append((px, py))
            x, y = px, py
        else:
            i += 1
    if not pts:
        return None

    # Compose the element's transform (translate/scale/rotate/matrix) left to
    # right as SVG does: point' = M · point.
    def mul(M, N):
        a1, b1, c1, d1, e1, f1 = M
        a2, b2, c2, d2, e2, f2 = N
        return (
            a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1,
        )

    M = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for kind, raw in re.findall(r'(translate|scale|rotate|matrix)\s*\(([^)]*)\)', transform or ""):
        args = [float(v) for v in re.findall(
            r'[-+]?(?:\d+\.\d+|\.\d+|\d+)(?:[eE][-+]?\d+)?', raw)]
        if kind == "translate":
            op = (1, 0, 0, 1, args[0] if args else 0, args[1] if len(args) > 1 else 0)
        elif kind == "scale":
            sx_ = args[0] if args else 1
            op = (sx_, 0, 0, args[1] if len(args) > 1 else sx_, 0, 0)
        elif kind == "rotate":
            a = math.radians(args[0] if args else 0)
            R = (math.cos(a), -math.sin(a), math.sin(a), math.cos(a), 0, 0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                op = mul(mul((1, 0, 0, 1, cx, cy), R), (1, 0, 0, 1, -cx, -cy))
            else:
                op = R
        else:  # matrix
            op = tuple(args[:6]) if len(args) >= 6 else (1, 0, 0, 1, 0, 0)
        M = mul(M, op)

    a, b, c, dd, e, f = M
    tx = [a * px + c * py + e for px, py in pts]
    ty = [b * px + dd * py + f for px, py in pts]
    return (min(tx), min(ty), max(tx), max(ty))


def _doodle_box(svg: str, elem_id: str):
    """``(x, y, w, h)`` of the drawn placeholder ``<path id=elem_id>`` — the
    template's own geometry, so an overlay lands exactly where the design
    draws the stamp / signature / logo. Falls back to ``None`` if not found.

    Memoised on the document: walking a 200 KB placeholder path costs ~100 ms
    and the answer only depends on the template file, while every card render
    asks for the same three boxes.
    """
    key = (elem_id, len(svg), hash(svg))
    cached = _DOODLE_BOX_CACHE.get(key)
    if cached is not None:
        return cached[0]
    box = _compute_doodle_box(svg, elem_id)
    _DOODLE_BOX_CACHE[key] = (box,)
    return box


def _compute_doodle_box(svg: str, elem_id: str):
    m = re.search(
        r'<path\b[^>]*\bid="' + re.escape(elem_id) + r'"[^>]*?/?>', svg
    )
    if not m:
        return None
    d_m = re.search(r'\bd="([^"]*)"', m.group(0))
    if not d_m:
        return None
    tf_m = re.search(r'\btransform="([^"]*)"', m.group(0))
    box = _path_bbox(d_m.group(1), tf_m.group(1) if tf_m else "")
    if not box:
        return None
    x0, y0, x1, y1 = box
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _ensure_overlay_in_memory(svg: str, var: str, spec: dict):
    """Attach an ``<image data-format="{var}">`` overlay, in memory.

    Geometry comes from the template's own placeholder doodle (``spec
    ["replaces"]``) when present so the picture sits exactly where the design
    draws it; the configured spec box is the fallback. The overlay remembers
    the doodle it stands in for (``data-replaces``): the renderer drops the
    doodle only when a real picture is supplied, so a card without a stamp
    still shows the template's own art. The file on disk is never touched.
    """
    if any(m.group(1) == var for m in _IMAGE_SLOT_MARKER.finditer(svg)):
        return svg, False
    if f'data-format="{{{var}}}"' in svg and re.search(
        r'<image\b[^>]*data-format="\{' + re.escape(var) + r'\}"', svg
    ):
        return svg, False
    replaces = spec.get("replaces", "")
    box = _doodle_box(svg, replaces) if replaces else None
    if box:
        x, y, w, h = (f"{v:.6g}" for v in box)
    else:
        x, y, w, h = spec.get("x"), spec.get("y"), spec.get("w"), spec.get("h")
    overlay = (
        f'<image id="slot-{var}" x="{x}" y="{y}" '
        f'width="{w}" height="{h}" '
        f'preserveAspectRatio="{spec.get("pa", "xMidYMid meet")}" '
        f'data-replaces="{replaces}" data-format="{{{var}}}"/>'
    )
    if "</svg>" not in svg:
        return svg, False
    svg = svg.replace("</svg>", overlay + "</svg>", 1)
    return svg, True


def _restore_image_slots_in_memory(svg: str, template_name: str, face=None):
    """Wire every image variable of *face* into *svg* without writing files."""
    slots = IMAGE_SLOTS.get(template_name, {}) or {}
    for var, specs in slots.items():
        for spec in specs:
            if "faces" not in spec:
                continue
            faces = [f.strip() for f in (spec.get("faces") or "").split(",")]
            if face and face not in faces:
                continue
            if _image_slot_wired(svg, var):
                continue
            if spec.get("type") == "anchor":
                svg, _ = _ensure_anchor(svg, spec["id"], var)
            else:
                svg, _ = _ensure_overlay_in_memory(svg, var, spec)
    return svg


def _restore_image_slots(template_name: str) -> list:
    """Re-wire the template's image variables (image_base64, student_barcode,
    school_logo, stamp_base64, signature_base64). Idempotent."""
    slots = IMAGE_SLOTS.get(template_name, {}) or {}
    restored = []
    for var, specs in slots.items():
        for spec in specs:
            if "faces" not in spec:
                continue
            faces = [f.strip() for f in (spec["faces"] or "").split(",")]
            for face in faces:
                path = pathlib.Path(f"{TEMPLATES_BASE}/{template_name}/{face}.card.kaascan")
                if not path.exists():
                    continue
                svg = path.read_text(encoding="utf-8")
                changed = False
                if not _image_slot_wired(svg, var):
                    if spec["type"] == "anchor":
                        svg, changed = _ensure_anchor(svg, spec["id"], var)
                    else:
                        svg, changed = _ensure_overlay(svg, var, spec)
                    if changed:
                        path.write_text(svg, encoding="utf-8")
                        if var not in restored:
                            restored.append(var)
                # The real image replaces the template's placeholder doodle, so
                # drop the drawn element (e.g. <path id="signature">) — otherwise
                # both show (the image is often transparent).
                if spec.get("replaces"):
                    svg = path.read_text(encoding="utf-8")
                    _delete_elements(svg, spec["replaces"], path)
    return restored


def _delete_elements(svg: str, elem_id: str, path) -> None:
    """Remove all self-closing elements carrying an id (e.g. a drawn doodle)."""
    pat = re.compile(
        r"<[a-zA-Z]+\b[^>]*?\bid=\"" + re.escape(elem_id) + r"\"" + r"[^>]*?/>"
    )
    new, n = pat.subn("", svg)
    if n:
        path.write_text(new, encoding="utf-8")


def check_template(template_name: str) -> list:
    """Variables that would never reach the card (empty list == healthy).

    Only templates with a registered demo-slot map undergo precise checking:
    the visible demo text must sit on a node carrying an anchor or a
    placeholder. Templates without a slot map have no known demo text to
    compare against, so nothing is reported (render smoke-tests cover them).
    """
    slots = DEMO_SLOTS.get(template_name, {})
    if not slots:
        return []
    missing = []
    for var, demos in slots.items():
        wired_block = False
        present = False
        for path in template_files(template_name):
            if not path.exists():
                continue
            for block in _TEXT_NODE.findall(path.read_text(encoding="utf-8")):
                if _inner_text(block) not in demos:
                    continue
                present = True
                # Demo slot is fine when the block carries an anchor OR the
                # placeholder was substituted on it (e.g. classic {var}).
                if _ANCHOR.search(block) or _placeholder_vars(block):
                    wired_block = True
        if not present or not wired_block:
            missing.append(var)
    for var in IMAGE_SLOTS.get(template_name, {}) or {}:
        if not any(
            _image_slot_wired(path.read_text(encoding="utf-8"), var)
            for path in template_files(template_name)
            if path.exists()
        ):
            missing.append(var)
    return missing


def _attach_demo_anchors(svg: str, slots: dict) -> str:
    """Attach ``data-format="{var}"`` to every demo-locked ``<text>`` node.

    One regex pass over the document, dispatching each node to its variable
    through a demo-text lookup. Scanning once per *variable* (or rewriting each
    block with ``str.replace``) copies the whole 200 KB template repeatedly and
    was the single biggest cost of rendering a card. The open tag is the only
    thing that changes, so it is rebuilt in place and every other byte is left
    exactly as authored.
    """
    by_demo_text: dict = {}
    for var, demos in slots.items():
        for demo in demos:
            by_demo_text.setdefault(demo, var)  # first var wins, as before
    if not by_demo_text:
        return svg

    def _repl(m):
        block = m.group(0)
        text = _inner_text(block)
        if not text:
            return block
        var = by_demo_text.get(text)
        if not var:
            return block  # not a demo slot
        if _ANCHOR.search(block) or _PLACEHOLDER.search(block):
            return block  # already wired: nothing to restore
        open_m = _OPEN_TAG.match(block)
        if not open_m:
            return block
        open_tag = open_m.group(0)
        anchor = f' data-format="{{{var}}}"'
        if anchor in open_tag:
            return block
        return open_tag[:-1] + anchor + ">" + block[len(open_tag):]

    return _TEXT_NODE.sub(_repl, svg)


def restore_svg_variables(svg: str, template_name: str, face: str = None) -> str:
    """In-memory, structure-preserving re-wiring of a template face.

    Returns a patched SVG *string*; the template file on disk is never touched.
    Two kinds of healing happen here:

    * demo-locked ``<text>`` nodes — a ``data-format`` attribute is added to an
      unwired open tag (layout untouched);
    * image slots — existing pictures get their anchor, and stamp / signature
      / school-logo overlays are attached at the placeholder doodle's own
      geometry (``data-replaces`` remembers the doodle so it is only dropped
      when a real picture is supplied).

    No coordinate is rewritten and no element other than the stand-in doodle
    (image present only) is removed, so the pushed design keeps its layout.

    Used by the card renderer (``generate_card``); the disk-mutating CLI
    ``restore`` is a separate, explicit maintenance action.
    """
    slots = DEMO_SLOTS.get(template_name, {})
    if slots:
        svg = _attach_demo_anchors(svg, slots)
    return _restore_image_slots_in_memory(svg, template_name, face)


def restore_template_variables(template_name: str) -> list:
    """Re-attach ``data-format`` anchors to demo-locked visible text nodes.

    Returns the list of variable anchors restored. Idempotent: blocks that
    already carry an anchor or a placeholder are left untouched.
    """
    slots = DEMO_SLOTS.get(template_name, {})
    if not slots:
        return []
    restored = []
    for path in template_files(template_name):
        if not path.exists():
            continue
        svg = path.read_text(encoding="utf-8")
        changed = False
        for var, demos in slots.items():
            for block in _TEXT_NODE.findall(svg) or []:
                text = _inner_text(block)
                if text not in demos or text == "":
                    continue
                if _ANCHOR.search(block) or _PLACEHOLDER.search(block):
                    continue  # already wired: nothing to restore
                anchor = f' data-format="{{{var}}}"'
                if anchor in _strip_attrs(block):
                    continue
                open_tag = _OPEN_TAG.match(block).group(0)
                new_open = open_tag[:-1] + anchor + ">"
                svg = svg.replace(block, new_open + block[len(open_tag):], 1)
                changed = True
                if var not in restored:
                    restored.append(var)
        if changed:
            path.write_text(svg, encoding="utf-8")
    for var in _restore_image_slots(template_name):
        if var not in restored:
            restored.append(var)
    return restored


def check_all() -> dict:
    problems = {}
    for folder in sorted(TEMPLATES_BASE.iterdir()):
        if folder.is_dir() and (folder / "front.card.kaascan").exists():
            missing = check_template(folder.name)
            if missing:
                problems[folder.name] = missing
    return problems


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    action = argv[0] if argv else "check"
    targets = argv[1:] or None
    if action not in ("check", "restore"):
        print(f"usage: python -m card_sdk.template_variables <check|restore> [TEMPLATE ...]")
        return 2
    names = targets or [f.name for f in sorted(TEMPLATES_BASE.iterdir())
                        if f.is_dir() and (f / "front.card.kaascan").exists()]
    exit_code = 0
    for name in names:
        if action == "check":
            missing = check_template(name)
            if missing:
                exit_code = 1
                print(f"[MISSING] {name}: {', '.join(missing)}")
            else:
                print(f"[ok] {name}: all expected variables wired")
        else:
            restored = restore_template_variables(name)
            print(f"[restored] {name}: {', '.join(restored) if restored else 'none needed'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())