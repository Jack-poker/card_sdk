#!/usr/bin/env python3
"""Inkscape extension: push the current design to a Kaascan SDK template (or
load a template design back into the canvas).

Two menu entries are defined by kaascan_push.inx / kaascan_load.inx; they
share this script and differ only by the hidden ``--mode`` argument.

Push mode
    Writes the current document to ``card_sdk/templates/templates_base/``
    as ``<template>/front.svg`` (or ``back.svg``), rebuilds that side's
    ``.card.kaascan`` (the minified file the generator actually reads), and
    optionally commits a version snapshot so old designs stay restorable.

Load mode
    Replaces the current document with ``<template>/<side>.svg`` so a saved
    design can be opened again for editing.
"""

import copy
import datetime
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys

import inkex
from lxml import etree
from inkex import TextElement, load_svg

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SODIPODI_NS = "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}"
SODIPODI_OLD_NS = "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.0.dtd}"
INKSCAPE_NS = "{http://www.inkscape.org/namespaces/inkscape}"

CONFIG_DIR = pathlib.Path(
    os.environ.get("XDG_CONFIG_HOME", "~/.config")
).expanduser() / "kaascan"
CONFIG_PATH = CONFIG_DIR / "inkscape_push.json"

IMAGE_PLACEHOLDERS = {
    "student_photo": (120, 150),
    "student_barcode": (200, 60),
    "stamp_base64": (90, 90),
    "signature_base64": (120, 40),
    "school_logo": (60, 60),
}

# Placeholders that normally belong to each side (used for the side-aware
# warning + the optional insert-as-objects hint).
SIDE_PLACEHOLDERS = {
    "front": [
        "name", "student_code", "student_class", "school_name",
        "valid_thru", "school_type", "student_photo", "school_logo",
    ],
    "back": [
        "name", "student_id", "school_name", "student_barcode",
        "stamp_base64", "signature_base64", "director_name", "director_contact",
    ],
}

KNOWN_SDK_HINTS = (
    "KACCAN_SDK_ROOT",
    "KAASCAN_SDK_ROOT",
    "GEN_CARD_SDK",
)


def _config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(patch: dict) -> None:
    data = _config()
    data.update(patch)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def is_sdk_root(path: pathlib.Path) -> bool:
    return (
        (path / "card_sdk" / "template_versioning.py").is_file()
        and (path / "card_sdk" / "templates" / "templates_base").is_dir()
    )


def _paths_to_root(start: pathlib.Path):
    """Yield ``start`` and each parent up to the filesystem root."""
    current = start
    while True:
        yield current
        if current.parent == current:
            break
        current = current.parent


def find_sdk_root(explicit: str = "") -> pathlib.Path:
    """Return the SDK repo folder that contains card_sdk/, or raise. An
    explicitly provided path is authoritative: it fails instead of silently
    falling back to remembered/discovered folders. A deeper path (e.g. one
    that points at templates_base/) is walked upwards until the SDK root is
    found."""
    if explicit and explicit.strip():
        start = pathlib.Path(explicit.strip()).expanduser()
        if not (start.is_dir() and is_sdk_root(start)):
            for candidate in _paths_to_root(start):
                if candidate.is_dir() and is_sdk_root(candidate):
                    start = candidate
                    break
            else:
                start = None
        if start is not None:
            return start
        raise inkex.AbortExtension(
            f"SDK folder not found at {explicit} — expected a folder that "
            "contains card_sdk/templates/templates_base/."
        )
    candidates = []
    cfg = _config()
    if cfg.get("sdk_root"):
        candidates.append(pathlib.Path(cfg["sdk_root"]).expanduser())
    for key in KNOWN_SDK_HINTS:
        value = os.environ.get(key)
        if value:
            candidates.append(pathlib.Path(value).expanduser())
    home = pathlib.Path.home()
    for rel in (
        "Documents/Q_PROJECTS/GEN_CARD/sdk",
        "Documents/GEN_CARD/sdk",
        "GEN_CARD/sdk",
    ):
        candidates.append(home / rel)
    for candidate in candidates:
        walked = None
        for node in _paths_to_root(candidate):
            if node.is_dir() and is_sdk_root(node):
                walked = node
                break
        if walked is not None:
            return walked
    hint = f"  (tried: {', '.join(str(c) for c in candidates)})"
    raise inkex.AbortExtension(
        "Could not locate the Kaascan SDK repo.\n"
        "Set the 'SDK repo folder' box in this dialog, or set the "
        f"KAASCAN_SDK_ROOT environment variable.{hint}"
    )


