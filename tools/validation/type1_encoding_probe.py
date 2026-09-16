"""Inspect Type 1 font encodings and PDF character codes.

This diagnostic is deliberately conservative: an embedded Type 1 font's
encoding is evidence about the glyph name used by the font program, not by
itself proof of a Unicode character.  It is useful for finding recoverable
font-level mappings that ordinary PDF text extraction misses.
"""
from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ContentStream, TextStringObject, ByteStringObject


ENCODING_ENTRY = re.compile(r"dup\s+(\d+)\s+/([^\s]+)\s+put")


def base_name(value: object) -> str:
    return str(value or "").lstrip("/")


def embedded_type1_encoding(font: object) -> dict[int, str]:
    descriptor = font.get("/FontDescriptor") if hasattr(font, "get") else None
    if descriptor is None:
        return {}
    descriptor = descriptor.get_object()
    stream_ref = descriptor.get("/FontFile")
    if stream_ref is None:
        return {}
    data = stream_ref.get_object().get_data()
    # The clear-text encoding section precedes the encrypted eexec section.
    text = data.decode("latin1", "ignore")
    start = text.find("/Encoding")
    end = text.find("currentfile eexec", start)
    if start < 0:
        return {}
    if end < 0:
        end = min(len(text), start + 100_000)
    return {int(code): name for code, name in ENCODING_ENTRY.findall(text[start:end])}


def font_resources(page: object) -> dict[str, object]:
    resources = page.get("/Resources")
    if not resources or "/Font" not in resources:
        return {}
    return {str(key): ref.get_object() for key, ref in resources["/Font"].items()}


def text_bytes(value: object) -> bytes | None:
    if isinstance(value, ByteStringObject):
        return bytes(value)
    if isinstance(value, TextStringObject):
        return value.encode("latin1", "replace")
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--page", type=int, default=2)
    args = parser.parse_args()

    reader = PdfReader(str(args.pdf))
    page = reader.pages[args.page - 1]
    fonts = font_resources(page)
    encodings = {key: embedded_type1_encoding(font) for key, font in fonts.items()}
    seen_codes: dict[str, set[int]] = defaultdict(set)
    current_font = ""
    content = ContentStream(page.get_contents(), reader)
    for operands, operator in content.operations:
        if operator == b"Tf" and operands:
            current_font = str(operands[0])
        elif operator == b"Tj" and operands:
            raw = text_bytes(operands[0])
            if raw is not None:
                seen_codes[current_font].update(raw)
        elif operator == b"TJ" and operands:
            for item in operands[0]:
                raw = text_bytes(item)
                if raw is not None:
                    seen_codes[current_font].update(raw)

    print(f"PDF={args.pdf}")
    print(f"PAGE={args.page}")
    for key, font in fonts.items():
        base = base_name(font.get("/BaseFont"))
        if not seen_codes.get(key) and not ("MT2" in base or "Realpage" in base):
            continue
        mapping = encodings.get(key, {})
        codes = sorted(seen_codes.get(key, set()))
        print(f"FONT {key} base={base} subtype={font.get('/Subtype')} embedded_encoding={len(mapping)}")
        if codes:
            print("  seen=" + " ".join(f"{code}:{mapping.get(code, '?')}" for code in codes))


if __name__ == "__main__":
    main()
