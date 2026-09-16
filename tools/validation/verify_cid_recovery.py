import argparse
import re
import json
from pathlib import Path
import fitz


def hex_to_text(s: str) -> str:
    raw = bytes.fromhex(s)
    if len(raw) % 2:
        return "�"
    return raw.decode("utf-16-be", errors="replace")


def parse_cmap(data: bytes):
    text = data.decode("latin1", errors="replace")
    mapping = {}
    for block in re.finditer(r"(\d+) beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>", block.group(2)):
            mapping[int(src, 16)] = hex_to_text(dst)
    for block in re.finditer(r"(\d+) beginbfrange(.*?)endbfrange", text, re.S):
        for src1, src2, dst in re.findall(r"<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>", block.group(2)):
            a, b, d = int(src1, 16), int(src2, 16), int(dst, 16)
            for code in range(a, b + 1):
                mapping[code] = chr(d + code - a)
    return mapping


def parse_differences(pdf, obj: str):
    m = re.search(r"/Differences\s*\[(.*?)\]", obj, re.S)
    if not m:
        return {}
    toks = re.findall(r"/(\S+)|\d+", m.group(1))
    # Re-tokenize because the previous expression loses which alternative matched.
    toks = re.findall(r"/([A-Za-z0-9_.+-]+)|(\d+)", m.group(1))
    result, code = {}, None
    names = {
        "lambda": "λ", "kappa": "κ", "chi": "χ", "sigma": "σ", "mu": "μ",
        "dotlessi": "ı", "dotlessj": "ȷ", "infinity": "∞", "minus": "−",
        "multiply": "×", "periodcentered": "·", "asteriskmath": "∗",
        "element": "∈", "bardbl": "∥", "arrowleft": "←", "arrowright": "→",
        "lessequal": "≤", "greaterequal": "≥", "angbracketleft": "⟨",
        "angbracketright": "⟩", "radicalbig": "√", "radicalBigg": "√", "summationdisplay": "∑",
        "summationtext": "∑", "parenleftbigg": "(", "parenrightbigg": ")",
        "bracketleftbigg": "[", "bracketrightbigg": "]", "bracelefttp": "⎧",
        "braceleftmid": "⎪", "braceleftbt": "⎩", "squaresolid": "■",
    }
    for name, number in toks:
        if number:
            code = int(number)
        elif code is not None:
            result[code] = names.get(name, name)
            code += 1
    return result


def font_info(pdf):
    info = {}
    for xref in range(1, pdf.xref_length()):
        try:
            obj = pdf.xref_object(xref, compressed=False)
        except Exception:
            continue
        m = re.search(r"/BaseFont\s*/([^\s/>]+)", obj)
        if not m or "/Subtype /Type1" not in obj:
            continue
        name = m.group(1)
        cmap = {}
        enc = {}
        tm = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
        if tm:
            try:
                cmap = parse_cmap(pdf.xref_stream(int(tm.group(1))) or b"")
            except Exception:
                pass
        em = re.search(r"/Encoding\s+(\d+)\s+0\s+R", obj)
        if em:
            try:
                enc = parse_differences(pdf, pdf.xref_object(int(em.group(1)), compressed=False))
            except Exception:
                pass
        elif "/Encoding /WinAnsiEncoding" in obj:
            enc = {}
        info.setdefault(name, {"font_xrefs": [], "to_unicode": cmap, "differences": enc})
        info[name]["font_xrefs"].append(xref)
    return info


def main():
    argument_parser = argparse.ArgumentParser(description="Inspect CID recovery for one PDF")
    argument_parser.add_argument("pdf", type=Path)
    argument_parser.add_argument("--out", type=Path, help="optional JSON report path")
    args = argument_parser.parse_args()
    if not args.pdf.is_file():
        raise FileNotFoundError(f"PDF does not exist: {args.pdf}")
    doc = fitz.open(args.pdf)
    fonts = font_info(doc)
    rows = {}
    for page_no, page in enumerate(doc, 1):
        raw = page.get_text("rawdict")
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = span.get("font", "")
                    for ch in span.get("chars", []):
                        value = ch.get("c", "")
                        if not value or ord(value[0]) >= 32:
                            continue
                        code = ord(value)
                        key = f"{font}:{code}"
                        entry = rows.setdefault(key, {"font": font, "code": code, "count": 0, "pages": [], "bboxes": [], "visual_context": []})
                        entry["count"] += 1
                        if page_no not in entry["pages"]:
                            entry["pages"].append(page_no)
                        if len(entry["bboxes"]) < 5:
                            entry["bboxes"].append(list(ch.get("bbox", [])))
                        if len(entry["visual_context"]) < 3:
                            entry["visual_context"].append({"page": page_no, "bbox": list(ch.get("bbox", [])), "line_chars": "".join(c.get("c", "") for c in line.get("spans", [{}])[0].get("chars", []))})
    for entry in rows.values():
        f = fonts.get(entry["font"], {})
        code = entry["code"]
        entry["to_unicode"] = f.get("to_unicode", {}).get(code)
        entry["encoding_difference"] = f.get("differences", {}).get(code)
        entry["recovered"] = entry["to_unicode"] or entry["encoding_difference"]
        entry["status"] = "recovered" if entry["recovered"] else "unresolved"
    result = {"pdf": str(args.pdf), "font_inventory": {k: {"font_xrefs": v["font_xrefs"], "to_unicode_count": len(v["to_unicode"]), "differences_count": len(v["differences"])} for k, v in fonts.items()}, "unknown_glyphs": sorted(rows.values(), key=lambda x: (x["font"], x["code"]))}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"unique_unknown_glyphs": len(rows), "recovered": sum(x["status"] == "recovered" for x in rows.values()), "unresolved": sum(x["status"] == "unresolved" for x in rows.values()), "output": str(args.out) if args.out else None}, ensure_ascii=False, indent=2))
    for x in result["unknown_glyphs"]:
        print(f"p{x['pages']} {x['font']} code={x['code']} count={x['count']} -> {x['recovered']!r} ({x['status']})")


if __name__ == "__main__":
    main()