def load_versioning(sdk_root: pathlib.Path):
    """Import card_sdk/template_versioning.py without pulling in the rest of
    the SDK (Inkscape's python doesn't have the card rendering deps)."""
    module_path = sdk_root / "card_sdk" / "template_versioning.py"
    spec = importlib.util.spec_from_file_location("kaascan_templates_tv", module_path)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(module_path)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _is_editor_tag(tag) -> bool:
    return (
        isinstance(tag, str)
        and (tag.startswith(SODIPODI_NS) or tag.startswith(SODIPODI_OLD_NS) or tag.startswith(INKSCAPE_NS))
    )


_CSS_INKSCAPE_RE = re.compile(r";?\s*-inkscape-[A-Za-z-]+:\s*[^;]*;?")


def clean_svg(root, keep_pages: bool = False) -> None:
    """Strip Inkscape editor-only markup, leaving a clean SVG that still maps
    1:1 to the generator's placeholder substitution."""
    for element in list(root.iter()):
        if element.tag is etree.Comment:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
            continue
        if _is_editor_tag(element.tag) and not (
            keep_pages and element.tag == f"{INKSCAPE_NS}page"
):
            parent = element.getparent()
            if parent is not None:
                if keep_pages and element.tag == f"{SODIPODI_NS}namedview":
                    for child in [c for c in element]:
                        if child.tag == f"{INKSCAPE_NS}page":
                            element.remove(child)
                            parent.append(child)
                parent.remove(element)
            continue
    for element in list(root.iter(f"{{{SVG_NS}}}metadata")):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    for element in root.iter():
        for attribute in [a for a in element.attrib if _is_editor_tag(a)]:
            del element.attrib[attribute]
        style = element.get("style")
        if style and "-inkscape-" in style:
            element.set("style", _CSS_INKSCAPE_RE.sub(";", style).strip(";").strip())
    etree.cleanup_namespaces(root, top_nsmap={None: SVG_NS})


def _placeholder_names(svg_text: str) -> set:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", svg_text))


def _svg_stats(svg_text: str) -> dict:
    """Lightweight fingerprint of a template file, used for the git-like diff."""
    return {
        "bytes": len(svg_text.encode("utf-8")),
        "placeholders": sorted(_placeholder_names(svg_text)),
        "images": len(re.findall(r"<image\b", svg_text)),
        "rasters": svg_text.count("data:image"),
        "tags": svg_text.count("<"),
    }


def diff_summary(old: str, new: str, side: str) -> list:
    """Git-like ``diff --stat`` lines comparing the existing template file with
    what is about to be written."""
    if old is None:
        stats = _svg_stats(new)
        return [
            f"  diff      : new file — no previous {side}.svg",
            f"  + placeholders : {', '.join(stats['placeholders']) or 'none'}",
            f"  + images   : {stats['images']} (embedded raster {stats['rasters']})",
        ]
    before, after = _svg_stats(old), _svg_stats(new)
    delta = after["bytes"] - before["bytes"]
    lines = [
        f"  diff      : {side}.svg  {before['bytes']} -> {after['bytes']} bytes "
        f"({'+' if delta >= 0 else ''}{delta})",
        f"  ~ elements: {before['tags']} -> {after['tags']}",
        f"  ~ images  : {before['images']} -> {after['images']}  "
        f"(embedded raster {before['rasters']} -> {after['rasters']})",
    ]
    removed = set(before["placeholders"]) - set(after["placeholders"])
    added = set(after["placeholders"]) - set(before["placeholders"])
    if removed:
        lines.append(f"  - placeholders removed : {', '.join(sorted(removed))}")
    if added:
        lines.append(f"  + placeholders added   : {', '.join(sorted(added))}")
    if not removed and not added:
        lines.append("  ~ placeholders: unchanged")
    return lines


def svg_bytes(root) -> bytes:
    return etree.tostring(
        root, encoding="utf-8", xml_declaration=True, pretty_print=True
    )


