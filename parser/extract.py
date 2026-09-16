"""Local-first PDF symbol extraction for PhiReader.

The parser intentionally has two distinct products:

* ``candidate_chars`` is a high-recall pool. It keeps every glyph on a
  definition-like or formula-like line, including ordinary italic letters.
* ``symbols`` is the first deterministic final pool. A symbol enters it only
  when it occurs on a definition-like event; single letters and structured
  tokens such as ``p(x)`` are supported, and occurrences elsewhere are then
  linked back to that definition.

No LLM is used here. PDF identity is recovered in this order:
ToUnicode -> Encoding/Differences glyph names -> AGL. An unresolved CID is
kept with its bbox and marked ``unresolved`` instead of being guessed.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pdfplumber
from fontTools.agl import AGL2UV
from pypdf import PdfReader


SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")
DEFINITION_RE = re.compile(
    r"\b(?:where|let|denote(?:d|s)?(?:\s+by)?|define[sd]?|defined\s+as|we\s+use|given|refer\s+to\s+it\s+as|"
    r"definition\s+\d+)\b|"
    r"(?:其中|式中|表示|定义(?:为|是)?|记为|记作|记|设|令|取|分别为|对应于|指的是|称为)",
    re.IGNORECASE,
)
MATH_UNICODE = set(
    "=~+-*/^_<>|∈∉∑√∞±≤≥∂∇∫∏∝≈≠×·∗∀∃∅∪∩⊂⊆⊥→←↔⇒⇔"
    "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
)
MATH_FAMILY_RE = re.compile(
    r"^(?:CMMI|CMMIB|MSBM|MSAM|CMSY|CMEX|MTMI|MTSYN|MTEX|MTSY|"
    r"MT2(?:MI|SY|EX|HR|MIF|MIT|MIS)|Realpage(?:MTIM|TEC|MTEX|SYM|ALL)|"
    r"STIXMath|MathTime|TeX|Symbol|Euclid|txmi|pxmi)",
    re.IGNORECASE,
)
# Computer Modern bold and the bold italic/blackboard companions are math
# alphabets, even though their names do not contain the ``MMI`` marker used by
# the original family expression.  Keep this separate from a generic
# ``.*Bold`` test: publisher bold fonts are also used for headings and figure
# captions, so they need line-level context before being treated as math.
BOLD_MATH_FAMILY_RE = re.compile(
    r"^(?:CMBX|CMMIB|CMBSY|RMTMIB|MSBM|STIXMath(?:-Bold)?|"
    r"MM(?:Greek|Times|Relation|Binary|Etc|aFermat)-?Bold)",
    re.IGNORECASE,
)
ITALIC_RE = re.compile(r"italic|ital|oblique|math|mtmi|cmmi|stix", re.IGNORECASE)
STRUCTURAL_MATH_FAMILY_RE = re.compile(
    r"^(?:CMEX|CMSY|MSAM|MSBM|LMMathSymbols|LMMathExtension)",
    re.IGNORECASE,
)
REFERENCE_HEADING_RE = re.compile(r"^(?:references|bibliography|参考文献)\b", re.IGNORECASE)
LET_DEFINITION_RE = re.compile(
    r"\blet\s+(?!us\b)(?:the\s+[A-Za-zΑ-Ωα-ω][A-Za-zΑ-Ωα-ω-]{0,24}\s+)?"
    r"[A-Za-zΑ-Ωα-ω](?:\s*(?:[,;/]|and)\s*[A-Za-zΑ-Ωα-ω])*\s*"
    r"(?:=|:=|\bbe\b|\bdenot(?:e|es|ed)\b|\brepresent(?:s)?\b|\bstand\s+for\b|"
    r"\bhave\b|\bconsist(?:s)?\b|∈|:|\b[RCNMXY]\b)",
    re.IGNORECASE,
)
CONVENTIONAL_NOTATION = {
    "argmin", "argmax", "min", "max", "sup", "inf", "lim", "sum", "prod",
    "log", "ln", "exp", "sin", "cos", "tan", "det", "diag", "rank",
    "trace", "tr", "span", "sign", "prox", "ker", "dom", "conv",
}

_GREEK_NAMES = {
    "ALPHA": "α", "BETA": "β", "GAMMA": "γ", "DELTA": "δ", "EPSILON": "ε",
    "ZETA": "ζ", "ETA": "η", "THETA": "θ", "IOTA": "ι", "KAPPA": "κ",
    "LAMDA": "λ", "LAMBDA": "λ", "MU": "μ", "NU": "ν", "XI": "ξ",
    "OMICRON": "ο", "PI": "π", "RHO": "ρ", "SIGMA": "σ", "TAU": "τ",
    "UPSILON": "υ", "PHI": "φ", "CHI": "χ", "PSI": "ψ", "OMEGA": "ω",
}


def font_family(font: str | None) -> str:
    return SUBSET_PREFIX.sub("", font or "")


def unicode_math_style(text: str | None) -> str | None:
    """Return the semantic style carried by mathematical Unicode characters."""
    if not text:
        return None
    styles: set[str] = set()
    for char in text:
        name = unicodedata.name(char, "")
        if "MATHEMATICAL" not in name:
            continue
        if "DOUBLE-STRUCK" in name:
            styles.add("blackboard")
        elif "FRAKTUR" in name:
            styles.add("fraktur")
        elif "SCRIPT" in name:
            styles.add("script")
        elif "SANS-SERIF" in name:
            styles.add("sans")
        elif "MONOSPACE" in name:
            styles.add("monospace")
        elif "BOLD" in name and "ITALIC" in name:
            styles.add("bold_italic")
        elif "BOLD" in name:
            styles.add("bold")
        elif "ITALIC" in name:
            styles.add("italic")
        else:
            styles.add("roman")
    if len(styles) == 1:
        return next(iter(styles))
    if styles:
        return "mixed:" + "+".join(sorted(styles))
    return None


def is_math_alphanumeric(text: str | None) -> bool:
    """Return whether text carries Unicode mathematical-alphanumeric style."""
    return bool(text) and any("MATHEMATICAL" in unicodedata.name(char, "") for char in text)


def font_style(font: str | None) -> str:
    """Map common PDF math font families to a semantic, size-independent style.

    The family is retained for unknown fonts instead of guessing that every
    unrecognised glyph is ordinary roman text.  This keeps recall broad while
    preventing unrelated publisher fonts from sharing an identity by accident.
    """
    family = font_family(font)
    upper = family.upper()
    if re.search(r"MSBM|DOUBLE.?STRUCK|BLACKBOARD|BBM", upper):
        return "blackboard"
    if re.search(r"FRAKTUR|EUFM|EUSM|UNIFRAKTUR", upper):
        return "fraktur"
    if re.search(r"SCRIPT|RSFS|CALIGRAPH|CAL", upper):
        return "script"
    if re.search(r"CMMIB|CMBX|BOLD", upper):
        return "bold"
    if re.search(r"CMMI|CMTI|ITALIC|OBLIQUE", upper):
        return "italic"
    if re.search(r"CMR|TIMES.?ROMAN|ROMAN|REGULAR", upper):
        return "roman"
    if not family:
        return "font:unknown"
    return f"font:{family}"


def symbol_style(candidate: dict[str, Any]) -> str:
    """Return the style used as part of a symbol's identity."""
    return candidate.get("style") or unicode_math_style(candidate.get("surface_decoded")) or font_style(
        candidate.get("font_family") or candidate.get("font")
    )


def symbol_identity_key(candidate: dict[str, Any]) -> str | None:
    """Build a stable identity key without merging distinct mathematical styles."""
    surface = symbol_surface(candidate)
    if not surface:
        return None
    return f"{surface}|{symbol_style(candidate)}"


def glyph_name_to_unicode(name: str | None) -> str | None:
    if not name:
        return None
    clean = name.lstrip("/")
    for candidate in (clean, re.sub(r"\d+$", "", clean)):
        if candidate in AGL2UV:
            return chr(AGL2UV[candidate])
        greek = _GREEK_NAMES.get(candidate.upper())
        if greek:
            return greek
    return None


def normalize_surface(text: str | None) -> str | None:
    """Normalize mathematical-alphanumeric Unicode without deleting identity."""
    if not text:
        return text
    output: list[str] = []
    for char in text:
        name = unicodedata.name(char, "")
        if "MATHEMATICAL" not in name:
            output.append(unicodedata.normalize("NFKC", char))
            continue
        tokens = name.split()
        if "LETTER" in tokens:
            if "GREEK" in tokens:
                base = _GREEK_NAMES.get(tokens[-1])
                if base:
                    output.append(base.upper() if "CAPITAL" in tokens else base)
                    continue
            letter = tokens[-1]
            if len(letter) == 1 and letter.isalpha():
                output.append(letter)
                continue
        output.append(unicodedata.normalize("NFKC", char))
    return "".join(output)


def parse_cmap(data: bytes) -> dict[int, str]:
    """Read the two CMap forms used by the Round 5 corpus."""
    text = data.decode("latin1", "ignore")
    result: dict[int, str] = {}
    scalar_pair = re.compile(r"^<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>$")
    scalar_range = re.compile(r"^<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>$")
    section: str | None = None
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
        if section == "char":
            match = scalar_pair.fullmatch(line)
            if match:
                code, value = (int(item, 16) for item in match.groups())
                if value <= 0x10FFFF:
                    result[code] = chr(value)
            continue
        if section == "range":
            match = scalar_range.fullmatch(line)
            if not match:
                continue
            start, end, value = (int(item, 16) for item in match.groups())
            if end < start or end - start > 4096:
                continue
            for code in range(start, end + 1):
                scalar = value + code - start
                if scalar <= 0x10FFFF:
                    result[code] = chr(scalar)
    return result


