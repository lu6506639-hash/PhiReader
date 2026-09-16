"""Composite high-recall probe for author-defined mathematical symbols.

This is an experiment, not the final semantic extractor.  It deliberately
keeps every glyph on formula-like and definition-like lines so that ordinary
Times-Italic variables are not lost merely because the PDF does not use a
named math font.  Conventional notation is filtered only in the semantic
stage, where local definition evidence is available.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from fontTools.agl import AGL2UV
from pypdf import PdfReader
from PIL import ImageDraw


SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
DEFINITION_RE = re.compile(
    r"\b(where|let|denote(?:d|s)?(?:\s+by)?|define[sd]?|we\s+use|"
    r"assum(?:e|ed)|given|represents?|refers?\s+to|means?)\b",
    re.I,
)
CN_DEFINITION_RE = re.compile(
    r"(其中|式中|表示|定义(?:为|是)?|记为|记作|设|令|取|分别为|对应于|指的是|称为)"
)
MATH_UNICODE = set(
    "=+-*/^_<>|∈∑√∞±≤≥∂∇∫∏∝≈≠×·∗∀∃∅∪∩⊂⊆⊥→←↔⇒⇔"
    "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
)
MATH_FAMILY_RE = re.compile(
    r"^(?:CM|MSBM|MSAM|CMSY|CMMI|CMBX|CMEX|MTMI|MTSYN|MTEX|MTSY|"
    r"MT2(?:MI|SY|EX|HR|MIF|MIT|MIS)|Realpage(?:MTIM|TEC|MTEX|SYM|ALL)|"
    r"STIXMath|MathTime|TeX|Symbol|Euclid|txmi|pxmi)",
    re.I,
)
CONVENTIONAL_NOTATION = {
    "argmin", "argmax", "min", "max", "sup", "inf", "lim", "sum", "prod",
    "log", "ln", "exp", "sin", "cos", "tan", "det", "diag", "rank",
    "trace", "tr", "span", "sign", "prox", "ker", "dom", "conv", "s.t.",
}


_GREEK_NAMES = {
    "ALPHA": "α", "BETA": "β", "GAMMA": "γ", "DELTA": "δ", "EPSILON": "ε",
    "ZETA": "ζ", "ETA": "η", "THETA": "θ", "IOTA": "ι", "KAPPA": "κ",
    "LAMDA": "λ", "LAMBDA": "λ", "MU": "μ", "NU": "ν", "XI": "ξ",
    "OMICRON": "ο", "PI": "π", "RHO": "ρ", "SIGMA": "σ", "TAU": "τ",
    "UPSILON": "υ", "PHI": "φ", "CHI": "χ", "PSI": "ψ", "OMEGA": "ω",
}


def normalize_surface(text: str | None) -> str | None:
    """Map mathematical alphanumeric Unicode (𝐶, 𝜏, 𝓁, …) to base symbols."""
    if not text:
        return text
    out = []
    for ch in text:
        name = unicodedata.name(ch, "")
        if "MATHEMATICAL" not in name:
            out.append(unicodedata.normalize("NFKC", ch))
            continue
        tokens = name.split()
        if "LETTER" in tokens:
            if "GREEK" in tokens:
                greek = tokens[-1]
                base = _GREEK_NAMES.get(greek)
                if base:
                    if "CAPITAL" in tokens:
                        base = base.upper()
                    out.append(base)
                    continue
            letter = tokens[-1]
            if len(letter) == 1 and letter.isalpha():
                out.append(letter)
                continue
        out.append(unicodedata.normalize("NFKC", ch))
    return "".join(out)


def is_math_alphanumeric(text: str | None) -> bool:
    """Recognize Unicode mathematical italic/bold/script letters in inline math."""
    return bool(text) and any("MATHEMATICAL" in unicodedata.name(ch, "") for ch in text)


def glyph_name_to_text(name: str) -> str | None:
    name = name.lstrip("/")
    if name in AGL2UV:
        return chr(AGL2UV[name])
    # TeX font encodings commonly suffix a glyph variant with a digit, e.g.
    # Psi1, phi1, Delta1.  The base AGL name is still useful for identity.
    stripped = re.sub(r"\d+$", "", name)
    if stripped in AGL2UV:
        return chr(AGL2UV[stripped])
    return None


def parse_cmap(data: bytes) -> dict[int, str]:
    text = data.decode("latin1", "ignore")
    result: dict[int, str] = {}
    # Parse line-by-line instead of running a cross-line regex.  It avoids
    # pathological backtracking on malformed publisher CMaps and caps a
    # malformed range from allocating an unbounded map.
    scalar_pair = re.compile(r"^<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>$")
    scalar_range = re.compile(r"^<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>$")
    section = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "beginbfchar":
            section = "char"
            continue
        if line == "beginbfrange":
            section = "range"
            continue
        if line in {"endbfchar", "endbfrange"}:
            section = None
            continue
        if section is None:
            continue
        match = scalar_pair.fullmatch(line)
        if match and section == "char":
            code, value = (int(v, 16) for v in match.groups())
            if value <= 0x10FFFF:
                result[code] = chr(value)
            continue
        match = scalar_range.fullmatch(line)
        if not match or section != "range":
            continue
        start, end, value = (int(v, 16) for v in match.groups())
        if end < start or end - start > 4096:
            continue
        for code in range(start, end + 1):
            scalar = value + code - start
            if scalar <= 0x10FFFF:
                result[code] = chr(scalar)
    return result


def build_font_maps(pdf_path: Path) -> dict[str, dict[int, str]]:
    """Build ToUnicode + Encoding/Differences maps keyed by PDF BaseFont."""
    maps: dict[str, dict[int, str]] = defaultdict(dict)
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources or "/Font" not in resources:
            continue
        for _, ref in resources["/Font"].items():
            font = ref.get_object()
            base = str(font.get("/BaseFont", "")).lstrip("/")
            if not base:
                continue
            to_unicode = font.get("/ToUnicode")
            if to_unicode:
                maps[base].update(parse_cmap(to_unicode.get_object().get_data()))
            encoding = font.get("/Encoding")
            if hasattr(encoding, "get_object"):
                encoding = encoding.get_object()
            if not isinstance(encoding, dict):
                family = font_family(base)
                if family and family != base:
                    maps[family].update(maps[base])
                continue
            differences = encoding.get("/Differences", [])
            code = None
            for item in differences:
                if isinstance(item, int):
                    code = item
                    continue
                if code is None:
                    continue
                mapped = glyph_name_to_text(str(item))
                if mapped:
                    maps[base].setdefault(code, mapped)
                code += 1
            # pdfplumber exposes the subset-stripped family in some versions;
            # keep an alias so the same map is found for both spellings.
            family = font_family(base)
            if family and family != base:
                maps[family].update(maps[base])
    return dict(maps)


def decode_surface(text: str | None, font: str | None, font_maps: dict[str, dict[int, str]]) -> str | None:
    if not text:
        return text
    match = re.fullmatch(r"\(cid:(\d+)\)", text)
    if not match:
        return text
    code = int(match.group(1))
    family = font_family(font)
    mapped = font_maps.get(family, {}).get(code)
    return mapped or text


def font_family(font: str | None) -> str:
    return SUBSET_PREFIX.sub("", font or "")


def char_bbox(c: dict) -> list[float]:
    return [round(float(c[k]), 3) for k in ("x0", "top", "x1", "bottom")]


def is_math_glyph(c: dict) -> bool:
    text = c.get("text", "") or ""
    family = font_family(c.get("fontname"))
    return bool(
        MATH_FAMILY_RE.search(family)
        or is_math_alphanumeric(text)
        or any(ch in MATH_UNICODE for ch in text)
    )


def nonspace(chars: list[dict]) -> list[dict]:
    return [c for c in chars if (c.get("text", "") or "").strip()]


def group_lines(chars: list[dict]) -> list[list[dict]]:
    """Group page characters by visual line, tolerating mixed baselines."""
    usable = nonspace(chars)
    if not usable:
        return []
    lines: list[list[dict]] = []
    for c in sorted(usable, key=lambda x: (float(x.get("top", 0)), float(x.get("x0", 0)))):
        size = float(c.get("size", 10) or 10)
        tolerance = max(1.5, min(4.5, size * 0.32))
        if not lines:
            lines.append([c])
            continue
        previous = lines[-1]
        reference = sum(float(x.get("top", 0)) for x in previous) / len(previous)
        if abs(float(c.get("top", 0)) - reference) <= tolerance:
            previous.append(c)
        else:
            lines.append([c])
    for line in lines:
        line.sort(key=lambda x: float(x.get("x0", 0)))
    return lines


def line_text(line: list[dict]) -> str:
    return "".join(c.get("text", "") for c in line)


def line_metrics(line: list[dict]) -> dict:
    text = line_text(line)
    math_count = sum(is_math_glyph(c) for c in line)
    operator_count = sum(any(ch in MATH_UNICODE for ch in (c.get("text", "") or "")) for c in line)
    family_count = sum(bool(MATH_FAMILY_RE.search(font_family(c.get("fontname")))) for c in line)
    italic_count = sum(
        bool(re.search(r"italic|ital|oblique|math|mtmi|cmmi|stix", font_family(c.get("fontname")), re.I))
        for c in line
    )
    x0 = min(float(c["x0"]) for c in line)
    x1 = max(float(c["x1"]) for c in line)
    top = min(float(c["top"]) for c in line)
    bottom = max(float(c["bottom"]) for c in line)
    return {
        "text": text,
        "math_count": math_count,
        "operator_count": operator_count,
        "family_count": family_count,
        "italic_count": italic_count,
        "char_count": len(line),
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
    }


def formula_like(metrics: dict) -> bool:
    """High-recall line gate; false positives are expected here."""
    text = metrics["text"]
    if not text.strip():
        return False
    if DEFINITION_RE.search(text):
        return True
    if metrics["operator_count"] >= 1 and metrics["math_count"] >= 1:
        return True
    if metrics["family_count"] >= 2:
        return True
    # A centered/short line with multiple math-like glyphs is often a display
    # equation even when the PDF uses ordinary Times-Italic for variables.
    if metrics["math_count"] >= 2 and len(text) <= 80:
        return True
    return False


def definition_like(text: str) -> bool:
    return bool(DEFINITION_RE.search(text) or CN_DEFINITION_RE.search(text))


def crop_bbox(chars: list[dict]) -> list[float]:
    return [
        round(min(float(c["x0"]) for c in chars), 3),
        round(min(float(c["top"]) for c in chars), 3),
        round(max(float(c["x1"]) for c in chars), 3),
        round(max(float(c["bottom"]) for c in chars), 3),
    ]


def image_regions(page) -> list[dict]:
    """Expose image/mask regions and compact glyph runs for visual fallback."""
    raw_regions = []
    for idx, image in enumerate(page.images):
        width = float(image.get("width", 0) or 0)
        height = float(image.get("height", 0) or 0)
        # Tiny image masks are commonly individual glyphs in publisher PDFs;
        # large images are figures.  Both are retained, but explicitly typed.
        raw_regions.append(
            {
                "region_id": f"image:{idx}",
                "bbox": [round(float(image["x0"]), 3), round(float(image["top"]), 3),
                         round(float(image["x1"]), 3), round(float(image["bottom"]), 3)],
                "kind": "image_or_glyph_mask" if width < 100 and height < 100 else "large_image",
                "source": "pdf_image_object",
            }
        )
    masks = [r for r in raw_regions if r["kind"] == "image_or_glyph_mask"]
    large = [r for r in raw_regions if r["kind"] == "large_image"]
    # Some publisher PDFs encode every glyph as a tiny image mask.  A formula
    # is then absent from page.chars but its glyph masks still form a compact
    # visual run.  Group nearby masks into local crops; the downstream visual
    # recognizer can inspect only these runs instead of the whole page.
    runs: list[list[dict]] = []
    for item in sorted(masks, key=lambda r: (r["bbox"][1], r["bbox"][0])):
        x0, top, x1, bottom = item["bbox"]
        cy = (top + bottom) / 2
        if not runs:
            runs.append([item])
            continue
        current = runs[-1]
        cx0 = min(r["bbox"][0] for r in current)
        cx1 = max(r["bbox"][2] for r in current)
        ccy = sum((r["bbox"][1] + r["bbox"][3]) / 2 for r in current) / len(current)
        horizontal_gap = x0 - cx1
        if abs(cy - ccy) <= 16 and horizontal_gap <= 18:
            current.append(item)
        else:
            runs.append([item])
    compact = []
    for run_idx, run in enumerate(runs):
        if len(run) < 2:
            continue
        box = [
            round(min(r["bbox"][0] for r in run), 3),
            round(min(r["bbox"][1] for r in run), 3),
            round(max(r["bbox"][2] for r in run), 3),
            round(max(r["bbox"][3] for r in run), 3),
        ]
        compact.append(
            {
                "region_id": f"visual_run:{run_idx}",
                "bbox": box,
                "kind": "image_glyph_run",
                "glyph_count": len(run),
                "source": "pdf_image_objects_grouped",
            }
        )
    return large + compact


def image_glyph_candidates(page, page_no: int) -> list[dict]:
    """Keep per-mask bboxes even when the PDF supplies no character mapping."""
    result = []
    for idx, image in enumerate(page.images):
        width = float(image.get("width", 0) or 0)
        height = float(image.get("height", 0) or 0)
        if width >= 100 or height >= 100:
            continue
        result.append(
            {
                "token_id": f"p{page_no}:image{idx}",
                "page": page_no,
                "surface": None,
                "surface_normalized": None,
                "bbox": [round(float(image["x0"]), 3), round(float(image["top"]), 3),
                         round(float(image["x1"]), 3), round(float(image["bottom"]), 3)],
                "font": None,
                "font_family": None,
                "size": round(height, 3),
                "line_id": "",
                "source": "pdf_image_glyph",
                "status": "unknown_glyph",
            }
        )
    return result


def candidate_for_char(
    c: dict,
    page_no: int,
    source: str,
    line_id: str,
    font_maps: dict[str, dict[int, str]],
) -> dict:
    decoded = decode_surface(c.get("text", ""), c.get("fontname"), font_maps)
    return {
        "token_id": f"p{page_no}:char{c.get('index', -1)}",
        "page": page_no,
        "surface": c.get("text", ""),
        "surface_decoded": decoded,
        "surface_normalized": normalize_surface(decoded),
        "bbox": char_bbox(c),
        "font": c.get("fontname"),
        "font_family": font_family(c.get("fontname")),
        "size": round(float(c.get("size", 0) or 0), 3),
        "line_id": line_id,
        "source": source,
        "status": "candidate",
    }


def analyze_page(page, page_no: int, font_maps: dict[str, dict[int, str]]) -> dict:
    chars = []
    for idx, raw in enumerate(page.chars):
        c = dict(raw)
        c["index"] = idx
        chars.append(c)
    lines = group_lines(chars)
    candidates: list[dict] = []
    formula_lines = []
    definition_lines = []
    seen = set()
    for line_idx, line in enumerate(lines):
        metrics = line_metrics(line)
        if not formula_like(metrics):
            continue
        line_id = f"p{page_no}:line{line_idx}"
        is_def = definition_like(metrics["text"])
        if is_def:
            definition_lines.append({"line_id": line_id, **metrics})
        else:
            formula_lines.append({"line_id": line_id, **metrics})
        source = "definition_line_all_glyphs" if is_def else "formula_line_all_glyphs"
        for c in line:
            key = c["index"]
            if key not in seen:
                candidates.append(candidate_for_char(c, page_no, source, line_id, font_maps))
                seen.add(key)
    # Preserve strong math glyphs even when the surrounding line gate did not
    # fire. This catches isolated Greek/operators and unknown-glyph neighbors.
    for c in chars:
        if is_math_glyph(c) and c["index"] not in seen:
            candidates.append(candidate_for_char(c, page_no, "standalone_math_glyph", "", font_maps))
            seen.add(c["index"])
    return {
        "page": page_no,
        "width": page.width,
        "height": page.height,
        "candidate_chars": candidates,
        "unknown_glyphs": image_glyph_candidates(page, page_no),
        "formula_lines": formula_lines,
        "definition_lines": definition_lines,
        "visual_fallback_regions": image_regions(page),
    }


def render_overlay(pdf_path: Path, pages: list[dict], out_dir: Path) -> None:
    pdf = pdfium.PdfDocument(str(pdf_path))
    by_page = {p["page"]: p for p in pages}
    for page_no, data in by_page.items():
        page = pdf[page_no - 1]
        bitmap = page.render(scale=1.5)
        image = bitmap.to_pil().convert("RGB")
        draw = ImageDraw.Draw(image)
        for c in data["candidate_chars"]:
            x0, top, x1, bottom = c["bbox"]
            color = (220, 30, 30) if "definition" in c["source"] else (30, 110, 220)
            draw.rectangle((x0 * 1.5, top * 1.5, x1 * 1.5, bottom * 1.5), outline=color, width=1)
        for c in data["unknown_glyphs"]:
            x0, top, x1, bottom = c["bbox"]
            draw.rectangle((x0 * 1.5, top * 1.5, x1 * 1.5, bottom * 1.5), outline=(235, 130, 20), width=1)
        for region in data["visual_fallback_regions"]:
            if region["kind"] in {"image_glyph_run", "large_image"}:
                x0, top, x1, bottom = region["bbox"]
                draw.rectangle((x0 * 1.5, top * 1.5, x1 * 1.5, bottom * 1.5), outline=(235, 130, 20), width=2)
        image.save(out_dir / f"page-{page_no:02d}-composite-candidates.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-render", action="store_true", help="skip full-page debug overlays")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    pages = []
    font_counts = Counter()
    font_maps = build_font_maps(args.pdf)
    with pdfplumber.open(str(args.pdf)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            font_counts.update(c.get("fontname", "") for c in page.chars)
            pages.append(analyze_page(page, page_no, font_maps))
    data = {
        "input": str(args.pdf),
        "method": "composite_author_defined_symbol_probe_v1",
        "policy": {
            "candidate_recall": "all glyphs on formula-like or definition-like lines plus standalone math glyphs",
            "semantic_target": "author-defined symbol with local definition evidence",
            "conventional_notation_excluded_later": sorted(CONVENTIONAL_NOTATION),
            "visual_fallback": "image_or_glyph_mask regions are emitted for local rendering/formula recognition",
            "cid_recovery": "ToUnicode first, then Encoding/Differences glyph names; raw surface is retained",
        },
        "totals": {
            "pages": len(pages),
            "candidate_chars": sum(len(p["candidate_chars"]) for p in pages),
            "unknown_glyphs": sum(len(p["unknown_glyphs"]) for p in pages),
            "formula_lines": sum(len(p["formula_lines"]) for p in pages),
            "definition_lines": sum(len(p["definition_lines"]) for p in pages),
            "visual_regions": sum(len(p["visual_fallback_regions"]) for p in pages),
        },
        "fonts": font_counts.most_common(),
        "cid_font_maps": {k: len(v) for k, v in font_maps.items()},
        "pages": pages,
    }
    (args.out / "composite_extraction.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.no_render:
        render_overlay(args.pdf, pages, args.out)
    lines = [
        "# Composite author-defined symbol probe",
        "",
        f"Input: `{args.pdf}`",
        "",
        f"- Candidate chars: {data['totals']['candidate_chars']}",
        f"- Formula-like lines: {data['totals']['formula_lines']}",
        f"- Definition-like lines: {data['totals']['definition_lines']}",
        f"- Visual fallback regions: {data['totals']['visual_regions']}",
        "",
        "This is a high-recall candidate layer. It does not label every candidate as a meaningful symbol.",
        "The semantic layer must require local author-definition evidence and exclude conventional notation.",
    ]
    (args.out / "composite_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
