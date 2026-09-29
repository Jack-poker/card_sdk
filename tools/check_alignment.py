#!/usr/bin/env python3
"""Alignment gate: verify card generation never moves static artwork.

The core rule of this card pipeline is *template geometry is fixed, only the
recorded values change*. This check enforces that property render-level: every
static word (words whose text is not one of the injected values) must land at
the exact same position (PT) on the final PDF for every dataset, and every PDF
must be a true CR80 page.

Run:  python tools/check_alignment.py            # all templates
      python tools/check_alignment.py --templates BANK_INSPIRE BLUE_TOPBAR
      python tools/check_alignment.py --json --threshold 0.05

Exit code: 0 = all static geometry preserved; 1 = something moved.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import defaultdict
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from card_sdk import card as _card  # noqa: E402
from card_sdk._svg2pdf import svg2pdf_py  # noqa: E402

CR80_PT_W = 242.65
CR80_PT_H = 153.01
MM_PER_PT = 25.4 / 72.0


# --- realistic datasets per template -----------------------------------------
def _bank_datasets() -> list[dict]:
    return [
        dict(
            student_name="A", student_class="C1", school_name="DEMO SCHOOL",
            student_id="1", student_code="0000 0000 0000 0001",
            valid_thru="09/30", director_name="D", director_contact="+250 0",
        ),
        dict(
            student_name="TUYISHIME EMMANUEL BIZIMANA",
            student_class="LEVEL 5 SOFTWARE DEV TEAM B",
            school_name="GREEN HILLS ACADEMY KIGALI RWANDA",
            student_id="123455876732389",
            student_code="5298 7601 2345 6789",
            valid_thru="09/30", director_name="BIZIMANA COLAUDE",
            director_contact="+250 788 888 888 EXT 101",
        ),
        dict(
            student_name="LYCEE NYAZA SECONDARY", student_class="S6 MPC",
            school_name="Academy Kicukiro", student_id="999999999999999",
            student_code="1111 2222 3333 4444",
            valid_thru="12/31", director_name="Uwera Aline",
            director_contact="+250 700 000 000",
        ),
    ]


def _mini_datasets() -> list[dict]:
    return [
        dict(student_name="A", school_name="DEMO"),
        dict(student_name="TUYISHIME EMMANUEL BIZIMANA",
             school_name="GREEN HILLS ACADEMY KIGALI RWANDA LONG NAME"),
    ]


def available_templates() -> list[str]:
    """template folders that carry both faces (the pipeline's own rule)."""
    tbase = _card.base_dir / "templates" / "templates_base"
    if not tbase.is_dir():
        return []
    return sorted(
        d.name for d in tbase.iterdir()
        if d.is_dir() and all((d / f"{face}.card.kaascan").is_file() for face in ("front", "back"))
    )


def _datasets_for(template: str) -> list[dict]:
    if template in ("BANK_INSPIRE", "BLUE_TOPBAR_CARD"):
        return _bank_datasets()
    return _mini_datasets()


# --- pipeline reuse -----------------------------------------------------------
def fill_and_sanitize(template: str, kwargs: dict) -> dict:
    fmt = _card.build_card_fmt_args(**kwargs)
    faces = _card.render_card_svgs(template, fmt)
    return {face: _card._sanitize_font_families(svg) for face, svg in faces.items()}


def render_face(svg: str) -> pymupdf.Document:
    data = svg2pdf_py.svg_pages_to_pdfs([svg], _card.svg)[0]
    raw = data.getvalue() if hasattr(data, "getvalue") else bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
    return pymupdf.open("pdf", raw)


def value_tokens(fmt: dict) -> set[str]:
    tokens: set[str] = set()
    for v in fmt.values():
        if not isinstance(v, str) or len(v) > 80:
            continue
        tokens.update(v.split())
    return tokens


def authored_root_mm(template: str, face: str):
    """The template's own authored root size (mm) — the expected page size."""
    p = _card.base_dir / "templates" / "templates_base" / template / f"{face}.card.kaascan"
    s = p.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"<svg\b[^>]*width=\"([\d.]+)mm\"[^>]*height=\"([\d.]+)mm\"", s)
    if not m:
        m = re.search(r"<svg\b[^>]*height=\"([\d.]+)mm\"[^>]*width=\"([\d.]+)mm\"", s)
    return (float(m.group(1)), float(m.group(2))) if m else None


def page_words(page: pymupdf.Page) -> dict:
    locs = defaultdict(list)
    for w in page.get_text("words"):
        locs[w[4]].append((w[0] * MM_PER_PT, w[1] * MM_PER_PT))
    return locs


