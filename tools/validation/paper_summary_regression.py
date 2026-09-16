"""Regression check for noisy local symbol summaries on a supplied PDF."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.extract import extract


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("pdf", type=Path, help="PDF fixture to validate")
    pdf = argument_parser.parse_args().pdf
    result = extract(pdf)
    meanings = [item["meaning"] for item in result["symbols"]]
    noisy = [
        meaning for meaning in meanings
        if len(meaning) > 40
        or any(marker in meaning for marker in ("(1.1)", "(1.4)", "(2.2)", "→", "∈", "def"))
    ]
    assert not noisy, f"noisy local summaries: {noisy[:3]}"
    print(f"paper-summary-regression=passed ({len(meanings)} symbols)")


if __name__ == "__main__":
    main()
