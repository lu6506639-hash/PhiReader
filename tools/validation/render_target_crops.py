"""Render small PDF crops for manual/visual identity verification."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--page", type=int, required=True)
    ap.add_argument("--line", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    page_data = next(p for p in data["pages"] if p["page"] == args.page)
    glyphs = [c for c in page_data["candidate_chars"] if c["line_id"] == args.line]
    if not glyphs:
        raise SystemExit(f"no candidates on {args.line}")
    x0 = min(c["bbox"][0] for c in glyphs)
    top = min(c["bbox"][1] for c in glyphs)
    x1 = max(c["bbox"][2] for c in glyphs)
    bottom = max(c["bbox"][3] for c in glyphs)
    doc = fitz.open(data["input"])
    page = doc[args.page - 1]
    clip = fitz.Rect(max(0, x0 - 18), max(0, top - 12), min(page.rect.width, x1 + 18), min(page.rect.height, bottom + 12))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(matrix=fitz.Matrix(5, 5), clip=clip, alpha=False).save(args.out)
    print(json.dumps({"line": args.line, "bbox": [x0, top, x1, bottom], "output": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
