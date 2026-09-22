"""Git-style versioning for card templates.

When a new card design is ready, drop the updated ``front.svg`` / ``back.svg``
into the template folder, run ``commit`` and the script snapshots that design,
rebuilds the ``.card.kaascan`` files the generator reads and marks it as the
active version. Old designs stay under ``versions/`` and can be brought back at
any time with ``rollback`` — no need to keep rewriting the card by hand.

Usage
-----
    python3 -m card_sdk.template_versioning build BANK_INSPIRE
    python3 -m card_sdk.template_versioning commit BANK_INSPIRE --label spring-2026 --message "Spring rebrand"
    python3 -m card_sdk.template_versioning list
    python3 -m card_sdk.template_versioning current BANK_INSPIRE
    python3 -m card_sdk.template_versioning rollback BANK_INSPIRE --label v1

Layout
------
    templates_base/<template>/
        front.svg, back.svg              # working design (source of truth)
        front.card.kaascan, back...      # built from the svg above
        versions/
            .current                     # active label
            <label>/                     # one snapshot per label (like a branch)
                front.svg
                back.svg
                meta.json                # {label, message, created_at, by}
"""

import argparse
import json
import pathlib
import re
import sys
from datetime import datetime

try:
    from getpass import getuser as _getuser
except Exception:  # pragma: no cover - fallback for odd platforms
    _getuser = lambda: "kaascan"

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

base_dir = pathlib.Path(__file__).parent
templates_base = base_dir / "templates" / "templates_base"

# Variables the generator can fill on any template. Used only to warn when a
# freshly-dropped design is missing one of the standard placeholders, so cards
# never silently lose data.
STANDARD_PLACEHOLDERS = (
    "{name}",
    "{student_code}",
    "{student_class}",
    "{school_name}",
    "{student_id}",
    "{student_photo}",
    "{student_barcode}",
    "{valid_thru}",
    "{school_type}",
    "{stamp_base64}",
    "{signature_base64}",
    "{director_name}",
    "{director_contact}",
)


def _template_dir(template_name: str) -> pathlib.Path:
    template_dir = templates_base / template_name
    if not template_dir.is_dir():
        raise FileNotFoundError(f"template '{template_name}' not found in {templates_base}")
    return template_dir


def _versions_dir(template_dir: pathlib.Path) -> pathlib.Path:
    return template_dir / "versions"


def _label_dir(template_dir: pathlib.Path, label: str) -> pathlib.Path:
    if not _SAFE_LABEL.match(label):
        raise ValueError(
            f"label {label!r} is not safe: use letters, digits, '.', '_' or '-' "
            "(and it must not start with '.' or '-')"
        )
    return _versions_dir(template_dir) / label


