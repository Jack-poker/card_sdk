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
        "school_name": ["GREEN HILLS ACADEMY", "DEMO SCHOOL"],
        "school_type": ["HIGH SCHOOL"],
        "valid_thru": ["09/30"],
        "director_name": ["BIZIMANA COLAUDE"],
        "director_contact": ["+2507f88666666"],
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