"""Round-5 validation for author-defined symbols in nine real PDFs.

This is an event-level audit, not a benchmark for all mathematical tokens.
Each event is a manually read definition passage.  The expected set contains
only symbols to which the paper assigns a role; conventional operators and
norm notation are intentionally absent.

The validator reports three different questions:

* candidate_surface: the decoded PDF surface contains the expected or known
  malformed alias;
* canonical_surface: the preferred Unicode surface is present;
* position_candidate: the composite probe retained at least one candidate in
  the definition passage (manual visual exceptions cover known bad mappings);
* identity: whether the surface can be trusted as the expected symbol.

For the two known private-font failures, visual_identity is recorded as false
even though the glyph bbox was manually verified against the rendered PDF.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent

# The source line ids refer to round4 v2 extraction JSON.  They are deliberately
# short definition passages, rather than arbitrary equation occurrences.
EVENTS = [
    # MIT Press / Times
    {"paper": "Lee & Seung (Times)", "sample": "round4_times_v2", "page": 2,
     "lines": ["p2:line1", "p2:line2"], "expected": ["n", "m", "V"],
     "evidence": "n-dimensional vectors; n x m matrix V; m examples"},
    {"paper": "Lee & Seung (Times)", "sample": "round4_times_v2", "page": 2,
     "lines": ["p2:line2", "p2:line3"], "expected": ["r", "W", "H"],
     "evidence": "n x r W and r x m H; r is smaller"},
    {"paper": "Lee & Seung (Times)", "sample": "round4_times_v2", "page": 2,
     "lines": ["p2:line7"], "expected": ["v", "h", "V", "H"],
     "evidence": "v and h are corresponding columns of V and H"},
    {"paper": "Lee & Seung (Times)", "sample": "round4_times_v2", "page": 2,
     "lines": ["p2:line35", "p2:line44", "p2:line47", "p2:line48"], "expected": ["D", "A", "B"],
     "evidence": "D(A||B) is called the divergence of A from B"},

    # IEEE TSP / Nimbus + Computer Modern
    {"paper": "Leplat et al. (Nimbus/CM)", "sample": "round4_elsevier_v2", "page": 1,
     "lines": ["p1:line37", "p1:line48", "p1:line49"],
     "expected": ["X", "F", "N", "x", "T", "w", "L", "H"],
     "evidence": "spectrogram dimensions; window, hop and overlap parameters"},
    {"paper": "Leplat et al. (Nimbus/CM)", "sample": "round4_elsevier_v2", "page": 2,
     "lines": ["p2:line7", "p2:line11", "p2:line25", "p2:line31", "p2:line34"],
     "expected": ["K", "s", "V", "W", "H"],
     "evidence": "source signals; non-negative V; W dictionary and H activation"},
    {"paper": "Leplat et al. (Nimbus/CM)", "sample": "round4_elsevier_v2", "page": 2,
     "lines": ["p2:line33", "p2:line35", "p2:line37", "p2:line38"],
     "expected": ["S", "I"],
     "evidence": "S is STFT; S dagger is conjugate transpose; I is identity"},
    {"paper": "Leplat et al. (Nimbus/CM)", "sample": "round4_elsevier_v2", "page": 2,
     "lines": ["p2:line54", "p2:line55", "p2:line56", "p2:line58", "p2:line61", "p2:line62"],
     "expected": ["beta", "d", "x", "y", "V", "X"],
     "surface_aliases": {"beta": ["β"]},
     "evidence": "beta-divergence d_beta(x|y) and V=|X|"},
    {"paper": "Leplat et al. (Nimbus/CM)", "sample": "round4_elsevier_v2", "page": 4,
     "lines": ["p4:line33", "p4:line35", "p4:line36"],
     "expected": ["lambda", "W", "delta"],
     "surface_aliases": {"lambda": ["λ"], "delta": ["δ"]},
     "evidence": "lambda is a penalty parameter; vol(W); delta is a small constant"},

    # JASA / Realpage
    {"paper": "Fan & Li (Realpage)", "sample": "round4_jasa_v2", "page": 2,
     "lines": ["p2:line1"], "expected": ["z", "y"],
     "evidence": "Denote z=X^T y and let y-hat=XX^T y"},
    {"paper": "Fan & Li (Realpage)", "sample": "round4_jasa_v2", "page": 2,
     "lines": ["p2:line66", "p2:line70"],
     "expected": ["y", "X", "beta", "epsilon", "n", "d"],
     "surface_aliases": {"beta": ["β", "‚"], "epsilon": ["ε", "˜"]},
     "visual_identity": {"beta": False, "epsilon": False},
     "evidence": "y=X beta+epsilon; y is n x 1 and X is n x d"},
    {"paper": "Fan & Li (Realpage)", "sample": "round4_jasa_v2", "page": 2,
     "lines": ["p2:line22", "p2:line23", "p2:line41", "p2:line48"],
     "expected": ["p", "lambda", "beta"],
     "surface_aliases": {"lambda": ["λ", "‹"], "beta": ["β", "‚", "¢"]},
     "visual_identity": {"lambda": False, "beta": False},
     "evidence": "penalty p and p_lambda; thresholding parameter lambda"},

    # IEEE Signal Processing Magazine / private MM fonts
    {"paper": "McCoy et al. (MM custom)", "sample": "round4_mm_custom_v2", "page": 1,
     "lines": ["p1:line42", "p1:line45"], "expected": ["z", "x", "y", "d"],
     "evidence": "mixed signal z in R^d; z=x+y"},
    {"paper": "McCoy et al. (MM custom)", "sample": "round4_mm_custom_v2", "page": 2,
     "lines": ["p2:line37", "p2:line39", "p2:line41", "p2:line43"],
     "expected": ["x", "y", "D", "A", "d"],
     "evidence": "x sparse; y sparse in frequency; D encodes the DCT; A is atom set"},
    {"paper": "McCoy et al. (MM custom)", "sample": "round4_mm_custom_v2", "page": 2,
     "lines": ["p2:line55", "p2:line60", "p2:line62"],
     "expected": ["x", "y", "lambda", "ell0", "A"],
     "surface_aliases": {"lambda": ["λ", "$"], "ell0": ["ℓ₀", ","]},
     "visual_identity": {"lambda": False, "ell0": False},
     "evidence": "ell_0 norm; lambda is a regularization parameter; atomic gauge"},

    # SIAM / Computer Modern
    {"paper": "Esser et al. (SIAM CM)", "sample": "round4_siam_v2", "page": 3,
     "lines": ["p3:line77", "p3:line80", "p3:line85", "p3:line89"],
     "expected": ["A", "b", "x", "M", "N", "l", "m", "j"],
     "evidence": "nonnegative mixing model and grouped dictionary dimensions"},
    {"paper": "Esser et al. (SIAM CM)", "sample": "round4_siam_v2", "page": 3,
     "lines": ["p3:line113", "p3:line121", "p3:line123"],
     "expected": ["R", "x", "j", "M"],
     "evidence": "R_j intra-group and R inter-group penalties"},
    {"paper": "Esser et al. (SIAM CM)", "sample": "round4_siam_v2", "page": 15,
     "lines": ["p15:line86", "p15:line92", "p15:line97", "p15:line100", "p15:line109", "p15:line112"],
     "expected": ["lambda", "sigma", "I", "c", "L"],
     "surface_aliases": {"lambda": ["λ"], "sigma": ["σ"]},
     "evidence": "lambda wavelength; sigma absorption spectrum; I intensity; c concentration"},

    # Springer/HAL / Latin Modern
    {"paper": "Chambolle & Pock (Latin Modern)", "sample": "round4_springer_lm_v2", "page": 3,
     "lines": ["p3:line28", "p3:line30", "p3:line33"], "expected": ["X", "Y", "K"],
     "evidence": "finite-dimensional spaces X,Y; K is a continuous linear map"},
    {"paper": "Chambolle & Pock (Latin Modern)", "sample": "round4_springer_lm_v2", "page": 3,
     "lines": ["p3:line41", "p3:line44", "p3:line46", "p3:line48"],
     "expected": ["F", "G"], "evidence": "F and G are proper convex functions; F* conjugate"},
    {"paper": "Chambolle & Pock (Latin Modern)", "sample": "round4_springer_lm_v2", "page": 4,
     "lines": ["p4:line8", "p4:line11", "p4:line14"],
     "expected": ["F", "G"], "evidence": "partial F and partial G are subgradients"},
    {"paper": "Chambolle & Pock (Latin Modern)", "sample": "round4_springer_lm_v2", "page": 4,
     "lines": ["p4:line41", "p4:line44", "p4:line48", "p4:line49"],
     "expected": ["theta", "sigma", "tau"],
     "surface_aliases": {"theta": ["θ"], "sigma": ["σ"], "tau": ["τ"]},
     "evidence": "theta relaxation parameter; sigma and tau steps"},

    # Springer LNCS / Computer Modern
    {"paper": "Kim et al. (LNCS CM)", "sample": "round4_springer_lncs_v2", "page": 2,
     "lines": ["p2:line5"], "expected": ["L", "M"],
     "evidence": "L and M are the number of sources/observations"},
    {"paper": "Kim et al. (LNCS CM)", "sample": "round4_springer_lncs_v2", "page": 2,
     "lines": ["p2:line42", "p2:line43", "p2:line45"],
     "expected": ["s", "x", "a", "D", "i", "j"],
     "evidence": "notation for vector/matrix variables and a(d) row/column element"},
    {"paper": "Kim et al. (LNCS CM)", "sample": "round4_springer_lncs_v2", "page": 4,
     "lines": ["p4:line8", "p4:line14", "p4:line15", "p4:line24", "p4:line32"],
     "expected": ["v", "alpha", "lambda", "Gamma", "s", "mu", "Sigma", "delta", "gamma"],
     "surface_aliases": {"alpha": ["α"], "lambda": ["λ"], "Gamma": ["Γ"], "mu": ["μ", "µ"], "Sigma": ["Σ"], "delta": ["δ"], "gamma": ["γ"]},
     "evidence": "v mixture variable; alpha/lambda Gamma parameters; delta/gamma shorthand"},
    {"paper": "Kim et al. (LNCS CM)", "sample": "round4_springer_lncs_v2", "page": 4,
     "lines": ["p4:line50"], "expected": ["c", "K", "z"],
     "evidence": "c normalization term; K_nu is modified Bessel function"},

    # Springer / STIX
    {"paper": "Kumar et al. (STIX)", "sample": "round4_springer_stix_v2", "page": 1,
     "lines": ["p1:line33", "p1:line37", "p1:line38", "p1:line40"],
     "expected": ["x", "t", "A", "s", "b", "N", "K"],
     "evidence": "model x(t)=A s(t)+b; definition line is visually readable but symbols are omitted from text layer"},
    {"paper": "Kumar et al. (STIX)", "sample": "round4_springer_stix_v2", "page": 2,
     "lines": ["p2:line45", "p2:line46", "p2:line49", "p2:line52"],
     "expected": ["x", "A", "s", "N", "R", "K"],
     "evidence": "same mixing model and A/s dimensions"},
    {"paper": "Kumar et al. (STIX)", "sample": "round4_springer_stix_v2", "page": 4,
     "lines": ["p4:line18", "p4:line21", "p4:line22", "p4:line24"],
     "expected": ["C", "A", "D", "d", "m", "t", "L"],
     "evidence": "C[m], A, D, d_k[m], frame index m and length L"},
    {"paper": "Kumar et al. (STIX)", "sample": "round4_springer_stix_v2", "page": 5,
     "lines": ["p5:line30", "p5:line34"], "expected": ["h", "a", "N", "K"],
     "evidence": "h_k=vec(a_k a_k^H); H contains h_k"},

    # CNKI / Times + CID Chinese text
    {"paper": "Zhou et al. (CNKI Times/CID)", "sample": "round4_cnki_times_v2", "page": 3,
     "lines": ["p3:line28", "p3:line29", "p3:line37", "p3:line39", "p3:line40"],
     "expected": ["G", "D", "z", "x", "V", "E", "p"],
     "evidence": "G generator, D discriminator, z noise, V cross-entropy, E expectation, p distributions"},
    {"paper": "Zhou et al. (CNKI Times/CID)", "sample": "round4_cnki_times_v2", "page": 4,
     "lines": ["p4:line3", "p4:line4", "p4:line7", "p4:line8", "p4:line10", "p4:line13", "p4:line14", "p4:line18", "p4:line20", "p4:line21", "p4:line24"],
     "expected": ["L", "G", "D", "x", "y"],
     "evidence": "adversarial/cycle losses and X-to-Y generator/discriminator roles"},
    {"paper": "Zhou et al. (CNKI Times/CID)", "sample": "round4_cnki_times_v2", "page": 5,
     "lines": ["p5:line15", "p5:line18"], "expected": ["C", "H", "W", "r"],
     "evidence": "C channels, H height, W width, r reduction ratio"},
    {"paper": "Zhou et al. (CNKI Times/CID)", "sample": "round4_cnki_times_v2", "page": 8,
     "lines": ["p8:line42", "p8:line44", "p8:line47", "p8:line49"],
     "expected": ["P", "R", "m"],
     "evidence": "precision P, recall R, number of classes m and AP variants"},
]


def aliases(event: dict, symbol: str) -> set[str]:
    values = {symbol}
    values.update(event.get("surface_aliases", {}).get(symbol, []))
    # Canonical labels above are ASCII only to keep the gold file readable.
    return values


def preferred_surface(event: dict, symbol: str) -> str:
    """Return the visual/Unicode surface expected for a canonical label."""
    return event.get("surface_aliases", {}).get(symbol, [symbol])[0]


def load(event: dict) -> dict:
    return json.loads((ROOT / event["sample"] / "composite_extraction.json").read_text(encoding="utf-8"))


def main() -> None:
    results = []
    for event in EVENTS:
        data = load(event)
        page = next(p for p in data["pages"] if p["page"] == event["page"])
        candidates = [c for c in page["candidate_chars"] if c["line_id"] in event["lines"]]
        surfaces = {c.get("surface_normalized") for c in candidates}
        by_symbol = {}
        for symbol in event["expected"]:
            accepted = aliases(event, symbol)
            matches = [c for c in candidates if c.get("surface_normalized") in accepted or c.get("surface") in accepted]
            canonical_matches = [
                c for c in candidates
                if c.get("surface_normalized") == preferred_surface(event, symbol)
                or c.get("surface") == preferred_surface(event, symbol)
            ]
            # A visual exception means the rendered glyph was manually seen in
            # the event, but its PDF surface is known not to be trustworthy.
            visual = event.get("visual_identity", {}).get(symbol)
            by_symbol[symbol] = {
                "candidate_surface": bool(matches),
                "candidate_count": len(matches),
                "surfaces": sorted({str(c.get("surface")) for c in matches}),
                "canonical_surface": bool(canonical_matches),
                "visual_identity": visual if visual is not None else bool(matches),
            }
        candidate_all = all(v["candidate_surface"] for v in by_symbol.values())
        canonical_all = all(v["canonical_surface"] for v in by_symbol.values())
        identity_all = all(v["visual_identity"] for v in by_symbol.values())
        # For visual exceptions, manual rendering established that the
        # position was retained even if the exact surface test is false.
        position_all = all(
            value["candidate_surface"] or symbol in event.get("visual_identity", {})
            for symbol, value in by_symbol.items()
        )
        results.append({
            "paper": event["paper"], "sample": event["sample"],
            "page": event["page"], "lines": event["lines"],
            "evidence": event["evidence"], "expected": event["expected"],
            "candidate_chars_in_event": len(candidates),
            "position_candidate": position_all,
            "candidate_surface_all": candidate_all,
            "canonical_surface_all": canonical_all,
            "identity_all": identity_all,
            "by_symbol": by_symbol,
        })

    def count(key: str) -> tuple[int, int]:
        return sum(bool(r[key]) for r in results), len(results)

    output = {
        "method": "round5_event_validation",
        "scope": "manually read explicit definition passages in nine real PDFs",
        "policy": {
            "targets": "author-assigned symbols only",
            "excluded": ["argmin", "min", "max", "sum", "integral", "norm", "rank", "equation numbers", "figure/table/caption symbols"],
            "position_and_identity_are_separate": True,
        },
        "event_count": len(results),
        "position_candidate": {"passed": count("position_candidate")[0], "total": len(results)},
        "candidate_surface": {"passed": count("candidate_surface_all")[0], "total": len(results)},
        "canonical_surface": {"passed": count("canonical_surface_all")[0], "total": len(results)},
        "trusted_identity": {"passed": count("identity_all")[0], "total": len(results)},
        "results": results,
    }
    (ROOT / "ROUND5_VALIDATION.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Round 5：九篇跨出版社论文的作者赋义符号验收", "",
        "目标集只包含作者在正文中明确赋予意义、读者有理由回看的符号；argmin、范数、求和、积分、公式编号和图表符号不计入。",
        "", "| 论文 | 定义事件 | 位置候选 | canonical 可解码 | 可信身份全对 |", "|---|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['paper']} | 1 | {'PASS' if r['position_candidate'] else 'FAIL'} | {'PASS' if r['canonical_surface_all'] else 'FAIL'} | {'PASS' if r['identity_all'] else 'FAIL'} |")
    lines += [
        "", f"- 定义事件位置候选：{output['position_candidate']['passed']}/{output['event_count']}",
        f"- 候选 glyph surface 存在：{output['candidate_surface']['passed']}/{output['event_count']}",
        f"- canonical surface 可直接解码：{output['canonical_surface']['passed']}/{output['event_count']}",
        f"- 可信身份全对：{output['trusted_identity']['passed']}/{output['event_count']}",
        "", "Realpage 与 MM 自定义字体的失败是人工对照渲染得到的真实失败，不以 bbox 命中抵消字符身份错误。",
        "",
    ]
    (ROOT / "ROUND5_VALIDATION.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ["event_count", "position_candidate", "candidate_surface", "canonical_surface", "trusted_identity"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
