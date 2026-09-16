"""Compact, manually anchored validation for the second cross-publisher round.

The anchors come from visual inspection of the source PDFs.  The report keeps
candidate recall separate from Unicode identity recovery: an unknown glyph at
the right bbox is not counted as a usable symbol identity.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parent


def load(directory: str, filename: str = "composite_extraction.json") -> dict:
    return json.loads((ROOT / directory / filename).read_text(encoding="utf-8"))


def overlap(a: list[float], b: list[float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def page(data: dict, number: int) -> dict:
    return next(item for item in data["pages"] if item["page"] == number)


def candidates_in(data: dict, number: int, bbox: list[float]) -> list[dict]:
    return [item for item in page(data, number)["candidate_chars"] if overlap(item["bbox"], bbox)]


def symbol_counts(data: dict, number: int, symbols: list[str]) -> dict[str, int]:
    chars = page(data, number)["candidate_chars"]
    return {
        symbol: sum(item.get("surface_normalized") == symbol for item in chars)
        for symbol in symbols
    }


def main() -> None:
    siam = load("round2_fixed_siam")
    wiley = load("round2_fixed_wiley")
    jasa = load("round2_fixed_ims")
    nips = load("round2_nips")
    cnki_before = load("round2_cnki")
    cnki_after = load("round2_fixed_cnki")
    wiley_old = load("round2_old_wiley", "stage1_extraction.json")
    jasa_old = load("round2_old_ims", "stage1_extraction.json")

    wiley_bbox = [260, 155, 340, 181]
    jasa_bbox = [125, 605, 180, 630]
    cnki_regions = {
        "z": [410, 685, 425, 705],
        "m_b": [148, 718, 170, 738],
        "G_obj": [230, 718, 256, 738],
        "i_p": [347, 718, 365, 738],
        "i_c": [451, 718, 468, 738],
        "G_tr": [282, 734, 305, 755],
        "G_tr(second occurrence)": [408, 734, 431, 755],
        "G_tr(i_p,i_c)": [455, 749, 508, 770],
    }

    wiley_chars = candidates_in(wiley, 2, wiley_bbox)
    jasa_chars = candidates_in(jasa, 2, jasa_bbox)
    results = {
        "scope": "正文作者赋义符号；不把 argmin、norm、sum、公式编号和图中文字算作目标",
        "papers": [
            {
                "id": "siam_2009",
                "publisher": "SIAM",
                "pdf": "A Fast Iterative Shrinkage-Thresholding Algorithm for Linear Inverse Problems",
                "font_chain": "LaTeX/pdfTeX + CM Type 1",
                "evidence": "Ax=b+w; where A,b,w,x,m,n are described; p2 defines λ and L",
                "pages": [1, 2],
                "symbol_counts": {
                    "p1": symbol_counts(siam, 1, ["A", "b", "w", "x", "m", "n"]),
                    "p2": symbol_counts(siam, 2, ["λ", "L", "R", "W"]),
                },
                "verdict": "PASS: inspected definition/formula symbols have candidate occurrences",
            },
            {
                "id": "wiley_2019",
                "publisher": "Wiley / Communications on Pure and Applied Mathematics",
                "pdf": "Stable Gabor Phase Retrieval and Spectral Clustering",
                "font_chain": "pdfTeX + MathTime 2 Type 1 (MT2*) without ToUnicode",
                "evidence": "(|φ_ω(f)|)_{ω∈Ω}; page 2 visual crop",
                "bbox": wiley_bbox,
                "old_candidate_count": len([item for item in wiley_old["candidates"] if item["page"] == 2 and overlap(item["bbox"], wiley_bbox)]),
                "composite_candidate_count": len(wiley_chars),
                "raw_surfaces_in_bbox": sorted({item["surface"] for item in wiley_chars}),
                "identity_recovered": False,
                "verdict": "FAIL(identity): φ/ω/Ω are present visually but extracted as !, (cid:127), etc.",
            },
            {
                "id": "jasa_2001",
                "publisher": "American Statistical Association / Journal of the American Statistical Association",
                "pdf": "Variable Selection via Nonconcave Penalized Likelihood and its Oracle Properties",
                "font_chain": "Ghostscript + Realpage Type 1",
                "evidence": "y = Xβ + ε; page 2 equation (2.1)",
                "bbox": jasa_bbox,
                "old_candidate_count": len([item for item in jasa_old["candidates"] if item["page"] == 2 and overlap(item["bbox"], jasa_bbox)]),
                "composite_candidate_count": len(jasa_chars),
                "raw_surfaces_in_bbox": sorted({item["surface"] for item in jasa_chars}),
                "identity_recovered": False,
                "verdict": "FAIL(identity): β/ε are retained at the bbox but surface-decode to ‚/˜.",
            },
            {
                "id": "neurips_2001",
                "publisher": "NeurIPS proceedings",
                "pdf": "Algorithms for Non-negative Matrix Factorization",
                "font_chain": "Python PDF library + Times Type 1 / OCR overlay",
                "evidence": "V ≈ WH; n×m, n×r, r×m are introduced in the body",
                "pages": [1, 2],
                "symbol_counts": {"p1": symbol_counts(nips, 1, ["V", "W", "H", "n", "m", "r"])},
                "verdict": "PASS for the inspected ASCII-symbol sample",
            },
            {
                "id": "cnki_2024",
                "publisher": "《制造技术与机床》/ CNKI",
                "pdf": "基于 GAN 的车门上饰板表面缺陷检测数据增广算法",
                "font_chain": "ReaderEx_DIS + Cambria Math + Chinese TrueType",
                "evidence": "正文段落定义 z、y、G_obj(z,y)、m_b、i_p、i_c、G_tr",
                "regions": cnki_regions,
                "baseline_counts": {
                    name: len(candidates_in(cnki_before, 3, bbox))
                    for name, bbox in cnki_regions.items()
                },
                "after_math_alphanumeric_and_cn_definition_counts": {
                    name: len(candidates_in(cnki_after, 3, bbox))
                    for name, bbox in cnki_regions.items()
                },
                "verdict": "FAIL(before): all eight inline-definition anchors had 0 candidates; PASS(after): all have candidate glyphs",
            },
        ],
        "conclusion": [
            "The old candidate layer has a real recall hole for inline Unicode mathematical alphanumerics in Chinese engineering prose.",
            "The high-recall layer can preserve glyph bboxes while still failing identity recovery for unembedded custom Type 1 math fonts.",
            "Adding Unicode mathematical-alphanumeric detection and Chinese definition triggers fixes the CNKI omission, but does not solve MT2/Realpage identity mapping.",
        ],
    }
    (ROOT / "ROUND2_VALIDATION.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 第二轮跨出版社正文符号验收",
        "",
        "只验收作者在正文中赋予意义、读者可能需要回看的符号；图中文字、argmin、范数、sum 和公式编号不计入。",
        "",
        "| 样本 | PDF字体/链路 | 真实PDF对照 | 结果 |",
        "|---|---|---|---|",
        "| SIAM 2009 | CM Type 1 | `Ax=b+w` 与 where 段的 A,b,w,x,m,n；p2 的 λ,L | PASS |",
        "| Wiley 2019 | MathTime 2 Type 1，无 ToUnicode | 视觉为 `φ/ω/Ω`，文本层为 `!/(cid:127)` 等 | 身份失败 |",
        "| JASA 2001 | Realpage Type 1 | 视觉为 `β/ε`，文本层为 `‚/˜` | 身份失败 |",
        "| NeurIPS 2001 | Times Type 1 | `V≈WH` 及 n,m,r | PASS（ASCII样本） |",
        "| CNKI 2024 | Cambria Math + 中文正文 | `m_b,G_obj,i_p,i_c,G_tr` 原方法各 0；修后均有候选 | 原方法漏，修后通过 |",
        "",
        "## 结论",
        "",
        "原方法不能宣称跨字体可用：至少有一类真实正文内联数学符号会漏掉，另有两类旧式数学字体会保留位置但丢失身份。",
        "",
        "本轮新增的最小修正是：识别 Unicode 数学字母、加入中文定义触发词；这只修复 CNKI 的候选召回，不等于解决 MT2/Realpage 的字符映射。",
        "",
        "原始探针输出：`round2_*`；修正版复跑输出：`round2_fixed_*`。",
    ]
    (ROOT / "ROUND2_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"written": "ROUND2_VALIDATION.md", "papers": len(results["papers"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
