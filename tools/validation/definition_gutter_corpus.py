"""Check that definition-event boxes stay inside the detected physical column."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
PARSER_ROOT = ROOT / "parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from extract import column_boundary, extract  # noqa: E402
from round5_current_validate import CORPUS, resolve_pdf  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zotero-storage", type=Path, required=True)
    args = parser.parse_args()

    checked = 0
    crossing: list[dict[str, object]] = []
    gutter_margin = 6.0
    for sample in CORPUS:
        pdf = resolve_pdf(args.zotero_storage, sample)
        parsed = extract(pdf)
        with pdfplumber.open(str(pdf)) as document:
            boundaries = {
                page_number: column_boundary(page.chars, page.width)
                for page_number, page in enumerate(document.pages, 1)
            }
        for event in parsed["definition_events"]:
            boundary = boundaries.get(event["page"])
            if boundary is None:
                continue
            checked += 1
            x0, _top, x1, _bottom = event["bbox"]
            if x0 < boundary - gutter_margin and x1 > boundary + gutter_margin:
                crossing.append({
                    "sample": sample,
                    "page": event["page"],
                    "definition_id": event["definition_id"],
                    "boundary": boundary,
                    "bbox": event["bbox"],
                    "text": event["text"],
                })

    print(json.dumps({"definition_events_checked": checked, "crossing_count": len(crossing), "crossing": crossing[:20]}, ensure_ascii=False, indent=2))
    if crossing:
        raise AssertionError(f"{len(crossing)} definition events cross a detected gutter")
    print("definition-gutter corpus: ok")


if __name__ == "__main__":
    main()