def build_kaascan(svg_text: str) -> str:
    """Collapse an edited design SVG into the minified .card.kaascan form the
    generator reads (same shape CardFly's XMLSerializer produces)."""
    text = re.sub(r"<\?xml[^?]*\?>", "", svg_text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return " ".join(text.split())


def rebuild_kaascan(template_name: str) -> dict:
    """Rebuild front/back .card.kaascan from the working .svg files (source of
    truth). Returns which files were written and a list of missing standard
    placeholders that only appear once a generator fills them."""
    template_dir = _template_dir(template_name)
    written = []
    all_svg_text = ""
    for side, svg_name, kaascan_name in (
        ("front", "front.svg", "front.card.kaascan"),
        ("back", "back.svg", "back.card.kaascan"),
    ):
        svg_path = template_dir / svg_name
        if not svg_path.is_file():
            continue
        svg_text = svg_path.read_text(encoding="utf-8")
        (template_dir / kaascan_name).write_text(
            build_kaascan(svg_text), encoding="utf-8"
        )
        written.append(kaascan_name)
        all_svg_text += svg_text
    missing = sorted(
        placeholder
        for placeholder in STANDARD_PLACEHOLDERS
        if placeholder not in all_svg_text
    )
    return {"template": template_name, "written": written, "missing": missing}


def commit(template_name: str, label: str, message: str = "", force: bool = False) -> dict:
    """Snapshot the working design under ``versions/<label>`` and mark it active."""
    template_dir = _template_dir(template_name)
    target = _label_dir(template_dir, label)
    if target.exists() and not force:
        raise FileExistsError(
            f"version {label!r} already exists for {template_name} "
            f"(use --force to overwrite it)"
        )
    target.mkdir(parents=True, exist_ok=True)

    # 1) sync the .kaascan files to whatever design is currently in the folder,
    #    so a freshly-dropped SVG is immediately usable by the generator.
    rebuild = rebuild_kaascan(template_name)

    # 2) copy the design source files into the snapshot.
    snapshot_files = []
    for name in ("front.svg", "back.svg"):
        src = template_dir / name
        if src.is_file():
            (target / name).write_bytes(src.read_bytes())
            snapshot_files.append(name)

    meta = {
        "label": label,
        "message": message or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "by": _getuser(),
    }
    (target / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (_versions_dir(template_dir) / ".current").write_text(label, encoding="utf-8")

    return {
        "template": template_name,
        "label": label,
        "snapshot": str(target),
        "files": snapshot_files,
        "kaascan": rebuild["written"],
    }


def rollback(template_name: str, label: str) -> dict:
    """Restore the working design from a saved snapshot and sync .kaascan files."""
    template_dir = _template_dir(template_name)
    src = _label_dir(template_dir, label)
    if not src.is_dir():
        raise FileNotFoundError(
            f"version {label!r} not found for {template_name} "
            f"(see `list`) — nothing restored"
        )
    restored = []
    for name in ("front.svg", "back.svg"):
        version_file = src / name
        if version_file.is_file():
            (template_dir / name).write_bytes(version_file.read_bytes())
            restored.append(name)
    rebuild = rebuild_kaascan(template_name)
    (_versions_dir(template_dir) / ".current").write_text(label, encoding="utf-8")
    return {
        "template": template_name,
        "label": label,
        "restored": restored,
        "kaascan": rebuild["written"],
    }


def current(template_name: str) -> str | None:
    """Active version label for a template, or None when none committed yet."""
    pointer = _versions_dir(_template_dir(template_name)) / ".current"
    if pointer.is_file():
        value = pointer.read_text(encoding="utf-8").strip()
        return value or None
    return None


def list_versions(template_name: str | None = None) -> list[dict]:
    """All committed versions across templates (or one template) with metadata."""
    rows = []
    if template_name:
        dirs = [templates_base / template_name]
    else:
        dirs = [d for d in sorted(templates_base.iterdir()) if d.is_dir()]
    for template_dir in dirs:
        versions = _versions_dir(template_dir)
        if not versions.is_dir():
            continue
        active = (versions / ".current").read_text(encoding="utf-8").strip() or ""
        for vdir in sorted(versions.iterdir()):
            if not vdir.is_dir():
                continue
            meta = {}
            meta_path = vdir / "meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            rows.append(
                {
                    "template": template_dir.name,
                    "label": vdir.name,
                    "message": meta.get("message", ""),
                    "created_at": meta.get("created_at", ""),
                    "active": vdir.name == active,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("no committed versions yet.")
        return
    width = max(len(r["template"]) for r in rows)
    for row in rows:
        mark = "* " if row["active"] else "  "
        print(
            f"{mark}{row['template']:<{width}}  {row['label']:<18} "
            f"{row['created_at']}  {row['message']}"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="template_versioning",
        description="Git-style template design versioning: commit new designs, "
                    "roll back to old ones, rebuild the .kaascan cards apply.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="rebuild .kaascan from the folder's SVG files")
    p_build.add_argument("template")

    p_commit = sub.add_parser("commit", help="snapshot the current design as a version")
    p_commit.add_argument("template")
    p_commit.add_argument("--label", required=True, help="version/branch name, e.g. v1 or spring-2026")
    p_commit.add_argument("--message", default="", help="short description of this design")
    p_commit.add_argument("--force", action="store_true", help="overwrite an existing label")

    p_rollback = sub.add_parser("rollback", help="restore a saved design version")
    p_rollback.add_argument("template")
    p_rollback.add_argument("--label", required=True)

    p_cur = sub.add_parser("current", help="show the active version")
    p_cur.add_argument("template")

    p_list = sub.add_parser("list", help="list committed versions")
    p_list.add_argument("template", nargs="?")

    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            result = rebuild_kaascan(args.template)
            print(f"[+] rebuilt {result['template']}: {', '.join(result['written'])}")
            if result["missing"]:
                print(
                    f"[!] standard placeholders not in this design (card fields may stay "
                    f"empty until added): {', '.join(result['missing'])}"
                )
        elif args.command == "commit":
            result = commit(args.template, args.label, args.message, force=args.force)
            print(f"[+] committed {result['template']} -> {result['label']} ({result['snapshot']})")
            if result["kaascan"]:
                print(f"[+] synced {', '.join(result['kaascan'])}")
        elif args.command == "rollback":
            result = rollback(args.template, args.label)
            print(
                f"[+] restored {result['template']} -> {result['label']}: "
                f"{', '.join(result['restored'])} + {', '.join(result['kaascan'])}"
            )
        elif args.command == "current":
            print(current(args.template))
        elif args.command == "list":
            _print_list(list_versions(args.template))
    except Exception as error:
        print(f"[-] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())