def build_font_maps(pdf_path: Path) -> dict[str, dict[int, str]]:
    """Build maps keyed by both full BaseFont and subset-stripped family."""
    maps: dict[str, dict[int, str]] = defaultdict(dict)
    reader = PdfReader(str(pdf_path), strict=False)
    for page in reader.pages:
        resources = page.get("/Resources")
        if not resources or "/Font" not in resources:
            continue
        for _, reference in resources["/Font"].items():
            font = reference.get_object()
            base = str(font.get("/BaseFont", "")).lstrip("/")
            if not base:
                continue
            cmap = font.get("/ToUnicode")
            if cmap:
                maps[base].update(parse_cmap(cmap.get_object().get_data()))
            encoding = font.get("/Encoding")
            if hasattr(encoding, "get_object"):
                encoding = encoding.get_object()
            if isinstance(encoding, dict):
                code: int | None = None
                for item in encoding.get("/Differences", []):
                    if isinstance(item, int):
                        code = item
                        continue
                    if code is not None:
                        mapped = glyph_name_to_unicode(str(item))
                        if mapped:
                            maps[base].setdefault(code, mapped)
                        code += 1
            family = font_family(base)
            if family and family != base:
                maps[family].update(maps[base])
    return dict(maps)


def decode_surface(text: str | None, font: str | None, font_maps: dict[str, dict[int, str]]) -> tuple[str | None, str]:
    if not text:
        return text, "empty"
    match = re.fullmatch(r"\(cid:(\d+)\)", text)
    if not match:
        return text, "native"
    code = int(match.group(1))
    family = font_family(font)
    mapped = font_maps.get(family, {}).get(code) or font_maps.get(font or "", {}).get(code)
    return (mapped, "recovered") if mapped else (text, "unresolved")


def filename_metadata(path: Path) -> tuple[str, str, str]:
    """Read the common ``Author - YYYY - Title.pdf`` filename convention.

    This is only a fallback for missing PDF metadata.  A filename that does
    not match the complete convention produces no hints rather than a guess.
    """
    match = re.match(
        r"^\s*(?P<authors>.+?)\s+-\s+(?P<year>(?:19|20)\d{2})\s+-\s+(?P<title>.+?)\s*$",
        path.stem,
    )
    if not match:
        return "", "", ""
    return (
        match.group("authors").strip(),
        match.group("year"),
        match.group("title").strip(),
    )


def metadata(path: Path) -> dict[str, Any]:
    reader = PdfReader(str(path), strict=False)
    info = reader.metadata or {}
    filename_authors, filename_year, filename_title = filename_metadata(path)
    raw_title = str(info.get("/Title") or "").strip()
    # PDF exporters often write a placeholder such as "paper" as the title.
    if raw_title.casefold() in {"paper", "untitled", "document", "microsoft word"}:
        raw_title = ""
    raw_authors = str(info.get("/Author") or "").strip()
    year = None
    year_pattern = re.compile(r"\b(?:19|20)\d{2}\b")
    # Zotero-style filenames are usually the most reliable source for a
    # publication year (``Author - 2024 - Title``). Avoid using PDF creation
    # dates because those often describe a later download or re-export.
    for source in (
        filename_year,
        path.stem,
        str(info.get("/Title") or ""),
        str(info.get("/Subject") or ""),
    ):
        match = year_pattern.search(source)
        if match:
            year = match.group(0)
            break
    if year is None and reader.pages:
        first_page_text = reader.pages[0].extract_text() or ""
        match = year_pattern.search(first_page_text[:8000])
        if match:
            year = match.group(0)
    return {
        "title": raw_title or filename_title or path.stem,
        "authors": raw_authors or filename_authors,
        "producer": str(info.get("/Producer") or ""),
        "creator": str(info.get("/Creator") or ""),
        "year": year or "",
        "pages": len(reader.pages),
        "file_size": path.stat().st_size,
    }


def validate_pdf(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"PDF does not exist: {path}")
    if path.stat().st_size < 5:
        raise ValueError(f"PDF is empty or truncated: {path}")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Input is not a valid PDF file (missing %PDF- header): {path}")
    try:
        reader = PdfReader(str(path), strict=False)
        page_count = len(reader.pages)
        if page_count == 0:
            raise ValueError("PDF contains no pages")
        with pdfplumber.open(str(path)) as document:
            if len(document.pages) != page_count:
                raise ValueError("PDF page count differs between pypdf and pdfplumber")
    except Exception as error:
        raise ValueError(f"PDF could not be opened by both parser backends: {error}") from error


def nonspace(chars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        char
        for char in chars
        if (char.get("text", "") or "").strip()
        and char.get("upright", True) is not False
    ]


def column_boundary(chars: list[dict[str, Any]], page_width: float | None) -> float | None:
    """Find a persistent vertical gutter for event-level two-column cleanup."""
    chars = nonspace(chars)
    if page_width is None or page_width <= 0 or len(chars) < 40:
        return None
    vertical_bands: list[set[int]] = [set() for _ in range(int(page_width) + 1)]
    for char in chars:
        start = max(0, int(float(char.get("x0", 0) or 0)))
        end = min(len(vertical_bands) - 1, int(float(char.get("x1", 0) or 0)))
        if start >= len(vertical_bands):
            continue
        band = int(float(char.get("top", 0) or 0) / 2)
        for index in range(start, min(len(vertical_bands), max(start + 1, end + 1))):
            vertical_bands[index].add(band)
    density = [len(bands) for bands in vertical_bands]
    peak_density = max(density, default=0)
    if peak_density < 2:
        return None
    threshold = max(1, int(peak_density * 0.22))
    gaps: list[tuple[int, int, float]] = []
    index = 1
    while index < len(density) - 1:
        if density[index] > threshold:
            index += 1
            continue
        start = index
        while index < len(density) - 1 and density[index] <= threshold:
            index += 1
        if index - start >= 8:
            gaps.append((start, index, sum(density[start:index]) / (index - start)))
    eligible: list[tuple[int, int, int]] = []
    total = len(chars)
    for start, end, average_density in gaps:
        # A real gutter is almost empty over the page's vertical extent. A
        # single-column page can contain a large equation or figure with a
        # temporary central gap; its average density is much higher. Keep
        # only gaps below half the detection threshold.
        if average_density > peak_density * 0.12:
            continue
        center = (start + end) / 2
        # Column gutters in the supported paper layouts are centered. This
        # rejects page-internal whitespace and side figures on single-column
        # pages without hard-coding a paper's exact margins.
        if abs(center - page_width / 2) > page_width * 0.07:
            continue
        left = sum(float(char.get("x1", 0) or 0) <= start for char in chars)
        right = sum(float(char.get("x0", 0) or 0) >= end for char in chars)
        if left >= total * 0.12 and right >= total * 0.12:
            eligible.append((end - start, start, end))
    if not eligible:
        return None
    _, start, end = max(
        eligible,
        key=lambda item: (item[0], -abs((item[1] + item[2]) / 2 - page_width / 2)),
    )
    return (start + end) / 2


def supports_column_boundary(chars: list[dict[str, Any]], boundary: float | None) -> bool:
    """Return whether a page visibly follows an already known two-column gutter.

    Full-width titles and author blocks can make the page-wide density detector
    reject an otherwise ordinary two-column first page. Reuse a boundary from
    the rest of the document only when several baselines on this page have a
    real gap at that position; continuous single-column lines do not qualify.
    """
    if boundary is None:
        return False
    split_lines = 0
    lines_with_both_sides = 0
    for line in group_lines(chars):
        left = [item for item in line if float(item.get("x1", 0) or 0) < boundary]
        right = [item for item in line if float(item.get("x0", 0) or 0) > boundary]
        if not left or not right:
            continue
        lines_with_both_sides += 1
        gap = min(float(item.get("x0", 0) or 0) for item in right) - max(
            float(item.get("x1", 0) or 0) for item in left
        )
        if gap >= 8.0:
            split_lines += 1
    return split_lines >= 5 and split_lines >= lines_with_both_sides * 0.45