def _write_preview(template: str, side: str, svg_text: str) -> pathlib.Path:
    """Render exactly what a push would store, to a preview file the design is
    then opened against, so nothing is exported before the design is checked."""
    name = f"{template}-{side}.svg"
    path = CONFIG_DIR / "previews" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg_text, encoding="utf-8")
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    return path


class KaascanTemplates(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--tab", dest="tab", default="target")
        pars.add_argument("--mode", default="push", help="push or load")
        pars.add_argument("--template", default="", help="template folder name")
        pars.add_argument(
            "--side", default="front", choices=["front", "back"],
            help="which card side this document represents",
        )
        pars.add_argument(
            "--page", default="",
            help="document page to export (id, label or number; empty = side-"
                 "aware auto-detect; 'all' = whole document)",
        )
        pars.add_argument(
            "--preview", dest="preview", type=inkex.Boolean, default=False,
            help="render the would-be push to a preview file and update the "
                 "canvas, without writing to the SDK",
        )
        pars.add_argument(
            "--sdk_root", "--sdk-root", dest="sdk_root", default="",
            help="sdk repo folder (empty = remembered/auto)",
        )
        pars.add_argument(
            "--clean", type=inkex.Boolean, default=True,
            help="strip Inkscape editor-only markup before pushing",
        )
        pars.add_argument(
            "--add_placeholders", "--add-placeholders", dest="add_placeholders",
            type=inkex.Boolean, default=False,
            help="insert missing standard placeholders as objects to position",
        )
        pars.add_argument(
            "--snapshot_label", "--snapshot-label", dest="snapshot_label",
            default="", help="version snapshot label (empty = no snapshot)",
        )
        pars.add_argument(
            "--snapshot_message", "--snapshot-message", dest="snapshot_message",
            default="", help="version snapshot message",
        )

    def effect(self):
        self._clean = self.options.clean is None or bool(self.options.clean)
        self._add_placeholders = bool(self.options.add_placeholders)
        if self.options.mode.strip().lower() == "load":
            result = self.load_design()
        else:
            result = self.push_design()
        inkex.utils.debug("\n".join(result))

    # ---------------- setup helpers ----------------

    def _resolve(self):
        options = self.options
        template = (options.template or self._last_template() or "BANK_INSPIRE").strip()
        side = options.side.strip().lower()
        if side not in ("front", "back"):
            raise inkex.AbortExtension(f"side must be 'front' or 'back', got {side!r}")
        sdk_root = find_sdk_root(options.sdk_root)
        tv = load_versioning(sdk_root)
        templates_base = sdk_root / "card_sdk" / "templates" / "templates_base"
        template_dir = templates_base / template
        return sdk_root, tv, template, side, template_dir

    def _last_template(self) -> str:
        return str(_config().get("last_template", "") or "")

    def _missing_placeholders(self, tv, svg_text: str, side: str = "") -> list:
        present = _placeholder_names(svg_text)
        expected = [
            p.strip("{}")
            for p in (SIDE_PLACEHOLDERS.get(side, ()) or tv.STANDARD_PLACEHOLDERS)
        ]
        return [name for name in expected if name not in present]

    def _current_layer(self, root):
        """Locate the editing layer inside the working copy of the document
        (plain lxml), falling back to the svg root. ``self.svg`` is the
        original tree, so we resolve by id inside ``root`` instead."""
        import warnings

        current = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                current = self.svg.get_current_layer()
            except Exception:
                current = None
        if current is None:
            return root
        current_id = current.get("id")
        if current_id:
            found = root.find(f".//{{{SVG_NS}}}g[@id='{current_id}']")
            if found is not None:
                return found
        return root

    def _insert_placeholders(self, tv, root, side: str = ""):
        """Add any missing standard placeholder as a text/image object so the
        designer can drag it into place (Inkscape will render a 'missing image'
        for the href placeholders until a card is generated)."""
        svg_text = etree.tostring(root, encoding="unicode")
        missing = self._missing_placeholders(tv, svg_text, side)
        return self._insert_placeholder_objects(root, missing)

    def _insert_placeholder_objects(self, root, names, hidden=False):
        """Place text/image objects for the given placeholder names into the
        active layer of the working copy, so the pushed design keeps them
        visible and positionable. When ``hidden`` is set the objects are kept
        in the file (so the placeholders still substitute on export) but are
        not drawn on the canvas."""
        layer = self._current_layer(root)
        x, y, step = 20.0, 20.0, 45.0
        for name in names:
            if name in IMAGE_PLACEHOLDERS:
                width, height = IMAGE_PLACEHOLDERS[name]
                image = etree.SubElement(
                    layer, f"{{{SVG_NS}}}image", x=str(x), y=str(y),
                    width=str(width), height=str(height),
                )
                image.set(f"{{{XLINK_NS}}}href", "{" + name + "}")
                image.set("id", f"kaascan-placeholder-{name}")
                if hidden:
                    image.set("style", "display:none")
            else:
                text = TextElement(x=str(x), y=str(y + 30))
                text.text = "{" + name + "}"
                text.style["font-size"] = "36px"
                text.style["fill"] = "#000000"
                text.style["font-family"] = "sans-serif"
                text.set("id", f"kaascan-placeholder-{name}")
                if hidden:
                    text.style["display"] = "none"
                layer.append(text)
            y += step
        return names

    # ---------------- push ----------------

    def _page_label(self, page) -> str:
        return (
            page.get(f"{INKSCAPE_NS}label")
            or page.get("id")
            or ""
        )

    def _select_page(self, root):
        """Return the working root for the push. When the document has page
        containers the requested page is exported (id/label/number), the side's
        own page when none is requested (front=pos 1 / label 'front', back=pos
        2 / label 'back'), the active page as a last resort, or 'all' for the
        whole document."""
        namedview = next(
            (e for e in root.iter() if e.tag == f"{SODIPODI_NS}namedview"),
            None,
        )
        pages = (
            [e for e in namedview if e.tag == f"{INKSCAPE_NS}page"]
            if namedview is not None
            else []
        )
        if not pages:
            return (
                root,
                ["  pages     : no page containers — pushing whole document"],
                True,
            )
        info = ", ".join(
            f"{self._page_label(p) or 'page'}(pos {i + 1})"
            for i, p in enumerate(pages)
        )
        wants = (self.options.page or "").strip()
        if wants.lower() in ("all", "whole", "document"):
            return (
                root,
                [f"  pages     : pushing whole document — pages: {info}", "  (pass a page id/label/number in the dialog to push one page only)"],
                True,
            )
        active_id = (namedview.get("page") or pages[0].get("id") or "").strip()
        side = (self.options.side or "").strip().lower()
        target = None
        reason = ""
        if wants:
            for i, page in enumerate(pages, 1):
                if wants in (page.get("id") or "", self._page_label(page)) or wants == str(i):
                    target = page
                    reason = f"requested '{wants}'"
                    break
        if target is None and not wants and side:
            for i, page in enumerate(pages, 1):
                label = (self._page_label(page) or "").lower()
                pid = (page.get("id") or "").lower()
                if (
                    side in label
                    or side in pid
                    or (side == "front" and i == 1)
                    or (side == "back" and i == 2)
                ):
                    target = page
                    reason = f"side '{side}'"
                    break
        if target is None and not wants:
            target = next(
                (p for p in pages if (p.get("id") or "") == active_id),
                pages[0],
            )
            reason = "active page"
        if target is None:
            raise inkex.AbortExtension(
                f"No page matches {wants!r} in this document. "
                f"Available pages: {info}"
            )
        label = self._page_label(target)
        if any(True for _ in target):
            newroot = self._export_page(root, target, include_root_artwork=False)
            return (
                newroot,
                [
                    f"  pages     : {reason} -> exporting page '{label}' "
                    f"(document pages: {info})"
                ],
                False,
            )
        newroot = self._export_page(root, target, include_root_artwork=True)
        return (
            newroot,
            [
                f"  pages     : page '{label}' has no content — exporting the "
                f"rest of the document, aligned to this page (pages: {info})",
                "  (artwork drawn outside the pages is only included when the "
                "page itself is empty)",
            ],
            False,
        )

    @staticmethod
    def _page_number(value) -> float:
        try:
            return float(re.sub(r"[^0-9.+\-]", "", str(value)))
        except Exception:
            return 0.0

    def _export_page(self, root, page, include_root_artwork: bool = True):
        """Build a standalone svg element carrying the requested page. Shared
        defs are always copied untranslated. When the page has its own content
        it is exported alone; otherwise the root-level artwork is exported too.
        Either way the page offset is baked into each copied node's transform
        so the chosen page's region lines up with the export's viewBox
        (renderer-independent, no wrapper group needed)."""
        width = self._page_number(page.get("width"))
        height = self._page_number(page.get("height"))
        if width <= 0 or height <= 0:
            raise inkex.AbortExtension(
                "selected page has no usable size (attributes width/height)."
            )
        x, y = self._page_number(page.get("x")), self._page_number(page.get("y"))
        dx, dy = self._page_number(-x), self._page_number(-y)
        newroot = etree.Element(
            f"{{{SVG_NS}}}svg", nsmap={None: SVG_NS, "xlink": XLINK_NS}
        )
        newroot.set("width", f"{width:g}mm")
        newroot.set("height", f"{height:g}mm")
        newroot.set("viewBox", f"0 0 {width:g} {height:g}")

        def baked(node, node_x, node_y):
            node = copy.deepcopy(node)
            node_x = 0.0 if node_x == 0 else node_x
            node_y = 0.0 if node_y == 0 else node_y
            if node_x or node_y:
                orig = node.get("transform")
                node.set(
                    "transform",
                    f"translate({node_x:g} {node_y:g})"
                    + (f" {orig}" if orig else ""),
                )
            return node

        for child in list(root):
            if child.tag is etree.Comment:
                continue
            if child.tag == f"{INKSCAPE_NS}page":
                continue
            if child.tag in (
                f"{SODIPODI_NS}namedview",
                f"{{{SVG_NS}}}metadata",
            ) or _is_editor_tag(child.tag):
                continue
            if child.tag == f"{{{SVG_NS}}}defs":
                newroot.append(copy.deepcopy(child))
            elif include_root_artwork:
                newroot.append(baked(child, dx, dy))
        for child in list(page):
            newroot.append(baked(child, dx, dy))
        return newroot

    def _auto_backup(self, tv, template, side, template_dir, previous_text: str):
        """Keep a safe copy of the design we are about to overwrite, so any push
        (even a bad one) can be rolled back."""
        stamp = datetime.datetime.now().strftime("auto-%Y%m%d-%H%M%S-%f")
        try:
            result = tv.commit(
                template, stamp, "auto (safety) snapshot before push", force=False
            )
            return stamp, f"committed via template_versioning -> {result['snapshot']}"
        except FileNotFoundError as error:
            backup = template_dir / "versions" / stamp
            backup.mkdir(parents=True, exist_ok=True)
            (backup / f"{side}.svg").write_text(previous_text, encoding="utf-8")
            return stamp, f"side-only copy at versions/{stamp}/{side}.svg"
        except Exception as error:  # keep the push, note the snapshot failure
            return None, f"skipped ({error})"

    def push_design(self):
        options = self.options
        sdk_root, tv, template, side, template_dir = self._resolve()
        root = etree.fromstring(etree.tostring(self.document))

        root, pages_msg, pushed_all = self._select_page(root)
        if self._clean:
            clean_svg(root, keep_pages=pushed_all)
        svg_text = svg_bytes(root).decode("utf-8")

        target = template_dir / f"{side}.svg"
        previous_text = None
        if target.is_file():
            previous_text = target.read_text(encoding="utf-8")

        protect = []
        if previous_text is not None:
            protect = sorted(
                _placeholder_names(previous_text) - _placeholder_names(svg_text)
            )
        if protect:
            self._insert_placeholder_objects(root, protect, hidden=True)
            svg_text = svg_bytes(root).decode("utf-8")

        if bool(self.options.preview):
            self.document = load_svg(io.BytesIO(svg_bytes(root)))
            self.svg = self.document.getroot()
            preview_path = _write_preview(template, side, svg_text)
            missing = self._missing_placeholders(tv, svg_text, side)
            report = [
                "Kaascan · PREVIEW — nothing exported yet",
                f"  template : {template}/{side}.svg",
                f"  sdk repo : {sdk_root}",
                f"  preview  : {preview_path}",
            ]
            report += pages_msg
            report.append(
                f"  placeholders present : {len(_placeholder_names(svg_text))}"
            )
            if protect:
                report.append(
                    "  protecting SDK placeholders not in this push: "
                    + ", ".join(protect)
                )
            report += diff_summary(previous_text, svg_text, side)
            if missing:
                report.append(
                    f"  missing standard placeholders: {', '.join(missing)}"
                )
            if not _placeholder_names(svg_text):
                report.append(
                    "  !! WARNING: no {placeholder} found — this looks like a "
                    "rendered card, not the template design."
                )
            report.append(
                "  happy with it? re-run the push with the 'preview' option "
                "OFF to export."
            )
            return report

        safety = None
        if previous_text is not None and previous_text != svg_text:
            safety = self._auto_backup(tv, template, side, template_dir, previous_text)

        template_dir.mkdir(parents=True, exist_ok=True)
        (template_dir / f"{side}.svg").write_text(svg_text, encoding="utf-8")
        (template_dir / f"{side}.card.kaascan").write_text(
            tv.build_kaascan(svg_text), encoding="utf-8"
        )

        missing = self._missing_placeholders(tv, svg_text, side)
        inserted = []
        if self._add_placeholders:
            inserted = self._insert_placeholders(tv, root, side)

        # bring the canvas in sync with what was actually pushed
        self.document = load_svg(io.BytesIO(svg_bytes(root)))
        self.svg = self.document.getroot()

        save_config({"sdk_root": str(sdk_root), "last_template": template})

        report = [
            "Kaascan · design pushed",
            f"  template : {template}/{side}.svg",
            f"  sdk repo : {sdk_root}",
            f"  wrote    : {side}.svg + {side}.card.kaascan",
        ]
        report += pages_msg
        report.append(
            f"  placeholders present : {len(_placeholder_names(svg_text))}"
        )
        if protect:
            report.append(
                "  protected SDK placeholders that were not in this push: "
                + ", ".join(protect)
            )
        report += diff_summary(previous_text, svg_text, side)
        if missing:
            report.append(
                f"  missing standard placeholders: {', '.join(missing)}"
            )
        if inserted:
            report.append(
                "  added placeholder objects (move them into place): "
                + ", ".join(inserted)
            )

        if not _placeholder_names(svg_text):
            report.append(
                "  !! WARNING: no {placeholder} found — this looks like a "
                "rendered card, not the template design."
            )
            if safety and safety[0]:
                report.append(
                    "  !! The previous design is safe in snapshot "
                    f"'{safety[0]}'; restore it with:"
                )
                report.append(
                    "     python3 -m card_sdk.template_versioning rollback "
                    f"{template} {safety[0]}"
                )
        if safety and safety[0]:
            report.append(
                "  snapshot   : auto '" + safety[0]
                + "' — replaced design preserved (" + safety[1] + ")"
            )

        if options.snapshot_label:
            label = options.snapshot_label.strip()
            message = options.snapshot_message.strip()
            try:
                result = tv.commit(template, label, message, force=False)
                report.append(
                    f"  snapshot   : {template} -> {label} "
                    f"({result['snapshot']})"
                )
            except FileExistsError as error:
                raise inkex.AbortExtension(
                    f"{error}\nRe-run with a different label, or remove/replace "
                    "the existing snapshot manually."
                )
            except Exception as error:
                report.append(f"  snapshot   : '{label}' skipped ({error})")
        elif not (safety and safety[0]):
            report.append("  snapshot   : none (this file was new/unchanged)")
        return report

    # ---------------- load ----------------

    def load_design(self):
        sdk_root, tv, template, side, template_dir = self._resolve()
        source = template_dir / f"{side}.svg"
        if not source.is_file():
            raise inkex.AbortExtension(
                f"template {template!r} has no {side}.svg yet "
                "(push a design first, or check the template name)."
            )
        text = source.read_text(encoding="utf-8")
        self.document = load_svg(io.BytesIO(text.encode("utf-8")))
        self.svg = self.document.getroot()
        save_config({"sdk_root": str(sdk_root), "last_template": template})
        return [
            "Kaascan · design loaded",
            f"  template : {template}/{side}.svg",
            f"  placeholders found : {', '.join(sorted(_placeholder_names(text))) or 'none'}",
            "  Tip: run the push extension from Extensions > Kaascan SDK "
            "after editing to send it back.",
        ]


if __name__ == "__main__":
    KaascanTemplates().run()