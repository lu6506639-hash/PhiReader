"""Compare candidate recall of the original and expanded probes.

The gold set is the manually read author-definition events in
``round5_validate.py``.  This script deliberately measures candidate recall
only; character identity and definition-relation precision are separate gates.
"""
from __future__ import annotations

import json
from pathlib import Path

from round5_validate import EVENTS, aliases


ROOT = Path(__file__).parent


def candidate_surfaces(sample: str, page_no: int, line_ids: list[str]) -> set[str]:
    data = json.loads((ROOT / sample / "composite_extraction.json").read_text(encoding="utf-8"))
    page = next(page for page in data["pages"] if page["page"] == page_no)
    result: set[str] = set()
    for candidate in page["candidate_chars"]:
        if candidate.get("line_id") not in line_ids:
            continue
        for field in ("surface", "surface_normalized"):
            value = candidate.get(field)
            if value:
                result.add(value)
    return result


def main() -> None:
    rows = []
    for index, event in enumerate(EVENTS, 1):
        v2_sample = event["sample"]
        v1_sample = v2_sample.removesuffix("_v2")
        v1_surfaces = candidate_surfaces(v1_sample, event["page"], event["lines"])
        v2_surfaces = candidate_surfaces(v2_sample, event["page"], event["lines"])
        expected = event["expected"]
        v1_missing = [symbol for symbol in expected if aliases(event, symbol).isdisjoint(v1_surfaces)]
        v2_missing = [symbol for symbol in expected if aliases(event, symbol).isdisjoint(v2_surfaces)]
        rows.append({
            "event": index,
            "paper": event["paper"],
            "page": event["page"],
            "evidence": event["evidence"],
            "v1_missing": v1_missing,
            "v2_missing": v2_missing,
            "v1_pass": not v1_missing,
            "v2_pass": not v2_missing,
        })

    summary = {
        "scope": "34 manually read author-definition events; candidate recall only",
        "event_count": len(rows),
        "v1_event_pass": sum(row["v1_pass"] for row in rows),
        "v2_event_pass": sum(row["v2_pass"] for row in rows),
        "rows": rows,
    }
    (ROOT / "ROUND5_V1_V2_COMPARISON.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Round 5：候选召回 v1/v2 对照",
        "",
        "只比较人工确认的作者赋义事件是否进入候选层，不代表字符身份或定义关系已经正确。",
        "",
        f"- v1 事件通过：{summary['v1_event_pass']}/{len(rows)}",
        f"- v2 事件通过：{summary['v2_event_pass']}/{len(rows)}",
        "",
        "| 论文 | 页 | v1 缺失 | v2 缺失 |",
        "|---|---:|---|---|",
    ]
    for row in rows:
        if row["v1_missing"] or row["v2_missing"]:
            lines.append(
                f"| {row['paper']} | {row['page']} | "
                f"{', '.join(row['v1_missing']) or '无'} | {', '.join(row['v2_missing']) or '无'} |"
            )
    (ROOT / "ROUND5_V1_V2_COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("event_count", "v1_event_pass", "v2_event_pass")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