def group_lines(chars: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Keep the historical line stream stable; columns are cleaned per event."""
    lines: list[list[dict[str, Any]]] = []
    for char in sorted(nonspace(chars), key=lambda item: (float(item.get("top", 0)), float(item.get("x0", 0)))):
        size = float(char.get("size", 10) or 10)
        tolerance = max(1.5, min(4.5, size * 0.32))
        if not lines:
            lines.append([char])
            continue
        previous = lines[-1]
        reference = sum(float(item.get("top", 0)) for item in previous) / len(previous)
        if abs(float(char.get("top", 0)) - reference) <= tolerance:
            previous.append(char)
        else:
            lines.append([char])
    for line in lines:
        line.sort(key=lambda item: float(item.get("x0", 0)))
    return lines


def split_definition_line(
    line: list[dict[str, Any]], boundary: float | None,
) -> list[list[dict[str, Any]]]:
    """Split one merged baseline into left/right columns for an event only."""
    if boundary is None:
        return [line]
    columns = [[], []]
    for char in line:
        center = (float(char.get("x0", 0) or 0) + float(char.get("x1", 0) or 0)) / 2
        columns[0 if center < boundary else 1].append(char)
    return [column for column in columns if column]


def definition_event_column(
    line: list[dict[str, Any]], boundary: float | None,
) -> int | None:
    """Choose the column containing definition language and its notation."""
    columns = split_definition_line(line, boundary)
    if len(columns) <= 1:
        if boundary is None or not line:
            return 0
        center = sum(
            (float(char.get("x0", 0) or 0) + float(char.get("x1", 0) or 0)) / 2
            for char in line
        ) / len(line)
        return 0 if center < boundary else 1
    scores: list[tuple[int, int]] = []
    for index, column in enumerate(columns):
        text = line_text(column)
        metrics = line_metrics(column)
        score = 0
        if explicit_definition_signal(text):
            score += 8
        if formula_definition_signal(text):
            score += 3
        if metrics["math_count"]:
            score += 2
        score += min(2, len(line_math_token_candidates(column, 0, "", {})))
        scores.append((score, index))
    best_score, best_index = max(scores)
    return best_index if best_score > 0 else None


def definition_window_column(
    lines: list[list[dict[str, Any]]], window_indexes: list[int], boundary: float | None,
    page: int, font_maps: dict[str, dict[int, str]],
) -> int | None:
    """Choose the physical column containing one complete definition event.

    ``group_lines`` intentionally keeps the historical merged line stream so
    line IDs and the high-recall candidate pool stay stable.  The event itself
    is selected from the whole forward window: a seed line can share a
    baseline with unrelated text in the other column, while the actual
    ``where`` clause or equation appears a few lines later in the target
    column.
    """
    if boundary is None:
        return None
    scores = [0, 0]
    for line_index in window_indexes:
        line = lines[line_index]
        columns: list[list[dict[str, Any]]] = [[], []]
        for char in line:
            center = (float(char.get("x0", 0) or 0) + float(char.get("x1", 0) or 0)) / 2
            columns[0 if center < boundary else 1].append(char)
        for column_index, column in enumerate(columns):
            if not column:
                continue
            text = line_text(column)
            metrics = line_metrics(column)
            candidates = line_math_token_candidates(
                column, page, f"p{page}:line{line_index}", font_maps,
            )
            score = 0
            # A CID/private-font variable can disappear from extracted text,
            # leaving ``where , , and ...``.  The trigger word itself still
            # identifies the definition column and must outrank unrelated
            # author metadata or abstract prose in the other column.
            if re.search(
                r"\b(?:where|let|denote(?:d|s)?|define[sd]?|given)\b|"
                r"(?:其中|式中|表示|定义|记为|记作|设|令|分别为|对应于|称为)",
                text,
                re.IGNORECASE,
            ):
                score += 9
            if explicit_definition_signal(text):
                score += 10
            if formula_definition_signal(text):
                score += 4
            if metrics["operator_count"] and candidates:
                score += 3
            if metrics["family_count"] >= 2:
                score += 2
            score += min(3, len(candidates))
            scores[column_index] += score
    if max(scores) == 0 or scores[0] == scores[1]:
        return definition_event_column(lines[window_indexes[0]], boundary) if window_indexes else None
    return 0 if scores[0] > scores[1] else 1


def definition_event_line(
    line: list[dict[str, Any]], boundary: float | None, selected_column: int | None,
) -> list[dict[str, Any]]:
    """Return only the selected column, excluding singleton lines from the other column."""
    if boundary is None or selected_column is None:
        return line
    columns: list[list[dict[str, Any]]] = [[], []]
    for char in line:
        center = (float(char.get("x0", 0) or 0) + float(char.get("x1", 0) or 0)) / 2
        columns[0 if center < boundary else 1].append(char)
    if selected_column not in (0, 1) or not columns[selected_column]:
        return []
    return columns[selected_column]


def extend_event_to_sentence_end(
    indexes: list[int], lines: list[list[dict[str, Any]]], boundary: float | None,
    selected_column: int | None,
) -> list[int]:
    """Finish a definition sentence split by interleaved two-column lines."""
    if not indexes:
        return indexes
    selected_text = " ".join(
        line_text(definition_event_line(lines[index], boundary, selected_column))
        for index in indexes
    ).strip()
    if not (
        (explicit_definition_signal(selected_text) or formula_definition_signal(selected_text))
        and not re.search(r"[.!?。！？]\s*$", selected_text)
    ):
        return indexes
    result = list(indexes)
    for index in range(indexes[-1] + 1, min(len(lines), indexes[-1] + 6)):
        result.append(index)
        continuation = line_text(definition_event_line(lines[index], boundary, selected_column)).strip()
        if continuation:
            selected_text = f"{selected_text} {continuation}"
            if re.search(r"[.!?。！？](?:\s|$)", continuation):
                break
    return result


def line_text(line: list[dict[str, Any]]) -> str:
    output: list[str] = []
    previous: dict[str, Any] | None = None
    for char in line:
        text = char.get("decoded", char.get("text", "")) or ""
        if previous is not None and text.strip() and (previous.get("decoded", previous.get("text", "")) or "").strip():
            gap = float(char.get("x0", 0)) - float(previous.get("x1", 0))
            size = min(float(char.get("size", 10) or 10), float(previous.get("size", 10) or 10))
            if gap > max(0.8, min(3.0, size * 0.16)):
                output.append(" ")
        output.append(text)
        previous = char
    return "".join(output)


def bbox(chars: list[dict[str, Any]]) -> list[float]:
    return [
        round(min(float(char["x0"]) for char in chars), 3),
        round(min(float(char["top"]) for char in chars), 3),
        round(max(float(char["x1"]) for char in chars), 3),
        round(max(float(char["bottom"]) for char in chars), 3),
    ]


def boxes_overlap(left: list[float], right: list[float]) -> bool:
    return max(left[0], right[0]) < min(left[2], right[2]) and max(left[1], right[1]) < min(left[3], right[3])


def is_math_glyph(char: dict[str, Any]) -> bool:
    text = char.get("decoded", char.get("text", "")) or ""
    family = font_family(char.get("fontname"))
    return bool(
        MATH_FAMILY_RE.search(family)
        or BOLD_MATH_FAMILY_RE.search(family)
        or is_math_alphanumeric(text)
        or any(item in MATH_UNICODE for item in text)
    )


def is_bold_math_glyph(char: dict[str, Any]) -> bool:
    """Return whether a glyph is a strong, known mathematical bold alphabet."""
    text = char.get("decoded", char.get("text", "")) or ""
    family = font_family(char.get("fontname"))
    style = unicode_math_style(text)
    return bool(
        BOLD_MATH_FAMILY_RE.search(family)
        or style in {"bold", "bold_italic", "blackboard"}
    )


def line_metrics(line: list[dict[str, Any]]) -> dict[str, Any]:
    text = line_text(line)
    return {
        "text": text,
        "math_count": sum(is_math_glyph(char) for char in line),
        "operator_count": sum(any(item in MATH_UNICODE for item in (char.get("decoded", char.get("text", "")) or "")) for char in line),
        "family_count": sum(bool(MATH_FAMILY_RE.search(font_family(char.get("fontname")))) for char in line),
        "italic_count": sum(bool(ITALIC_RE.search(font_family(char.get("fontname")))) for char in line),
        "char_count": len(line),
        "bbox": bbox(line),
    }


def contextual_bold_math_glyph(line: list[dict[str, Any]], index: int) -> bool:
    """Treat generic publisher bold as math only with local math evidence.

    ``TimesLTStd-Bold`` and ``FormataOTF-Bold`` are used for both notation and
    ordinary headings/captions.  The font name alone is therefore a weak
    signal.  A definition trigger or an already math-looking line is required
    before such a glyph can enter the token candidate stream.
    """
    if not 0 <= index < len(line):
        return False
    char = line[index]
    if is_bold_math_glyph(char):
        return True
    text = char.get("decoded", char.get("text", "")) or ""
    if len(text) != 1 or not text.isalpha():
        return False
    style = font_style(char.get("fontname"))
    if style not in {"bold", "bold_italic"}:
        return False
    metrics = line_metrics(line)
    if definition_like(metrics["text"]):
        return True
    # A generic bold glyph is admitted on a formula-like line only when the
    # line also contains independent mathematical evidence.  This avoids
    # promoting the uppercase letters of bold section headings and captions.
    return bool(
        metrics["operator_count"] >= 1
        and (metrics["math_count"] >= 1 or metrics["italic_count"] >= 1 or metrics["family_count"] >= 1)
    )


def definition_like(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return False
    if re.search(r"\bdefinition\s+\d+\b", normalized, re.IGNORECASE):
        return True
    if re.search(
        r"(?:其中|式中|表示|定义(?:为|是)?|记为|记作|记|设|令|取|分别为|对应于|指的是|称为)",
        normalized,
    ):
        return True

    # A bare "where" also occurs in ordinary prose ("the case where ...").
    # Accept it only when the clause starts with a notation-like subject.
    if re.search(r"\bwhere\s+(?:[A-Za-zΑ-Ωα-ω]\b|[A-Za-zΑ-Ωα-ω]\s*[\(\[\{])", normalized, re.IGNORECASE):
        return True
    # ``Let x > 0`` is a theorem assumption, not a notation definition.
    # Accept ``let`` only when the sentence assigns a value, type, or role to
    # the symbol.  This prevents a later theorem hypothesis from pulling the
    # preceding formula into a false definition event.
    if LET_DEFINITION_RE.search(normalized):
        return True
    if re.search(r"\b(?:denote|denotes|denoted|define|defines|defined|defined\s+as)\b", normalized, re.IGNORECASE):
        return True
    if re.search(r"\bwe\s+use\s+(?:[A-Za-zΑ-Ωα-ω]\b|[A-Za-zΑ-Ωα-ω]\s*[\(\[\{])", normalized, re.IGNORECASE):
        return True
    if (
        re.search(r"\bgiven\s+(?:a|an|the)\b", normalized, re.IGNORECASE)
        or re.search(r"\bgiven\s+[A-ZΑ-Ω]\b", normalized)
    ):
        return True
    # Some papers explain a model with ``where we observe ...`` instead of
    # putting the assignment immediately after ``where``.
    if re.search(r"\bwhere\s+we\s+(?:observe|consider|have|use|denote|define|assume)\b", normalized, re.IGNORECASE):
        return True
    # ``we say that x is atomic`` is an explicit author definition even
    # though it does not use the usual ``define``/``denote`` vocabulary.
    if re.search(
        r"\bwe\s+say\s+that\s+(?:[A-Za-zΑ-Ωα-ω]\b|[A-Za-zΑ-Ωα-ω]\s*[\(\[\{])\s+"
        r"(?:is|are)\b",
        normalized,
        re.IGNORECASE,
    ):
        return True
    # Role nouns are a common compact notation style: ``the matrix A`` or
    # ``the steps σ and τ``. Keep this vocabulary narrow so ordinary prose
    # does not become a definition event.
    notation_nouns = (
        r"(?:matrix|matrices|vector|vectors|function|functions|operator|"
        r"parameter|parameters|constant|constants|variable|variables|"
        r"steps?|coefficient|coefficients|dimension|dimensions|"
        r"random variable|set|sets|map|mapping|distribution|"
        r"penalty|penalties|term|terms)"
    )
    symbol = r"[A-Za-zΑ-Ωα-ω](?:\s*(?:[,;/]|and)\s*[A-Za-zΑ-Ωα-ω])*"
    role_tail = r"(?:\s*(?:[=∈⊂:;,]|is\b|are\b|can\b|will\b|denotes?\b|represents?\b))"
    if re.search(rf"\b{notation_nouns}\s+{symbol}\b{role_tail}", normalized, re.IGNORECASE):
        return True
    # Adjectival definitions such as ``x is sparse`` and ``x is atomic``.
    if re.search(
        r"\b[A-Za-zΑ-Ωα-ω]\b\s+(?:is|are)\s+"
        r"(?:sparse|atomic|non[- ]?negative|positive|negative|real|complex|"
        r"orthogonal|independent|identical|differentiable|convex|concave|"
        r"normalized|normalised|bounded|continuous|uniformly convex|"
        r"identity|nonzero|zero|finite|infinite)\b",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if re.search(r"\brefer\s+to\s+(?:it|this|these|them)\s+as\b", normalized, re.IGNORECASE):
        return True

    # The weak verbs are accepted only as part of an explicit assignment.
    # This rejects prose such as "we assume ..." and "we mean ..." while
    # retaining sentences like "A represents the mixing matrix".
    return bool(
        re.search(
            r"(?:\b[A-Za-zΑ-Ωα-ω]\b|\([^)]{1,24}\))\s+"
            r"(?:is|are|represents?|corresponds?|refers?)\s+"
            r"(?:a|an|the|used|chosen|defined|called|the|our)\b",
            normalized,
            re.IGNORECASE,
        )
    )


def formula_like(metrics: dict[str, Any]) -> bool:
    text = metrics["text"]
    if not text.strip():
        return False
    if definition_like(text):
        return True
    if metrics["operator_count"] >= 1 and metrics["math_count"] >= 1:
        return True
    if metrics["family_count"] >= 2:
        return True
    return metrics["math_count"] >= 2 and len(text) <= 80


def image_regions(page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for index, image in enumerate(page.images):
        width = float(image.get("width", 0) or 0)
        height = float(image.get("height", 0) or 0)
        # A number of TeX/PDF producers encode rules and table borders as
        # image objects.  Their extreme aspect ratio is detectable without
        # looking at pixels; they are not formula regions and must not create
        # visual-review work for the user.
        if width <= 0 or height <= 0:
            continue
        if width >= 20 * height or height >= 20 * width:
            continue
        regions.append({
            "region_id": f"image:{index}",
            "bbox": [round(float(image["x0"]), 3), round(float(image["top"]), 3), round(float(image["x1"]), 3), round(float(image["bottom"]), 3)],
            "kind": "image_or_glyph_mask" if width < 100 and height < 100 else "large_image",
            "source": "pdf_image_object",
        })
    return regions


def unresolved_identity_review(candidate: dict[str, Any]) -> bool:
    """Return whether an unresolved glyph can plausibly be an identifier.

    CMEX/CMSY/MSAM and the corresponding Latin Modern symbol/extension
    fonts are primarily delimiters and operators.  Their unresolved glyphs
    still remain in the high-recall candidate pool, but asking for visual
    identity confirmation on every bracket or rule creates unusable noise.
    Math-italic and unknown fonts remain reviewable because they can encode a
    variable or a user-defined function.
    """
    if candidate.get("identity_status") != "unresolved":
        return False
    return not STRUCTURAL_MATH_FAMILY_RE.search(font_family(candidate.get("font_family")))


def char_record(char: dict[str, Any], page: int, line_id: str, source: str, font_maps: dict[str, dict[int, str]]) -> dict[str, Any]:
    raw = char.get("text", "") or ""
    decoded, identity = decode_surface(raw, char.get("fontname"), font_maps)
    normalized = normalize_surface(decoded)
    style = unicode_math_style(decoded) or font_style(char.get("fontname"))
    return {
        "token_id": f"p{page}:char{char.get('index', -1)}",
        "page": page,
        "surface": raw,
        "surface_decoded": decoded,
        "surface_normalized": normalized,
        "identity_status": identity,
        "style": style,
        "bbox": [round(float(char.get(key, 0) or 0), 3) for key in ("x0", "top", "x1", "bottom")],
        "font": char.get("fontname"),
        "font_family": font_family(char.get("fontname")),
        "size": round(float(char.get("size", 0) or 0), 3),
        "line_id": line_id,
        "source": source,
    }


def symbol_surface(candidate: dict[str, Any]) -> str | None:
    surface = candidate.get("surface_normalized")
    if not surface or candidate.get("identity_status") == "unresolved":
        return None
    if len(surface) == 1 and surface.isalpha():
        return surface
    if candidate.get("token_kind") != "composite":
        return None
    if not surface[0].isalpha() or not re.search(r"[()[\]{}|_^]", surface):
        return None
    return surface


def separated_single_letter(line: list[dict[str, Any]], index: int) -> bool:
    """Return whether a glyph is a one-letter visual token, not prose text."""
    char = line[index]
    size = float(char.get("size", 10) or 10)
    threshold = max(1.0, min(3.0, size * 0.14))
    previous_gap = float(char["x0"]) - float(line[index - 1]["x1"]) if index else float("inf")
    next_gap = float(line[index + 1]["x0"]) - float(char["x1"]) if index + 1 < len(line) else float("inf")
    return previous_gap > threshold and next_gap > threshold


def neighboring_tokens(line: list[dict[str, Any]], index: int) -> tuple[str, str]:
    """Get the nearest visual words on either side of a singleton glyph."""
    char = line[index]
    size = float(char.get("size", 10) or 10)
    threshold = max(1.0, min(3.0, size * 0.14))

    def gap(left: int, right: int) -> float:
        return float(line[right]["x0"]) - float(line[left]["x1"])

    start = index
    while start and gap(start - 1, start) <= threshold:
        start -= 1
    end = index
    while end + 1 < len(line) and gap(end, end + 1) <= threshold:
        end += 1

    previous = end_token = ""
    if start:
        previous_end = start - 1
        previous_start = previous_end
        while previous_start and gap(previous_start - 1, previous_start) <= threshold:
            previous_start -= 1
        previous = "".join((item.get("decoded", item.get("text", "")) or "") for item in line[previous_start:previous_end + 1])
    if end + 1 < len(line):
        next_start = end + 1
        next_end = next_start
        while next_end + 1 < len(line) and gap(next_end, next_end + 1) <= threshold:
            next_end += 1
        end_token = "".join((item.get("decoded", item.get("text", "")) or "") for item in line[next_start:next_end + 1])
    return previous.lower(), end_token.lower()


def is_single_letter_token(line: list[dict[str, Any]], index: int, candidate: dict[str, Any]) -> bool:
    """Recognize a one-glyph math token without treating word letters as symbols."""
    surface = symbol_surface(candidate)
    if not surface:
        return False
    if index < 0:
        return False
    previous = line[index - 1].get("decoded", "") if index else ""
    following = line[index + 1].get("decoded", "") if index + 1 < len(line) else ""
    size = float(candidate.get("size", 10) or 10)
    # Times-style PDFs often leave only ~0.22em between a prose word and an
    # italic one-letter notation token (for example ``Usually r is ...``).
    # The previous 0.24em cutoff merged that token into the preceding word.
    gap_threshold = max(2.0, min(4.5, size * 0.18))
    left_gap = float(line[index]["x0"]) - float(line[index - 1]["x1"]) if index else float("inf")
    right_gap = float(line[index + 1]["x0"]) - float(line[index]["x1"]) if index + 1 < len(line) else float("inf")
    family = candidate.get("font_family", "")
    current_top = float(line[index].get("top", 0) or 0)

    def is_script_pair(neighbor_index: int) -> bool:
        if not 0 <= neighbor_index < len(line):
            return False
        neighbor = line[neighbor_index]
        neighbor_text = neighbor.get("decoded", neighbor.get("text", "")) or ""
        if len(neighbor_text) != 1 or not neighbor_text.isalpha():
            return False
        if not is_math_glyph(line[index]) or not is_math_glyph(neighbor):
            return False
        neighbor_size = float(neighbor.get("size", size) or size)
        size_ratio = min(size, neighbor_size) / max(size, neighbor_size)
        vertical_shift = abs(current_top - float(neighbor.get("top", current_top) or current_top))
        horizontal_gap = (
            float(neighbor.get("x0", 0)) - float(line[index].get("x1", 0))
            if neighbor_index > index
            else float(line[index].get("x0", 0)) - float(neighbor.get("x1", 0))
        )
        return horizontal_gap <= max(1.5, size * 0.22) and (size_ratio <= 0.88 or vertical_shift >= 0.8)

    # PDF text extraction often drops the underscore/caret from indexed
    # variables while preserving the smaller, vertically shifted glyph.  A
    # math-font script pair (m_j, x_M, R_j) consists of two valid symbol
    # identities even though their horizontal boxes touch like a word.
    if is_script_pair(index - 1) or is_script_pair(index + 1):
        return True
    if surface == "x" and previous.isalpha() and following.isalpha() and left_gap > gap_threshold and right_gap > gap_threshold:
        # Times-style dimension notation such as "n x m" is a multiplication
        # marker, not the variable x.
        return False
    if surface.isupper() and bool(ITALIC_RE.search(family)) and left_gap > gap_threshold:
        # Some PDF producers omit the visual space in "W and H" or "Wh".
        # An uppercase italic glyph with a real left token gap is still a
        # standalone mathematical token.
        return True
    if (previous.isalpha() and left_gap <= gap_threshold) or (following.isalpha() and right_gap <= gap_threshold):
        # A glyph joined to another letter belongs to a prose word or a
        # multi-letter identifier. A wide PDF word gap is a token boundary.
        compact_text = "".join((item.get("decoded", "") or "") for item in line)
        if not (len(compact_text) <= 32 and any(operator in compact_text for operator in "=~≈∈∑∫") and surface.isupper()):
            return False
    if previous in "=~≈×+/()[]{}∈∑∫|" or following in "=~≈×+/()[]{}∈∑∫|":
        return True
    if following == "-" and index + 2 < len(line):
        suffix = "".join((item.get("decoded", "") or "") for item in line[index + 2:index + 14]).lower()
        if suffix.startswith("dimensional"):
            return left_gap >= gap_threshold
    wide_side = left_gap >= gap_threshold or right_gap >= gap_threshold
    if not wide_side:
        return False
    if surface.lower() in {"a", "an"}:
        _, next_token = neighboring_tokens(line, index)
        return next_token in {"is", "are", "means", "denotes", "denote", "represents", "represent", "called"}
    return surface.isupper() or bool(ITALIC_RE.search(candidate.get("font_family", ""))) or (left_gap >= gap_threshold and right_gap >= gap_threshold)


def likely_definition_symbol(candidate: dict[str, Any], line: list[dict[str, Any]]) -> bool:
    index = next((idx for idx, char in enumerate(line) if char.get("index") == candidate.get("_index")), -1)
    return is_single_letter_token(line, index, candidate)


def line_symbol_candidates(
    line: list[dict[str, Any]], page: int, line_id: str, font_maps: dict[str, dict[int, str]]
) -> list[dict[str, Any]]:
    """Return deterministic single-letter math candidates for one PDF line."""
    result: list[dict[str, Any]] = []
    plain_definition_line = definition_like(line_text(line))
    plain_right_context = {
        "is", "are", "denotes", "denote", "represents", "represent",
        "means", "mean", "matrix", "vector", "function", "set", "number",
        "dimension", "dimensional", "parameter", "variable", "index",
        "coefficient", "coefficients", "column", "row",
    }
    plain_left_context = {
        "where", "let", "denote", "by", "as", "and", "or", "x", "×",
        "atom", "atoms", "matrix", "matrices", "vector", "vectors", "set",
        "function", "operator", "parameter", "variable", "dictionary",
        "group", "groups", "space", "spaces", "source", "sources",
    }
    for index, char in enumerate(line):
        # A definition/formula line may contain ordinary prose.  Contextual
        # spacing alone is not enough to promote its letters: require either
        # a math font or an explicitly italic/oblique font, while retaining
        # standalone Unicode mathematical characters in ordinary fonts.
        math_or_italic = (
            is_math_glyph(char)
            or ITALIC_RE.search(font_family(char.get("fontname")))
            or contextual_bold_math_glyph(line, index)
        )
        if not math_or_italic:
            previous_token, next_token = neighboring_tokens(line, index)
            plain_assignment = (
                plain_definition_line
                and separated_single_letter(line, index)
                and (
                    previous_token in plain_left_context
                    or (
                        next_token in plain_right_context
                        and bool(previous_token)
                    )
                )
            )
            if not plain_assignment:
                continue
        candidate = char_record(char, page, line_id, "definition_event", font_maps)
        candidate["_index"] = char["index"]
        candidate["token_kind"] = "single_letter"
        # A few publishers keep set names such as ``A`` in a roman font. If
        # the preceding visual word is a notation noun (``atoms A`` or
        # ``matrix A``), the boundary itself is stronger evidence than the
        # generic singleton prose guard.
        if (
            not math_or_italic
            and candidate["surface_normalized"]
            and candidate["surface_normalized"].isupper()
            and previous_token in plain_left_context
        ):
            result.append(candidate)
            continue
        if likely_definition_symbol(candidate, line):
            result.append(candidate)
    return result


def composite_token_candidates(
    line: list[dict[str, Any]], page: int, line_id: str, font_maps: dict[str, dict[int, str]]
) -> list[dict[str, Any]]:
    """Return structured math tokens such as ``p(x)`` and ``K_nu``.

    The span must start with a math-looking letter and contain an explicit
    mathematical delimiter.  This prevents ordinary prose words from being
    promoted while retaining common function and indexed-variable notation.
    """
    result: list[dict[str, Any]] = []
    for start, char in enumerate(line):
        decoded = char.get("decoded", char.get("text", "")) or ""
        if len(decoded) != 1 or not decoded.isalpha() or not (
            is_math_glyph(char) or contextual_bold_math_glyph(line, start)
        ):
            continue
        if start:
            previous = line[start - 1]
            previous_text = previous.get("decoded", previous.get("text", "")) or ""
            gap = float(char.get("x0", 0)) - float(previous.get("x1", 0))
            size = min(float(char.get("size", 10) or 10), float(previous.get("size", 10) or 10))
            if gap <= max(2.5, min(6.0, size * 0.55)) and (
                is_math_glyph(previous)
                or contextual_bold_math_glyph(line, start - 1)
                or previous_text.isalpha()
            ):
                continue
        end = start
        while end + 1 < len(line):
            previous = line[end]
            following = line[end + 1]
            gap = float(following.get("x0", 0)) - float(previous.get("x1", 0))
            size = min(float(previous.get("size", 10) or 10), float(following.get("size", 10) or 10))
            if gap > max(2.5, min(6.0, size * 0.55)):
                break
            following_text = following.get("decoded", following.get("text", "")) or ""
            previous_text = previous.get("decoded", previous.get("text", "")) or ""
            if previous_text in ")]}":
                break
            if not (
                (
                    following_text.isalpha()
                    and (
                        is_math_glyph(following)
                        or contextual_bold_math_glyph(line, end + 1)
                    )
                )
                or following_text.isdigit()
                or following_text in "()[]{}|,_^"
            ):
                break
            end += 1
        if end == start:
            continue
        span = line[start:end + 1]
        surfaces = [item.get("decoded", item.get("text", "")) or "" for item in span]
        joined = "".join(surfaces)
        structure = re.search(r"[\(\[\{_^]", joined)
        if not structure or len(joined[:structure.start()]) != 1:
            continue
        if not (joined[-1].isalnum() or joined[-1] in ")]}"):
            continue
        if any(joined.count(opening) != joined.count(closing) for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"))):
            continue
        records = [char_record(item, page, line_id, "composite_math_token", font_maps) for item in span]
        if any(item["identity_status"] == "unresolved" for item in records):
            continue
        candidate = {
            "token_id": f"p{page}:token:{line_id}:{start}-{end}",
            "page": page,
            "surface": joined,
            "surface_decoded": joined,
            "surface_normalized": normalize_surface(joined),
            "identity_status": "recovered" if any(item["identity_status"] == "recovered" for item in records) else "native",
            "bbox": bbox(span),
            "font": records[0]["font"],
            "font_family": records[0]["font_family"],
            "style": next(iter({item["style"] for item in records}))
            if len({item["style"] for item in records}) == 1
            else "mixed:" + "+".join(sorted({item["style"] for item in records})),
            "size": max(item["size"] for item in records),
            "line_id": line_id,
            "source": "composite_math_token",
            "token_kind": "composite",
        }
        candidate["identity_key"] = symbol_identity_key(candidate)
        if symbol_surface(candidate):
            result.append(candidate)
    return result


def line_math_token_candidates(
    line: list[dict[str, Any]], page: int, line_id: str, font_maps: dict[str, dict[int, str]]
) -> list[dict[str, Any]]:
    return line_symbol_candidates(line, page, line_id, font_maps) + composite_token_candidates(line, page, line_id, font_maps)


def explicit_definition_signal(text: str) -> bool:
    """Use the same explicit assignment gate for seed and line detection."""
    return definition_like(text)


def formula_definition_signal(text: str) -> bool:
    """Detect definition wording suitable for anchoring a preceding formula.

    A formula should not inherit symbols from a later sentence merely because
    that sentence contains a weak assignment such as ``G is an auxiliary
    function``.  Formula anchoring therefore accepts only explicit notation
    cues; ordinary assignment sentences are handled as their own line.
    """
    normalized = re.sub(r"\s+", " ", text).strip()
    if re.search(
        r"\bwhere\s+(?:[A-Za-zΑ-Ωα-ω]\b|[A-Za-zΑ-Ωα-ω]\s*[\(\[\{])",
        normalized,
        re.IGNORECASE,
    ):
        return True
    # Some PDF text layers place subscripted subjects on the preceding visual
    # line and leave only ``where and represent ...`` on this one. This is a
    # relation to nearby notation, not a standalone definition sentence.
    if re.search(
        r"\bwhere\s+(?:and\s+)?(?:represent(?:s|ed)?|denote(?:s|d)?)\b",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if LET_DEFINITION_RE.search(normalized):
        return True
    if re.search(
        r"\b(?:denote|denotes|denoted|define|defines|defined|defined\s+as|refer\s+to\s+it\s+as)\b",
        normalized,
        re.IGNORECASE,
    ):
        return True
    if re.search(r"(?:其中|式中|表示|定义(?:为|是)?|记为|记作|记|设|令|取|分别为|对应于|指的是|称为)", normalized):
        return True
    return bool(
        re.search(r"\bgiven\s+(?:a|an|the)\b", normalized, re.IGNORECASE)
        or re.search(r"\bgiven\s+[A-ZΑ-Ω]\b", normalized)
    )


def evidence_type_for_level(level: int) -> str:
    """Give every event symbol a machine-readable evidence category."""
    if level >= 3:
        return "direct_definition"
    if level == 2:
        return "formula_context"
    return "event_context"


def is_final_pool_evidence(level: int) -> bool:
    """Return whether event evidence is strong enough for the final pool."""
    return level >= 2


def visual_words(line: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
    """Return visual word spans with PDF x coordinates.

    ``line_text`` inserts spaces from glyph gaps, but the resulting string no
    longer tells us where a definition trigger sits on the page.  Grouping
    adjacent alphabetic glyphs lets the event extractor keep the clause after
    ``where``/``let`` while dropping the unrelated formula prefix on the same
    PDF line.
    """
    words: list[tuple[str, float, float]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if current:
            words.append((
                "".join((item.get("decoded", item.get("text", "")) or "") for item in current).lower(),
                float(current[0].get("x0", 0) or 0),
                float(current[-1].get("x1", 0) or 0),
            ))
            current.clear()

    for item in line:
        text = item.get("decoded", item.get("text", "")) or ""
        if len(text) != 1 or not text.isalpha():
            flush()
            continue
        if current:
            gap = float(item.get("x0", 0) or 0) - float(current[-1].get("x1", 0) or 0)
            size = min(float(item.get("size", 10) or 10), float(current[-1].get("size", 10) or 10))
            if gap > max(1.5, min(4.0, size * 0.32)):
                flush()
        current.append(item)
    flush()
    return words


def definition_clause_candidates(
    line: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep all valid symbols from a definition/formula line.

    Earlier versions removed glyphs before ``where``/``given``. That is
    unsafe because the prefix may be the equation that introduces the symbol,
    and pdfplumber may also interleave two columns on one extracted line.
    Precision is handled by the math-token and definition-event gates; this
    layer must remain high-recall.
    """
    return candidates


def is_generic_definition_sentence(text: str) -> bool:
    return bool(re.search(r"\bdefine\s+(?:the\s+)?(?:cost|objective|loss|utility)\s+function", text, re.IGNORECASE))


def definition_meaning(surface: str, text: str) -> str:
    """Extract a short deterministic gloss from the author's definition text."""
    escaped = re.escape(surface)
    english_patterns = (
        # Type annotations are common in mathematical prose (``g : X -> R is
        # a convex function``).  Allow the bounded annotation, but never cross
        # a sentence/colon boundary or consume the next definition clause.
        rf"(?<![A-Za-z]){escaped}(?:\s*\([^)]{{0,40}}\))?\s*(?::\s*[^,.;:，。；：]{{1,80}})?\s+(?:is\s+called|defined\s+as|refers?\s+to|denotes?|means?|represents?|is|are)\s+(?P<value>[^,.;:，。；：]{{3,100}})",
        rf"(?<![A-Za-z]){escaped}\s*-\s*dimensional\s+(?P<value>[^,.;:，。；：]{{3,80}})",
    )
    chinese_patterns = (
        rf"{escaped}[^。；;]{{0,40}}?(?:为|是|表示|定义为|记为|称为)\s*(?P<value>[^，。；;]{{2,80}})",
    )
    for pattern in (*english_patterns, *chinese_patterns):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            if pattern in english_patterns:
                prefix = text[max(0, match.start() - 12):match.start()]
                if re.search(
                    r"(?<![A-Za-zΑ-Ωα-ω])[A-Za-zΑ-Ωα-ω]\s+$",
                    prefix,
                ):
                    continue
            value = re.sub(r"\s+", " ", match.group("value")).strip(" ,.;，。；：:")
            # PDF extraction can leave an arrow, equation tail, or the next
            # conjunction attached to an otherwise valid gloss.
            value = re.split(r"\s+(?:where|while|and\s+[A-Za-zΑ-Ωα-ω]\s+(?:is|are)\b)", value, maxsplit=1, flags=re.IGNORECASE)[0]
            value = value.strip(" ,.;，。；：:")
            if re.match(r"(?:defined|deﬁned)\s+by\b", value, re.IGNORECASE):
                continue
            if any(marker in value for marker in ("def", "=def", "∈", "×", "→", "{", "}")):
                # These are equation tails, not a human-readable gloss.
                continue
            if "ℓ" in value and not re.search(r"\bnorm\b", value, re.IGNORECASE):
                value = f"{value} norm"
            if len(value) > 40:
                value = value[:37].rstrip() + "…"
            if value:
                return value
    return "作者定义的数学符号"


def definition_summary(surface: str, text: str) -> str:
    """Return a compact local fallback when no model summary is available."""
    meaning = definition_meaning(surface, text)
    if meaning != "作者定义的数学符号":
        return meaning
    normalized = re.sub(r"\s+", " ", text).strip(" \t\r\n.,;:，。；：")
    if not normalized or any(marker in normalized for marker in ("(1.1)", "(1.4)", "(2.2)", "=def", "→", "∈")):
        return "作者定义的数学符号"
    if len(normalized) > 40:
        normalized = normalized[:37].rstrip() + "…"
    return normalized


def definition_excerpt(surface: str, text: str) -> str:
    """Build a bounded prompt excerpt without leaking an entire event window."""
    meaning = definition_meaning(surface, text)
    if meaning != "作者定义的数学符号":
        return f"{surface}: {meaning}"
    normalized = re.sub(r"\s+", " ", text).strip(" \t\r\n.,;:，。；：")
    if not normalized:
        return ""
    # Keep only a small local clause around the symbol.  This is context for a
    # model, not a fallback meaning, so it is deliberately capped.
    match = re.search(rf"(?<![A-Za-z]){re.escape(surface)}(?![A-Za-z])", normalized)
    if match:
        start = max(0, match.start() - 160)
        end = min(len(normalized), match.end() + 540)
        return normalized[start:end].strip()
    return normalized[:700]


def symbol_evidence_text(
    surface: str,
    line_index: int,
    window_indexes: list[int],
    lines: list[list[dict[str, Any]]],
    line_records: list[dict[str, Any]],
    boundary: float | None,
    selected_column: int | None,
) -> str:
    """Build bounded evidence for one symbol instead of the whole event.

    A definition event may introduce many symbols in one paragraph.  The
    event remains the grouping unit, but each symbol gets its own line-local
    prompt evidence plus at most two nearby definition/formula lines.  This
    preserves split equations while preventing later, unrelated clauses from
    being reused as that symbol's definition.
    """
    try:
        position = window_indexes.index(line_index)
    except ValueError:
        position = 0
    indexes = window_indexes[position:position + 3]
    parts: list[str] = []
    all_parts: list[str] = []
    for index in window_indexes:
        selected = definition_event_line(lines[index], boundary, selected_column)
        if selected:
            text = line_text(selected).strip()
            if text:
                all_parts.append(text)
    all_text = re.sub(r"\s+", " ", " ".join(all_parts)).strip()
    relation = re.search(
        r"\b(?:where|here|let|given)\b|(?:其中|式中|表示|定义|记为|设|令)",
        all_text,
        re.IGNORECASE,
    )
    relation_clause = ""
    if relation:
        relation_clause = all_text[relation.start():]
        sentence_end = re.search(r"[.;。；](?:\s|$)", relation_clause)
        if sentence_end:
            relation_clause = relation_clause[:sentence_end.end()].strip()
        relation_clause = relation_clause[:720].strip()
    for offset, index in enumerate(indexes):
        selected = definition_event_line(lines[index], boundary, selected_column)
        if not selected:
            continue
        text = line_text(selected).strip()
        if not text:
            continue
        if index != line_index and line_records[line_index]["definition_like"] and line_records[index]["definition_like"]:
            break
        if index != line_index and not (
            line_records[index]["definition_like"]
            or line_records[index]["formula_like"]
            or formula_definition_signal(" ".join(parts + [text]))
        ):
            break
        parts.append(text)
        if len(" ".join(parts)) >= 720:
            break
    evidence = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if relation_clause and (surface in relation_clause or re.search(r"\b(?:and|represent|denote|is|are)\b", relation_clause, re.IGNORECASE)):
        evidence = relation_clause if not evidence else f"{evidence} {relation_clause}"
    if len(evidence) > 720:
        evidence = evidence[:717].rstrip() + "..."
    return evidence


def definition_seed_indexes(
    lines: list[list[dict[str, Any]]], line_records: list[dict[str, Any]], page: int,
    font_maps: dict[str, dict[int, str]], reference_start: int | None = None,
) -> list[int]:
    """Find event starts using symbol-bearing definition wording or formula context."""
    line_symbols = [
        line_math_token_candidates(line, page, record["line_id"], font_maps)
        for line, record in zip(lines, line_records)
    ]
    seeds: set[int] = set()
    for index, record in enumerate(line_records):
        if reference_start is not None and index >= reference_start:
            continue
        nearby_symbols = any(line_symbols[offset] for offset in range(index, min(len(lines), index + 3)))
        if not nearby_symbols or is_generic_definition_sentence(record["text"]):
            continue
        text = record["text"]
        if explicit_definition_signal(text):
            seeds.add(index)
            continue
        # Equations often introduce the notation first and explain it in the
        # next few extracted lines. Anchor the event at the equation itself.
        if record["formula_like"] and line_symbols[index]:
            lookahead = " ".join(item["text"] for item in line_records[index:index + 7])
            if formula_definition_signal(lookahead):
                seeds.add(index)
    return sorted(seeds)


def event_indexes(
    seed: int, lines: list[list[dict[str, Any]]], line_records: list[dict[str, Any]], page: int,
    font_maps: dict[str, dict[int, str]],
) -> list[int]:
    """Keep the anchor and forward continuation lines from one definition block."""
    definition_span = 10
    symbol_lines = {
        index: line_math_token_candidates(lines[index], page, line_records[index]["line_id"], font_maps)
        for index in range(seed, min(len(lines), seed + definition_span + 1))
    }
    end = seed
    seed_is_definition = (
        line_records[seed]["definition_like"]
        or any(
            record["definition_like"]
            for record in line_records[seed:min(len(line_records), seed + 4)]
        )
        or formula_definition_signal(
            " ".join(item["text"] for item in line_records[seed:min(len(line_records), seed + 7)])
        )
    )
    for index in range(seed + 1, min(len(lines), seed + definition_span + 1)):
        if symbol_lines[index]:
            end = index
            continue
        # A definition sentence is often followed by a formula or a
        # continuation line whose symbols are part of the same author
        # definition.  Keep those lines in the event even when PDF text
        # extraction inserts a short blank/artifact line between columns.
        if seed_is_definition and line_records[index]["formula_like"]:
            end = index
            continue
        if seed_is_definition and any(
            symbol_lines[future]
            for future in range(index + 1, min(len(lines), seed + definition_span + 1))
        ):
            # A short run of PDF superscript/subscript artifacts can sit
            # between the defining sentence and its equation. Keep the
            # bridge so the later symbol-bearing line remains in the same
            # event; the hard bound prevents paragraph-wide drift.
            end = index
            continue
        if seed_is_definition and formula_definition_signal(
            " ".join(item["text"] for item in line_records[index:min(len(lines), seed + definition_span + 1)])
        ):
            end = index
            continue
        # Allow an equation artifact or a short bridge only when the later
        # lines contain explicit definition wording.
        lookahead = " ".join(item["text"] for item in line_records[index:min(len(lines), seed + definition_span + 1)])
        if explicit_definition_signal(lookahead):
            end = index
            continue
        break
    return list(range(seed, end + 1))


def visual_review_queue(
    page: Any,
    lines: list[list[dict[str, Any]]],
    line_records: list[dict[str, Any]],
    candidate_chars: list[dict[str, Any]],
    font_maps: dict[str, dict[int, str]],
) -> list[dict[str, Any]]:
    """Emit only machine-detectable regions that require visual review.

    This is a trigger queue, not a guessed recognition result. Figures are
    excluded unless their image box overlaps a definition/formula line.
    """
    queue: list[dict[str, Any]] = []
    for candidate in candidate_chars:
        if unresolved_identity_review(candidate) and candidate.get("line_id") in {
            record["line_id"] for record in line_records if record["formula_like"] or record["definition_like"]
        }:
            queue.append({
                "review_id": f"{candidate['token_id']}:identity",
                "kind": "unresolved_glyph_identity",
                "page": candidate["page"],
                "line_id": candidate["line_id"],
                "bbox": candidate["bbox"],
                "surface": candidate["surface"],
                "reason": "CID or private-font glyph has no deterministic Unicode identity",
            })
    for index, record in enumerate(line_records):
        if not record["definition_like"]:
            continue
        symbols = line_math_token_candidates(lines[index], page.page_number, record["line_id"], font_maps)
        if not symbols:
            queue.append({
                "review_id": f"{record['line_id']}:missing-math-token",
                "kind": "definition_without_text_math_token",
                "page": page.page_number,
                "line_id": record["line_id"],
                "bbox": record["bbox"],
                "reason": "definition wording exists but no deterministic math token was extracted",
            })
    return queue


def extract(path: Path) -> dict[str, Any]:
    validate_pdf(path)
    document_metadata = metadata(path)
    font_maps = build_font_maps(path)
    pages: list[dict[str, Any]] = []
    font_counts: Counter[str] = Counter()
    all_candidates: list[dict[str, Any]] = []
    all_occurrences: list[dict[str, Any]] = []
    definition_events: list[dict[str, Any]] = []
    references_started = False

    with pdfplumber.open(str(path)) as document:
        detected_boundaries = [
            column_boundary(page.chars, page.width)
            for page in document.pages
        ]
        stable_boundaries = [boundary for boundary in detected_boundaries if boundary is not None]
        document_boundary = statistics.median(stable_boundaries) if stable_boundaries else None
        for page_number, page in enumerate(document.pages, 1):
            chars: list[dict[str, Any]] = []
            for index, raw in enumerate(page.chars):
                char = dict(raw)
                char["index"] = index
                decoded, _ = decode_surface(char.get("text", ""), char.get("fontname"), font_maps)
                char["decoded"] = decoded or ""
                chars.append(char)
                font_counts[char.get("fontname", "")] += 1
            lines = group_lines(chars)
            event_boundary = detected_boundaries[page_number - 1]
            if event_boundary is None and supports_column_boundary(chars, document_boundary):
                event_boundary = document_boundary
            line_records: list[dict[str, Any]] = []
            candidate_chars: list[dict[str, Any]] = []
            seen: set[int] = set()
            definition_line_indexes: list[int] = []
            formula_line_indexes: list[int] = []
            reference_start: int | None = None
            for line_index, line in enumerate(lines):
                metrics = line_metrics(line)
                if REFERENCE_HEADING_RE.search(metrics["text"].strip()):
                    references_started = True
                    reference_start = line_index
                is_definition = definition_like(metrics["text"]) and not references_started
                is_formula = formula_like(metrics)
                line_id = f"p{page_number}:line{line_index}"
                line_records.append({
                    "line_id": line_id,
                    "text": metrics["text"],
                    "bbox": metrics["bbox"],
                    "definition_like": is_definition,
                    "formula_like": is_formula,
                })
                if is_definition:
                    definition_line_indexes.append(line_index)
                if is_formula and not is_definition:
                    formula_line_indexes.append(line_index)
                for record in line_math_token_candidates(line, page_number, line_id, font_maps):
                    surface = symbol_surface(record)
                    identity_key = symbol_identity_key(record)
                    if not surface or not identity_key:
                        continue
                    all_occurrences.append({
                        "token_id": record["token_id"],
                        "page": page_number,
                        "line_id": line_id,
                        "bbox": record["bbox"],
                        "surface_normalized": surface,
                        "identity_key": identity_key,
                        "style": symbol_style(record),
                        "identity_status": record["identity_status"],
                    })
                if not is_formula:
                    continue
                source = "definition_line_all_glyphs" if is_definition else "formula_line_all_glyphs"
                for char in line:
                    if char["index"] not in seen:
                        candidate = char_record(char, page_number, line_id, source, font_maps)
                        candidate["_index"] = char["index"]
                        candidate_chars.append(candidate)
                        seen.add(char["index"])

            # The candidate pool deliberately remains high-recall: retain a
            # six-line context around every definition-like line. PDF text
            # extraction can split one logical paragraph across several
            # formula/continuation lines, especially in two-column papers.
            # This is
            # separate from final-pool event selection below, so ordinary
            # context glyphs can never become final symbols by themselves.
            context_indexes = {
                index
                for definition_index in definition_line_indexes
                for index in range(max(0, definition_index - 6), min(len(lines), definition_index + 7))
            }
            for line_index in sorted(context_indexes):
                line = lines[line_index]
                line_id = f"p{page_number}:line{line_index}"
                for char in line:
                    if char["index"] not in seen:
                        candidate = char_record(char, page_number, line_id, "definition_context", font_maps)
                        candidate["_index"] = char["index"]
                        candidate_chars.append(candidate)
                        seen.add(char["index"])

            page_events: list[dict[str, Any]] = []
            seeds = definition_seed_indexes(lines, line_records, page_number, font_maps, reference_start)
            claimed_lines: set[int] = set()
            for line_index in seeds:
                if line_index in claimed_lines:
                    continue
                window_indexes = event_indexes(line_index, lines, line_records, page_number, font_maps)
                claimed_lines.update(window_indexes)
                window_line_ids = [line_records[index]["line_id"] for index in window_indexes]
                selected_column = definition_window_column(
                    lines, window_indexes, event_boundary, page_number, font_maps,
                )
                evidence_indexes = extend_event_to_sentence_end(
                    window_indexes, lines, event_boundary, selected_column,
                )
                window_line_ids = [line_records[index]["line_id"] for index in evidence_indexes]
                event_candidates: list[dict[str, Any]] = []
                for candidate_line_index in window_indexes:
                    line = definition_event_line(
                        lines[candidate_line_index], event_boundary, selected_column,
                    )
                    if not line:
                        continue
                    line_record = {
                        **line_records[candidate_line_index],
                        **{
                            key: value
                            for key, value in line_metrics(line).items()
                            if key in {"text", "bbox"}
                        },
                    }
                    line_record["definition_like"] = definition_like(line_record["text"])
                    line_record["formula_like"] = formula_like(line_metrics(line))
                    line_id = line_record["line_id"]
                    line_candidates = line_math_token_candidates(line, page_number, line_id, font_maps)
                    if line_record["definition_like"]:
                        line_candidates = definition_clause_candidates(line, line_candidates)
                    for candidate in line_candidates:
                        candidate["_index"] = candidate.get("_index", -1)
                        candidate["event_definition_like"] = line_record["definition_like"]
                        candidate["event_formula_like"] = line_record["formula_like"]
                        event_candidates.append(candidate)
                symbols: list[dict[str, Any]] = []
                for candidate in event_candidates:
                    candidate_line_index = next((index for index in window_indexes if candidate["line_id"] == line_records[index]["line_id"]), line_index)
                    candidate_line = lines[candidate_line_index]
                    if candidate.get("token_kind") in {"composite", "single_letter"} or likely_definition_symbol(candidate, candidate_line):
                        evidence_level = (
                            3 if candidate.get("event_definition_like")
                            else 2 if candidate.get("event_formula_like")
                            else 1
                        )
                        evidence_type = evidence_type_for_level(evidence_level)
                        symbols.append({
                            "surface": symbol_surface(candidate),
                            "identity_key": symbol_identity_key(candidate),
                            "style": symbol_style(candidate),
                            "token_id": candidate["token_id"],
                            "identity_status": candidate["identity_status"],
                            "evidence_level": evidence_level,
                            "evidence_type": evidence_type,
                            "line_index": candidate_line_index,
                            "evidence_text": symbol_evidence_text(
                                symbol_surface(candidate), candidate_line_index,
                                evidence_indexes, lines, line_records,
                                event_boundary, selected_column,
                            ),
                        })
                grouped: dict[str, dict[str, Any]] = {}
                for item in symbols:
                    identity_key = item["identity_key"]
                    if not identity_key:
                        continue
                    group = grouped.setdefault(identity_key, {
                        "surface": item["surface"],
                        "identity_key": identity_key,
                        "style": item["style"],
                        "token_ids": [],
                        "identity_status": item["identity_status"],
                        "evidence_level": item["evidence_level"],
                        "evidence_types": [item["evidence_type"]],
                        "evidence_texts": [],
                    })
                    group["token_ids"].append(item["token_id"])
                    if item.get("evidence_text") and item["evidence_text"] not in group["evidence_texts"]:
                        group["evidence_texts"].append(item["evidence_text"])
                    group["evidence_level"] = max(group["evidence_level"], item["evidence_level"])
                    if item["evidence_type"] not in group["evidence_types"]:
                        group["evidence_types"].append(item["evidence_type"])
                if grouped:
                    event_lines = [
                        line_text(definition_event_line(lines[index], event_boundary, selected_column))
                        for index in evidence_indexes
                    ]
                    text = " ".join(part for part in event_lines if part)
                    trigger = DEFINITION_RE.search(text)
                    event = {
                        "definition_id": f"p{page_number}:definition{line_index}",
                        "page": page_number,
                        "line_ids": window_line_ids,
                        "text": text,
                        "bbox": bbox([
                            char
                            for index in evidence_indexes
                            for char in definition_event_line(lines[index], event_boundary, selected_column)
                        ]),
                        "symbols": list(grouped.values()),
                        "trigger": trigger.group(0) if trigger else "",
                    }
                    page_events.append(event)
                    definition_events.append(event)

                # Keep the same candidate layer useful to later diagnostics,
                # but only add the forward definition block, never its prior
                # prose context.
                for candidate_line_index in window_indexes:
                    line = lines[candidate_line_index]
                    line_id = line_records[candidate_line_index]["line_id"]
                    for char in line:
                        if char["index"] not in seen:
                            candidate = char_record(char, page_number, line_id, "definition_context", font_maps)
                            candidate["_index"] = char["index"]
                            candidate_chars.append(candidate)
                            seen.add(char["index"])

            for candidate in candidate_chars:
                candidate.pop("_index", None)
            review_queue = visual_review_queue(page, lines, line_records, candidate_chars, font_maps)
            page_data = {
                "page": page_number,
                "width": page.width,
                "height": page.height,
                "lines": line_records,
                "candidate_chars": candidate_chars,
                "definition_events": page_events,
                "definition_line_count": len(definition_line_indexes),
                "formula_line_count": len(formula_line_indexes),
                "visual_fallback_regions": image_regions(page),
                "visual_review_queue": review_queue,
            }
            pages.append(page_data)
            all_candidates.extend(candidate_chars)

    defined: dict[str, dict[str, Any]] = {}
    for event in definition_events:
        for item in event["symbols"]:
            surface = item["surface"]
            identity_key = item.get("identity_key") or surface
            # The event window deliberately has high recall. A symbol found
            # only on an otherwise unrelated bridge line is event context,
            # not evidence that the author defined it. Formula lines inside
            # the same event are retained as review evidence because they
            # commonly carry the actual notation after a prose definition.
            if not is_final_pool_evidence(item.get("evidence_level", 1)):
                continue
            record = defined.setdefault(identity_key, {
                "id": f"symbol:{identity_key}:{len(defined)}",
                "surface": surface,
                "identity_key": identity_key,
                "style": item.get("style", "font:unknown"),
                "status": "confirmed" if item["identity_status"] != "unresolved" and item.get("evidence_level", 1) >= 3 else "review",
                "evidence_level": item.get("evidence_level", 1),
                "evidence_types": list(item.get("evidence_types", [item.get("evidence_type", "event_context")])),
                "definition_events": [],
                "occurrences": 0,
                "occurrence_locations": [],
            })
            record["evidence_level"] = max(record["evidence_level"], item.get("evidence_level", 1))
            for evidence_type in item.get("evidence_types", [item.get("evidence_type", "event_context")]):
                if evidence_type not in record["evidence_types"]:
                    record["evidence_types"].append(evidence_type)
            if item["identity_status"] == "unresolved":
                record["status"] = "unresolved"
            elif record["evidence_level"] >= 3:
                record["status"] = "confirmed"
            record["definition_events"].append(event["definition_id"])

    for candidate in all_occurrences:
        identity_key = candidate.get("identity_key") or candidate.get("surface_normalized")
        if identity_key in defined and candidate.get("identity_status") != "unresolved":
            defined[identity_key]["occurrences"] += 1
            defined[identity_key]["occurrence_locations"].append({
                "token_id": candidate["token_id"],
                "page": candidate["page"],
                "line_id": candidate["line_id"],
                "bbox": candidate["bbox"],
                "surface": candidate.get("surface_normalized"),
                "identity_key": identity_key,
                "style": candidate.get("style", "font:unknown"),
            })

    for item in defined.values():
        events = [event for event in definition_events if event["definition_id"] in item["definition_events"]]
        evidence_texts = []
        for event in events:
            for symbol in event.get("symbols", []):
                if symbol.get("identity_key") == item["identity_key"]:
                    evidence_texts.extend(symbol.get("evidence_texts", []))
        evidence_texts = list(dict.fromkeys(text for text in evidence_texts if text))
        ranked_evidence = sorted(
            evidence_texts,
            key=lambda text: (
                definition_meaning(item["surface"], text) == "作者定义的数学符号",
            ),
        )
        evidence = ranked_evidence[0] if ranked_evidence else ""
        event = events[0] if events else (definition_events[0] if definition_events else None)
        item["definition"] = definition_excerpt(item["surface"], evidence or (event["text"] if event else ""))
        item["location"] = f"第 {event['page']} 页"
        item["meaning"] = definition_summary(item["surface"], evidence or (event["text"] if event else ""))
        item["meaning_source"] = "local"

    return {
        "schema": "phireader.parse.v1",
        "input": str(path),
        "metadata": document_metadata,
        "method": "round5_composite_local_first",
        "llm_used": False,
        "policy": {
            "candidate_recall": "definition/formula lines plus six-line definition context",
            "identity_recovery": "ToUnicode -> Encoding/Differences -> AGL; unresolved CID is never guessed",
            "symbol_identity": "normalized surface + semantic math font style; unknown font families remain isolated",
            "final_pool": "single-letter and structurally bounded composite symbols with direct-definition or formula-context evidence; event-context-only symbols are excluded",
            "visual_review_trigger": "unresolved glyphs in math/definition lines or definition lines with no deterministic math token; image objects are excluded",
        },
        "fonts": font_counts.most_common(),
        "font_map_sizes": {name: len(mapping) for name, mapping in font_maps.items()},
        "pages": pages,
        "definition_events": definition_events,
        "symbols": list(defined.values()),
        "totals": {
            "candidate_chars": len(all_candidates),
            "definition_lines": sum(page["definition_line_count"] for page in pages),
            "formula_lines": sum(page["formula_line_count"] for page in pages),
            "definition_events": len(definition_events),
            "final_symbols": len(defined),
            "unresolved_candidates": sum(candidate["identity_status"] == "unresolved" for candidate in all_candidates),
            "visual_fallback_regions": sum(len(page["visual_fallback_regions"]) for page in pages),
            "visual_review_items": sum(len(page["visual_review_queue"]) for page in pages),
            "review_symbols": sum(item["status"] == "review" for item in defined.values()),
        },
    }


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Extract PhiReader candidate and final symbol pools")
    argument_parser.add_argument("pdf", type=Path)
    argument_parser.add_argument("--out", type=Path, required=True)
    args = argument_parser.parse_args()
    try:
        result = extract(args.pdf)
    except Exception as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        raise SystemExit(2) from error
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.out), "status": "ok", "totals": result["totals"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
