"""Regression check for physical single-column vs two-column page detection."""
from __future__ import annotations

import argparse
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
PARSER_ROOT = ROOT / "parser"
import sys

if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from extract import column_boundary  # noqa: E402


CORPUS = {
    "siam": ("3L46E8X7", "Esser*.pdf"),
    "jasa": ("37AWYBBN", "Fan*.pdf"),
    "mm": ("L2FVRVAT", "McCoy*.pdf"),
    "stix": ("MDDRGL2V", "Kumar*.pdf"),
}


def resolve(storage: Path, name: str) -> Path:
    key, pattern = CORPUS[name]
    matches = list((storage / key).glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(f"{name}: expected one PDF, found {len(matches)}")
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zotero-storage", type=Path, required=True)
    args = parser.parse_args()

    single_column_pages = [("siam", page) for page in (1, 4, 17)]
    double_column_pages = [("jasa", 5), ("mm", 2), ("stix", 1)]
    failures: list[str] = []
    observations: list[dict[str, object]] = []

    for name, page_number in single_column_pages + double_column_pages:
        pdf = resolve(args.zotero_storage, name)
        with pdfplumber.open(str(pdf)) as document:
            page = document.pages[page_number - 1]
            boundary = column_boundary(page.chars, page.width)
        observations.append({"paper": name, "page": page_number, "boundary": boundary})
        if (name == "siam" and boundary is not None) or (name != "siam" and boundary is None):
            failures.append(f"{name} page {page_number}: boundary={boundary}")

    print(observations)
    if failures:
        raise AssertionError("; ".join(failures))
    print("column-boundary corpus: ok")


if __name__ == "__main__":
    main()
