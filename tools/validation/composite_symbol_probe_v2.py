"""Second-pass probe for author-defined symbols.

This experiment separates two questions that the original probe conflated:

* candidate coverage: did we retain the PDF glyph at the relevant location?
* author-definition evidence: does nearby text assign a meaning to it?

The pass is intentionally permissive.  It restores approximate word gaps,
uses definition-context windows, and does not require a math font name for a
glyph on a definition line.  It does *not* claim that a raw character from a
private font is correctly decoded; that remains an identity-recovery stage.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import ImageDraw

import composite_symbol_probe as v1


# The PDF text layer often emits an approximation sign as a tilde.  It is a
# structural cue even when it is not a trustworthy Unicode identity.
MATH_CUES = set(v1.MATH_UNICODE) | {"~", "≈", "≃", "≅", "∼"}
DEFINITION_RE_V2 = re.compile(
    r"\b(?:where|given|let|denote(?:d|s)?|define[sd]?|defined\s+as|"
    r"we\s+use|assum(?:e|ed)|represent(?:s|ed)?|refer(?:s|red)?\s+to|"
    r"correspond(?:s|ing)?\s+to|call(?:ed)?|is\s+(?:a|an|the)|"
    r"are\s+(?:the|a|an))\b",
    re.I,
)


def line_text_with_gaps(line: list[dict]) -> str:
    """Reinsert likely word gaps from PDF character geometry.

    This is only for language triggers and context matching.  Candidate
    bboxes and raw surfaces are never reconstructed from this string.
    """
    if not line:
        return ""
    ordered = sorted(line, key=lambda c: float(c.get("x0", 0)))
    out: list[str] = []
    previous = None
    for c in ordered:
        text = c.get("text", "") or ""
        if not text.strip():
            previous = c
            continue
        if previous is not None:
            gap = float(c.get("x0", 0)) - float(previous.get("x1", 0))
            size = min(float(c.get("size", 10) or 10), float(previous.get("size", 10) or 10))
            # A normal word gap is often smaller than this on journal PDFs;
            # use a low threshold and cap the number of inserted spaces.
            threshold = max(0.8, min(3.0, size * 0.16))
            if gap > threshold:
                out.append(" ")
        out.append(text)
        previous = c
    return "".join(out)


def line_metrics_v2(line: list[dict]) -> dict:
    raw = v1.line_metrics(line)
    spaced = line_text_with_gaps(line)
    cue_count = sum(any(ch in MATH_CUES for ch in (c.get("text", "") or "")) for c in line)
    return {**raw, "text_spaced": spaced, "cue_count": cue_count}


def definition_like_v2(metrics: dict) -> bool:
    return bool(v1.CN_DEFINITION_RE.search(metrics["text_spaced"]) or DEFINITION_RE_V2.search(metrics["text_spaced"]))


def formula_like_v2(metrics: dict) -> bool:
    """Retain known math lines plus short/display-like unknown-font lines."""
    if definition_like_v2(metrics):
        return True
    # Use the original math-family/operator rules for fonts that expose them.
    if metrics["operator_count"] >= 1 and metrics["math_count"] >= 1:
        return True
    if metrics["cue_count"] >= 1 and metrics["math_count"] >= 1:
        return True
    if metrics["family_count"] >= 2 or (metrics["math_count"] >= 2 and len(metrics["text"]) <= 100):
        return True
    # A short isolated line containing an equality/approximation mark can be
    # a Times-like display formula even when its variables look like prose.
    if metrics["cue_count"] >= 1 and len(metrics["text_spaced"]) <= 100:
        return True
    return False


def add_candidate(candidates, seen, c, page_no, source, line_id, font_maps):
    key = c["index"]
    if key in seen:
        return
    candidates.append(v1.candidate_for_char(c, page_no, source, line_id, font_maps))
    seen.add(key)


def analyze_page_v2(page, page_no: int, font_maps: dict[str, dict[int, str]]) -> dict:
    chars = []
    for idx, raw in enumerate(page.chars):
        c = dict(raw)
        c["index"] = idx
        chars.append(c)

    # A wider baseline tolerance merges detached operator glyphs such as the
    # approximation sign emitted on a separate line by some Times PDFs.
    # Keep the original conservative baseline groups for reading-order and
    # definition IDs.  A separate geometry pass below handles detached
    # operators without merging unrelated two-column lines.
    lines = v1.group_lines(chars)
    candidates: list[dict] = []
    formula_lines: list[dict] = []
    definition_lines: list[dict] = []
    definition_indexes: set[int] = set()
    line_records: list[dict] = []
    seen: set[int] = set()

    for line_idx, line in enumerate(lines):
        metrics = line_metrics_v2(line)
        line_id = f"p{page_no}:line{line_idx}"
        record = {"line_id": line_id, **metrics}
        line_records.append(record)
        if definition_like_v2(metrics):
            definition_indexes.add(line_idx)
            definition_lines.append(record)
        elif formula_like_v2(metrics):
            formula_lines.append(record)

    # Attach nearby ordinary-font glyphs to a formula when a detached math
    # operator is emitted just above/below the line (the Times ``~`` case).
    # This is deliberately geometric: the original raw line IDs remain
    # stable, and no Unicode identity is inferred from the operator.
    detached_operator_indexes: set[int] = set()
    operator_chars = [
        c for c in chars if any(ch in MATH_CUES for ch in (c.get("text", "") or ""))
    ]
    for op in operator_chars:
        op_center = (float(op.get("top", 0)) + float(op.get("bottom", 0))) / 2
        for idx, line in enumerate(lines):
            line_top = min(float(c.get("top", 0)) for c in line)
            line_bottom = max(float(c.get("bottom", 0)) for c in line)
            if line_top - 5.0 <= op_center <= line_bottom + 5.0:
                detached_operator_indexes.add(idx)
                break

    # Definition context is a paragraph-level recall expansion.  It catches
    # definitions split over several PDF lines, e.g. the pure-Times NMF
    # paragraph where "where" appears on one line and W/H/r on the next.
    context_indexes: set[int] = set()
    for idx in definition_indexes:
        for j in range(max(0, idx - 3), min(len(lines), idx + 4)):
            context_indexes.add(j)

    for line_idx, line in enumerate(lines):
        if line_idx in definition_indexes:
            source = "definition_line_all_glyphs"
            line_id = f"p{page_no}:line{line_idx}"
            for c in line:
                add_candidate(candidates, seen, c, page_no, source, line_id, font_maps)
        elif line_idx in context_indexes:
            source = "definition_context_all_glyphs"
            line_id = f"p{page_no}:line{line_idx}"
            for c in line:
                add_candidate(candidates, seen, c, page_no, source, line_id, font_maps)
        elif line_idx in detached_operator_indexes or formula_like_v2(line_metrics_v2(line)):
            source = "formula_line_all_glyphs"
            line_id = f"p{page_no}:line{line_idx}"
            for c in line:
                add_candidate(candidates, seen, c, page_no, source, line_id, font_maps)

    # Keep the detached operator itself as a bbox candidate as well.  Its raw
    # surface may be ``~`` even when the page visually shows ``≈``.
    for c in operator_chars:
        if c["index"] not in seen:
            add_candidate(candidates, seen, c, page_no, "detached_operator_glyph", "", font_maps)

    # Preserve standalone known math glyphs, including ones on lines that did
    # not pass the broad line gate.
    for c in chars:
        if v1.is_math_glyph(c) and c["index"] not in seen:
            add_candidate(candidates, seen, c, page_no, "standalone_math_glyph", "", font_maps)

    return {
        "page": page_no,
        "width": page.width,
        "height": page.height,
        "candidate_chars": candidates,
        "unknown_glyphs": v1.image_glyph_candidates(page, page_no),
        "formula_lines": formula_lines,
        "definition_lines": definition_lines,
        "definition_context_line_ids": [f"p{page_no}:line{i}" for i in sorted(context_indexes)],
        "line_records": line_records,
        "visual_fallback_regions": v1.image_regions(page),
    }


def render_overlay(pdf_path: Path, pages: list[dict], out_dir: Path) -> None:
    pdf = pdfium.PdfDocument(str(pdf_path))
    for data in pages:
        page = pdf[data["page"] - 1]
        image = page.render(scale=1.5).to_pil().convert("RGB")
        draw = ImageDraw.Draw(image)
        for c in data["candidate_chars"]:
            x0, top, x1, bottom = c["bbox"]
            if "definition" in c["source"]:
                color = (220, 30, 30)
            elif "context" in c["source"]:
                color = (220, 125, 20)
            else:
                color = (30, 110, 220)
            draw.rectangle((x0 * 1.5, top * 1.5, x1 * 1.5, bottom * 1.5), outline=color, width=1)
        image.save(out_dir / f"page-{data['page']:02d}-v2-candidates.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    font_maps = v1.build_font_maps(args.pdf)
    pages = []
    font_counts = Counter()
    with pdfplumber.open(str(args.pdf)) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            font_counts.update(c.get("fontname", "") for c in page.chars)
            pages.append(analyze_page_v2(page, page_no, font_maps))
    data = {
        "input": str(args.pdf),
        "method": "composite_author_defined_symbol_probe_v2",
        "policy": {
            "candidate_recall": "definition lines plus paragraph context, layout/math lines, and standalone math glyphs",
            "definition_text": "gap-restored line text; definition evidence is separate from symbol identity",
            "identity_policy": "raw surface retained; private-font glyphs remain unresolved until mapping or visual confirmation",
            "excluded_from_semantic_target": ["argmin", "min", "max", "sum", "integral", "norm", "rank", "equation_number", "figure_text", "table_text"],
        },
        "totals": {
            "pages": len(pages),
            "candidate_chars": sum(len(p["candidate_chars"]) for p in pages),
            "unknown_glyphs": sum(len(p["unknown_glyphs"]) for p in pages),
            "formula_lines": sum(len(p["formula_lines"]) for p in pages),
            "definition_lines": sum(len(p["definition_lines"]) for p in pages),
            "definition_context_lines": sum(len(p["definition_context_line_ids"]) for p in pages),
            "visual_regions": sum(len(p["visual_fallback_regions"]) for p in pages),
        },
        "fonts": font_counts.most_common(),
        "cid_font_maps": {k: len(v) for k, v in font_maps.items()},
        "pages": pages,
    }
    (args.out / "composite_extraction.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_render:
        render_overlay(args.pdf, pages, args.out)
    (args.out / "composite_report.md").write_text(
        "\n".join([
            "# Composite author-defined symbol probe v2",
            "",
            f"Input: `{args.pdf}`",
            "",
            f"- Candidate chars: {data['totals']['candidate_chars']}",
            f"- Formula-like lines: {data['totals']['formula_lines']}",
            f"- Definition-like lines: {data['totals']['definition_lines']}",
            f"- Definition context lines: {data['totals']['definition_context_lines']}",
            "",
            "Candidate recall and author-defined-symbol confirmation are separate stages.",
        ]) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