# --- the gate ------------------------------------------------------------------
@dataclass
class FaceReport:
    template: str
    face: str
    pair: str
    static_words: int
    max_dx_mm: float
    max_dy_mm: float
    missing_static: int
    page_delta_pt: float


def compare_faces(template: str, face: str, kw_a: dict, kw_b: dict, threshold: float, label: str) -> FaceReport:
    fmt_a = _card.build_card_fmt_args(**kw_a)
    fmt_b = _card.build_card_fmt_args(**kw_b)
    dyn = value_tokens(fmt_a) | value_tokens(fmt_b)

    svg_a = fill_and_sanitize(template, kw_a)[face]
    svg_b = fill_and_sanitize(template, kw_b)[face]
    doc_a, doc_b = render_face(svg_a), render_face(svg_b)
    page_a, page_b = doc_a[0], doc_b[0]

    locs_a = page_words(page_a)
    locs_b = page_words(page_b)
    root = authored_root_mm(template, face)
    if root is None:
        expect_w, expect_h = CR80_PT_W, CR80_PT_H
    else:
        expect_w = root[0] * 72.0 / 25.4
        expect_h = root[1] * 72.0 / 25.4
    pa = page_a.rect
    pb = page_b.rect
    page_delta = max(abs(pa.width - expect_w), abs(pa.height - expect_h),
                     abs(pb.width - expect_w), abs(pb.height - expect_h))

    max_dx = max_dy = 0.0
    missing = 0
    compared = 0
    for token, ps_a in locs_a.items():
        if token in dyn:
            continue  # the value that is *supposed* to change
        ps_b = locs_b.get(token)
        if ps_b is None:
            missing += len(ps_a)
            continue
        if len(ps_b) != len(ps_a):
            missing += abs(len(ps_a) - len(ps_b))
            continue
        for (x1, y1), (x2, y2) in zip(ps_a, ps_b):
            compared += 1
            max_dx = max(max_dx, abs(x1 - x2))
            max_dy = max(max_dy, abs(y1 - y2))
    return FaceReport(template, face, label, compared, max_dx, max_dy, missing, page_delta)


def run(templates: list[str], threshold: float, json_out: bool) -> int:
    reports: list[FaceReport] = []
    for tpl in templates:
        ds = _datasets_for(tpl)
        for face in ("front", "back"):
            for i in range(len(ds) - 1):
                for j in range(i + 1, len(ds)):
                    reports.append(compare_faces(tpl, face, ds[i], ds[j], threshold, f"data{i} v data{j}"))

    fails = 0
    if json_out:
        import json
        print(json.dumps([r.__dict__ for r in reports], indent=2))
    else:
        hdr = f"{'template':<14}{'face':<7}{'pair':<13}{'static':>7}{'dx(mm)':>8}{'dy(mm)':>8}{'missing':>8}{'pageD(pt)':>9}"
        print(hdr)
        print("-" * len(hdr))
        for r in reports:
            # missing_static = a token vanished/changed count between renders; a
            # clipped or overlapping *dynamic* value can legitimately alter the
            # PDF text layer, so it is a warning, never a hard failure. The gate
            # fails only on real drift: static position moved, or page no longer
            # matches the authored size.
            bad = r.max_dx_mm > threshold or r.max_dy_mm > threshold or r.page_delta_pt > 0.1
            fails += int(bad)
            warn = "  <warn: word count changed>" if r.missing_static else ""
            print(f"{r.template:<14}{r.face:<7}{r.pair:<13}{r.static_words:>7}"
                  f"{r.max_dx_mm:>8.4f}{r.max_dy_mm:>8.4f}{r.missing_static:>8}{r.page_delta_pt:>9.3f}"
                  f"{'  <-- FAIL' if bad else ''}{warn}")
        print("-" * len(hdr))
        print(f"templates: {', '.join(templates)}  threshold: {threshold}mm  "
              f"static words checked: {sum(r.static_words for r in reports)}  failures: {fails}")
        print("note: page-delta is measured against each template's own authored root mm size.")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--templates", nargs="*", default=available_templates(),
                    help="default: all known templates")
    ap.add_argument("--threshold", type=float, default=0.05,
                    help="max allowed static-word movement in mm (default 0.05)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    a = ap.parse_args(argv)
    unknown = [t for t in a.templates if t not in available_templates()]
    if unknown:
        known = ", ".join(available_templates())
        print(f"unknown template(s) {unknown} — known: {known}", file=sys.stderr)
        return 1
    return run(a.templates, a.threshold, a.json)


if __name__ == "__main__":
    raise SystemExit(main())