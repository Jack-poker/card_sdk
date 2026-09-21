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
             "pa": "xMidYMid meet", "faces": "back"},
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
    overlay = (
        f'<image id="slot-{var}" x="{spec["x"]}" y="{spec["y"]}" '
        f'width="{spec["w"]}" height="{spec["h"]}" '
        f'preserveAspectRatio="{spec["pa"]}" data-format="{marker}"/>'
    )
    if "</svg>" not in svg:
        return svg, False
    svg = svg.replace("</svg>", overlay + "</svg>", 1)
    return svg, True


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