"""Validate the composite candidate layer against a small author-symbol gold set.

The gold set is intentionally about symbols that the paper gives a local
meaning to.  It does not count argmin, norm bars, sum, rank, equation numbers,
or other conventional notation as missing symbols.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent

CASES = [
    {
        "id": "ieee2008_eq8",
        "sample": "cross_1",
        "page": 3,
        "expected_author_symbols": ["s", "N", "x", "θ"],
        "representation": "visual_region",
        # Equation (8), right column. The PDF encodes its glyphs as image
        # masks, so individual text surfaces are intentionally unavailable.
        "visual_bbox": [300, 105, 555, 145],
        "note": "Equation (8) is visually readable but absent from page.chars; local formula recognition is required for names/roles.",
    },
    {
        "id": "elsevier1994_eq11a",
        "sample": "cross_5",
        "page": 2,
        "expected_author_symbols": ["y", "M", "x", "v", "T", "F", "z"],
        "representation": "text_formula_and_definition",
        "note": "Times-Italic variables in y=Mx+v and the following where sentences.",
    },
    {
        "id": "elsevier2018_td_sarsa",
        "sample": "cross_2",
        "page": 2,
        "expected_author_symbols": ["θ", "α", "δ", "e", "r", "γ", "λ", "V", "Q", "s", "a", "t"],
        "representation": "text_formula_and_definition",
        "note": "Variables and parameters introduced by equations (1)-(5) and the following definitions.",
    },
    {
        "id": "elsevier2024_prox",
        "sample": "cross_4",
        "page": 2,
        "expected_author_symbols": ["C", "p", "η", "τ", "x", "J", "g", "t"],
        "representation": "text_formula_and_definition",
        "note": "Parameters and functions introduced in Section 2 and the proof.",
    },
    {
        "id": "springer2023_model",
        "sample": "cross_6",
        "page": 2,
        "expected_author_symbols": ["Ψ", "f", "g", "h", "x", "N", "A", "b", "λ", "ε", "p", "θ", "M"],
        "representation": "text_formula_and_definition",
        "note": "Model (1)-(3) and the explicit where clauses; Ψ is a MathTime CID recovered from Encoding/Differences.",
    },
]

CONVENTIONAL_NOTATION = [
    "argmin", "min", "sum", "rank", "norm bars", "equation numbers",
]


def overlaps(a: list[float], b: list[float]) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return max(ax0, bx0) < min(ax1, bx1) and max(ay0, by0) < min(ay1, by1)


def load(sample: str) -> dict:
    return json.loads((ROOT / f"composite_{sample}" / "composite_extraction.json").read_text(encoding="utf-8"))


def load_old(sample: str) -> dict:
    return json.loads((ROOT / sample / "stage1_extraction.json").read_text(encoding="utf-8"))


def main() -> None:
    results = []
    for case in CASES:
        data = load(case["sample"])
        old_data = load_old(case["sample"])
        page = next(p for p in data["pages"] if p["page"] == case["page"])
        if case["representation"] == "visual_region":
            visual = [
                r for r in page["visual_fallback_regions"]
                if overlaps(r["bbox"], case["visual_bbox"])
            ]
            covered = bool(visual)
            old_candidates = [
                c for c in old_data["candidates"]
                if c["page"] == case["page"] and overlaps(c["bbox"], case["visual_bbox"])
            ]
            result = {
                **case,
                "covered": covered,
                "covered_by": "visual_fallback_region" if covered else None,
                "matching_regions": visual,
                "old_candidate_count_in_bbox": len(old_candidates),
            }
        else:
            candidates = page["candidate_chars"]
            old_candidates = [c for c in old_data["candidates"] if c["page"] == case["page"]]
            by_symbol = {}
            for symbol in case["expected_author_symbols"]:
                matches = [
                    c for c in candidates
                    if c.get("surface_normalized") == symbol
                ]
                by_symbol[symbol] = {
                    "covered": bool(matches),
                    "count": len(matches),
                    "sources": sorted({m["source"] for m in matches}),
                    "old_count_same_surface": sum(c.get("surface") == symbol for c in old_candidates),
                }
            result = {
                **case,
                "covered": all(v["covered"] for v in by_symbol.values()),
                "by_symbol": by_symbol,
            }
        results.append(result)
    output = {
        "purpose": "author-defined-symbol recall, not conventional-notation recall",
        "conventional_notation_not_counted_as_targets": CONVENTIONAL_NOTATION,
        "cases": results,
        "all_cases_covered": all(r["covered"] for r in results),
    }
    out = ROOT / "author_symbol_validation.json"
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Author-defined symbol validation",
        "",
        "Conventional notation such as argmin, min, sum, norm bars and equation numbers is not a target.",
        "",
        "| Case | Representation | Covered |",
        "|---|---|---:|",
    ]
    for result in results:
        lines.append(f"| {result['id']} | {result['representation']} | {'PASS' if result['covered'] else 'FAIL'} |")
    lines += ["", f"All cases covered: **{output['all_cases_covered']}**", ""]
    (ROOT / "author_symbol_validation.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"all_cases_covered": output["all_cases_covered"], "cases": [(r["id"], r["covered"]) for r in results]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
