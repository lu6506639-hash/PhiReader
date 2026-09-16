"""Render one PDF page for the PhiReader desktop reader.

Rendering is deliberately demand-driven: the desktop command calls this
script for the page currently being viewed, so opening a 40-page paper does
not rasterize the entire document up front.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pypdfium2 as pdfium


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("page", type=int, help="one-based page number")
    parser.add_argument("--out", type=Path, required=True)
    # Render above the largest normal reader zoom so the page remains sharp
    # when the image is displayed at 100% or enlarged in the viewport.
    parser.add_argument("--scale", type=float, default=2.5)
    args = parser.parse_args()
    if args.page < 1:
        raise SystemExit("page must be >= 1")
    document = pdfium.PdfDocument(str(args.pdf))
    if args.page > len(document):
        raise SystemExit(f"page {args.page} is outside the PDF ({len(document)} pages)")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    page = document[args.page - 1]
    width, height = page.get_size()
    bitmap = page.render(scale=args.scale)
    bitmap.to_pil().convert("RGB").save(args.out, format="PNG", optimize=True)
    print(json.dumps({"output": str(args.out), "width": width, "height": height}, ensure_ascii=False))


if __name__ == "__main__":
    main()
