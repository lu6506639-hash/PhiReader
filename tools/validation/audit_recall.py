"""Audit candidate coverage in manually identified formula bands."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pdfplumber

from extract_stage1 import is_math_char


def line_groups(chars: list[dict], tolerance: float = 1.5) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for c in sorted(chars, key=lambda x: (x.get("top", 0), x.get("x0", 0))):
        if not groups or abs(float(c["top"]) - float(groups[-1][0]["top"])) > tolerance:
            groups.append([c])
        else:
            groups[-1].append(c)
    return groups


def math_dominant(line: list[dict]) -> bool:
    meaningful = [c for c in line if c.get("text", "").strip()]
    math_count = sum(1 for c in meaningful if is_math_char(c))
    # This selects displayed formulas and formula-heavy lines, not ordinary
    # prose with one inline variable. It is still an audit heuristic.
    return len(meaningful) >= 2 and math_count >= 2 and math_count / len(meaningful) >= 0.35


def main() -> None:
    import sys

    pdf_path = Path(sys.argv[1])
    rows = []
    total_formula_chars = total_selected = total_cid = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            lines = [line for line in line_groups(page.chars) if math_dominant(line)]
            chars = [c for line in lines for c in line if c.get("text", "").strip()]
            # The stage-1 predicate is the candidate membership test.
            selected = [c for c in chars if is_math_char(c)]
            missing = [c for c in chars if not is_math_char(c)]
            italic_missing = [c for c in missing if "Italic" in (c.get("fontname", "") or "")]
            cid = [c for c in selected if str(c.get("text", "")).startswith("(cid:")]
            total_formula_chars += len(chars)
            total_selected += len(selected)
            total_cid += len(cid)
            rows.append(
                {
                    "page": page_no,
                    "math_dominant_lines": len(lines),
                    "formula_band_chars": len(chars),
                    "candidate_chars": len(selected),
                    "coverage": round(len(selected) / len(chars), 4) if chars else None,
                    "missing_chars": "".join(c.get("text", "") for c in missing),
                    "missing_count": len(missing),
                    "italic_missing_count": len(italic_missing),
                    "italic_missing_examples": [c.get("text") for c in italic_missing[:20]],
                    "cid_count": len(cid),
                    "cid_examples": [c.get("text") for c in cid[:10]],
                }
            )
    out = {
        "pdf": str(pdf_path),
        "formula_band_chars": total_formula_chars,
        "candidate_chars": total_selected,
        "coverage": round(total_selected / total_formula_chars, 4) if total_formula_chars else None,
        "cid_glyphs": total_cid,
        "pages": rows,
        "interpretation": "Coverage is for manually selected formula bands and tests stage-1 candidate recall, not semantic precision.",
    }
    Path("paper_probe/output/recall_audit.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
