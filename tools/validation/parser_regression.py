"""Small deterministic regression checks for the final symbol pool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    args = parser.parse_args()
    data = json.loads(args.result.read_text(encoding="utf-8"))
    surfaces = {item["surface"] for item in data["symbols"]}

    required = {"n", "m", "V", "r", "W", "H", "v", "h", "D", "A", "B"}
    forbidden_prose = {"e", "t", "i", "o", "c", "s"}
    missing = sorted(required - surfaces)
    prose = sorted(forbidden_prose & surfaces)
    if missing or prose:
        raise SystemExit(json.dumps({"status": "fail", "missing": missing, "prose_false_positives": prose}, ensure_ascii=False))

    print(json.dumps({
        "status": "ok",
        "required_present": sorted(required),
        "prose_false_positives": [],
        "final_symbols": data["totals"]["final_symbols"],
        "definition_events": data["totals"]["definition_events"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
