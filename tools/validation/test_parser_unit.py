"""Unit regression tests for the deterministic PDF symbol parser.

These tests deliberately exercise the parser's decision boundaries without
requiring a checked-in PDF fixture.  Real-paper recall is covered by the
corpus validation scripts; this file protects the low-level rules that make
that recall reproducible.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PARSER_ROOT = Path(__file__).resolve().parents[2] / "parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from extract import (  # noqa: E402
    composite_token_candidates,
    definition_event_column,
    definition_event_line,
    definition_window_column,
    definition_like,
    decode_surface,
    extract,
    formula_definition_signal,
    event_indexes,
    extend_event_to_sentence_end,
    filename_metadata,
    glyph_name_to_unicode,
    group_lines,
    image_regions,
    is_single_letter_token,
    line_symbol_candidates,
    normalize_surface,
    parse_cmap,
    split_definition_line,
    symbol_evidence_text,
    symbol_surface,
    symbol_identity_key,
    unresolved_identity_review,
    visual_review_queue,
)


def char(text: str, x0: float, *, font: str = "CMMI10", index: int = 0) -> dict:
    """Create the small subset of a pdfplumber char record used by the rules."""
    width = 5.0 if text != " " else 2.5
    return {
        "text": text,
        "decoded": text,
        "fontname": font,
        "x0": x0,
        "x1": x0 + width,
        "top": 10.0,
        "bottom": 20.0,
        "size": 10.0,
        "index": index,
    }


def text_chars(text: str, x0: float, *, top: float, font: str, start_index: int) -> list[dict]:
    """Build positioned character records with visible word gaps."""
    result: list[dict] = []
    cursor = x0
    for offset, value in enumerate(text):
        if value == " ":
            cursor += 4.0
            continue
        record = char(value, cursor, font=font, index=start_index + offset)
        record["top"] = top
        record["bottom"] = top + 10.0
        result.append(record)
        cursor += 5.0
    return result


class FakeDocument:
    def __init__(self, pages: list[object]) -> None:
        self.pages = pages

    def __enter__(self) -> "FakeDocument":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class ParserUnitTests(unittest.TestCase):
    def test_filename_metadata_is_used_only_for_complete_convention(self) -> None:
        self.assertEqual(
            filename_metadata(Path("Lee & Seung - 2001 - Algorithms for NMF.pdf")),
            ("Lee & Seung", "2001", "Algorithms for NMF"),
        )
        self.assertEqual(filename_metadata(Path("paper-final.pdf")), ("", "", ""))

    def test_extract_does_not_bind_a_symbol_from_the_other_column(self) -> None:
        left = text_chars(
            "where F is the objective function.", 40, top=20, font="Times-Roman", start_index=0,
        )
        # Give the notation glyph a math identity while keeping the prose roman.
        next(item for item in left if item["text"] == "F")["fontname"] = "CMMI10"
        right = text_chars("D = x", 350, top=20, font="CMMI10", start_index=100)
        page = type("FakePage", (), {
            "chars": left + right,
            "width": 600.0,
            "height": 800.0,
            "images": [],
            "page_number": 1,
        })()
        document = FakeDocument([page])

        with (
            patch("extract.validate_pdf"),
            patch("extract.metadata", return_value={}),
            patch("extract.build_font_maps", return_value={}),
            patch("extract.column_boundary", return_value=300.0),
            patch("extract.pdfplumber.open", return_value=document),
        ):
            result = extract(Path("two-column.pdf"))

        surfaces = {item["surface"] for item in result["symbols"]}
        self.assertIn("F", surfaces)
        self.assertNotIn("D", surfaces)

    def test_extract_reuses_document_gutter_on_a_two_column_title_page(self) -> None:
        first_page_chars: list[dict] = []
        for row in range(5):
            left = text_chars(
                "where F is the objective function.", 40, top=20 + row * 14,
                font="Times-Roman", start_index=row * 200,
            )
            next(item for item in left if item["text"] == "F")["fontname"] = "CMMI10"
            right = text_chars(
                "D = x", 350, top=20 + row * 14, font="CMMI10",
                start_index=row * 200 + 100,
            )
            first_page_chars.extend(left + right)
        first_page = type("FakePage", (), {
            "chars": first_page_chars,
            "width": 600.0,
            "height": 800.0,
            "images": [],
            "page_number": 1,
        })()
        second_page = type("FakePage", (), {
            "chars": text_chars(
                "ordinary text", 40, top=20, font="Times-Roman", start_index=2000,
            ),
            "width": 600.0,
            "height": 800.0,
            "images": [],
            "page_number": 2,
        })()
        document = FakeDocument([first_page, second_page])

        with (
            patch("extract.validate_pdf"),
            patch("extract.metadata", return_value={}),
            patch("extract.build_font_maps", return_value={}),
            patch("extract.column_boundary", side_effect=[None, 300.0]),
            patch("extract.pdfplumber.open", return_value=document),
        ):
            result = extract(Path("title-page.pdf"))

        surfaces = {item["surface"] for item in result["symbols"]}
        self.assertIn("F", surfaces)
        self.assertNotIn("D", surfaces)


    def test_cmap_char_and_range_forms_are_decoded(self) -> None:
        cmap = b"""\
begincmap
beginbfchar
<01> <03B1>
endbfchar
beginbfrange
<10> <12> <0041>
endbfrange
endcmap
"""
        self.assertEqual(parse_cmap(cmap), {1: "α", 16: "A", 17: "B", 18: "C"})

    def test_glyph_name_recovery_handles_agl_suffix_and_greek_names(self) -> None:
        self.assertEqual(glyph_name_to_unicode("/alpha"), "α")
        self.assertEqual(glyph_name_to_unicode("/A123"), "A")
        self.assertEqual(glyph_name_to_unicode("/lambda"), "λ")
        self.assertIsNone(glyph_name_to_unicode("/privateGlyph"))

    def test_cid_uses_font_map_and_never_guesses_unknown_identity(self) -> None:
        maps = {"CMEX10": {80: "√"}}
        self.assertEqual(decode_surface("(cid:80)", "ABCDEF+CMEX10", maps), ("√", "recovered"))
        self.assertEqual(decode_surface("(cid:81)", "ABCDEF+CMEX10", maps), ("(cid:81)", "unresolved"))
        self.assertEqual(decode_surface("x", "CMMI10", maps), ("x", "native"))

    def test_mathematical_alphanumeric_surface_is_normalized(self) -> None:
        self.assertEqual(normalize_surface("𝑥𝛼"), "xα")
        self.assertEqual(normalize_surface("plain"), "plain")

    def test_same_surface_keeps_distinct_math_font_identity(self) -> None:
        roman = {
            "surface_normalized": "R",
            "identity_status": "native",
            "font_family": "CMR9",
        }
        blackboard = {
            "surface_normalized": "R",
            "identity_status": "native",
            "font_family": "MSBM10",
        }
        self.assertNotEqual(symbol_identity_key(roman), symbol_identity_key(blackboard))
        self.assertEqual(
            symbol_identity_key({**roman, "font_family": "CMR8"}),
            symbol_identity_key(roman),
        )

    def test_computer_modern_roman_is_not_treated_as_math_font(self) -> None:
        from extract import is_math_glyph

        self.assertFalse(is_math_glyph(char("e", 0, font="CMR9")))
        self.assertTrue(is_math_glyph(char("x", 0, font="CMMI10")))
        prose = [char("e", 0, font="CMR9", index=0), char("x", 20, font="CMR9", index=1)]
        self.assertEqual(line_symbol_candidates(prose, 1, "p1:line0", {}), [])

    def test_computer_modern_bold_math_alphabet_enters_candidates(self) -> None:
        from extract import is_math_glyph

        bold = char("X", 0, font="CMBX10", index=0)
        self.assertTrue(is_math_glyph(bold))
        line = [
            char("X", 0, font="CMBX10", index=0),
            char("(", 5.1, font="CMR7", index=1),
            char("i", 8.3, font="CMMI7", index=2),
            char(")", 11.1, font="CMR7", index=3),
            char("=", 20, font="CMSY10", index=4),
            char("x", 28, font="CMMI10", index=5),
        ]
        candidates = line_symbol_candidates(line, 1, "p1:line0", {})
        self.assertIn("X", [item["surface"] for item in candidates])
        composite = composite_token_candidates(line, 1, "p1:line0", {})
        self.assertIn("X(i)", [item["surface"] for item in composite])

    def test_bold_heading_word_is_not_promoted_to_single_symbol(self) -> None:
        heading = [
            char(letter, index * 5.1, font="CMBX10", index=index)
            for index, letter in enumerate("Notation")
        ]
        self.assertEqual(line_symbol_candidates(heading, 1, "p1:line0", {}), [])

    def test_generic_publisher_bold_requires_math_context(self) -> None:
        formula = [
            char("L", 0, font="TimesLTStd-Bold", index=0),
            char("=", 10, font="CMSY10", index=1),
            char("x", 20, font="CMMI10", index=2),
        ]
        self.assertIn(
            "L",
            [item["surface"] for item in line_symbol_candidates(formula, 1, "p1:line0", {})],
        )
        heading = [
            char(letter, index * 5.1, font="FormataOTF-Bold", index=index)
            for index, letter in enumerate("TABLE")
        ]
        self.assertEqual(line_symbol_candidates(heading, 1, "p1:line1", {}), [])

    def test_definition_line_keeps_script_and_italic_body_symbols(self) -> None:
        line = [
            char("Let", 0, font="TimesLTStd-Roman", index=0),
            char("X", 22, font="CMBSY10", index=1),
            char("∈", 32, font="CMSY10", index=2),
            char("R", 42, font="MSBM10", index=3),
            char("N", 52, font="TimesLTStd-Italic", index=4),
            char("×", 62, font="CMSY10", index=5),
            char("B", 72, font="TimesLTStd-Italic", index=6),
            char("where", 88, font="TimesLTStd-Roman", index=7),
            char("N", 122, font="TimesLTStd-Italic", index=8),
            char("=", 132, font="CMSY10", index=9),
            char("H", 142, font="TimesLTStd-Italic", index=10),
            char("×", 152, font="CMSY10", index=11),
            char("W", 162, font="TimesLTStd-Italic", index=12),
        ]
        candidates = line_symbol_candidates(line, 4, "p4:line24", {})
        self.assertTrue({item["surface"] for item in candidates} >= {"X", "N", "H", "W"})
        self.assertIn("font:CMBSY10", {item["style"] for item in candidates if item["surface"] == "X"})

    def test_plain_font_letter_is_allowed_only_in_assignment_context(self) -> None:
        words = ["where", "m", "is", "the", "number"]
        line = []
        x0 = 0.0
        index = 0
        for word in words:
            for letter in word:
                line.append(char(letter, x0, font="Times-Roman", index=index))
                x0 += 5.1
                index += 1
            x0 += 4.0
        candidates = line_symbol_candidates(line, 1, "p1:line0", {})
        self.assertEqual([item["surface"] for item in candidates], ["m"])
        self.assertFalse(definition_like("The update is given e for the next step."))
        self.assertFalse(definition_like("We use penalties related to the Hoyer measure."))
        self.assertTrue(definition_like("We refer to it as the divergence."))
        self.assertFalse(formula_definition_signal("Since G is an auxiliary function, F is nonincreasing."))
        self.assertTrue(formula_definition_signal("D(A||B), where A and B are nonnegative matrices."))
        self.assertFalse(formula_definition_signal("The method gives O(1/N) convergence where the primal or the dual is uniformly convex."))
        self.assertFalse(definition_like("Lemma 2. Let λ > 0, and N ≥ 0 with γτ ≤ λ."))
        self.assertTrue(definition_like("Let X and Y be finite-dimensional spaces."))
        self.assertTrue(definition_like("For the model, let b, A R and let the dictionary A have M groups."))
        self.assertTrue(definition_like("Given a non-negative matrix V ∈ R^{F×N}."))
        self.assertTrue(definition_like("where we observe a mixed signal z ∈ R^d."))
        self.assertTrue(definition_like("we say that a signal x is atomic."))
        self.assertTrue(definition_like("the steps σ and τ can be modified at each iteration."))
        self.assertTrue(definition_like("the covariance matrix Σ is identity."))

    def test_composite_token_is_bounded_before_operator(self) -> None:
        line = [
            char("s", 0, index=0),
            char("(", 5.1, index=1),
            char("x", 10.2, index=2),
            char(")", 15.3, index=3),
            char("=", 24.0, index=4),
            char("a", 30.0, index=5),
        ]
        candidates = composite_token_candidates(line, 1, "p1:line0", {})
        self.assertEqual([item["surface"] for item in candidates], ["s(x)"])
        self.assertTrue(all("=" not in item["surface"] for item in candidates))

    def test_composite_token_requires_balanced_structure_and_math_start(self) -> None:
        balanced = [
            char("K", 0, index=0),
            char("_", 5.1, index=1),
            char("ν", 10.2, index=2),
        ]
        unbalanced = [
            char("p", 0, index=0),
            char("(", 5.1, index=1),
            char("x", 10.2, index=2),
        ]
        prose = [
            char("z", 0, font="Times-Roman", index=0),
            char("e", 5.1, font="Times-Roman", index=1),
            char("r", 10.2, font="Times-Roman", index=2),
            char("o", 15.3, font="Times-Roman", index=3),
            char("s", 20.4, font="Times-Roman", index=4),
            char("(", 25.5, font="Times-Roman", index=5),
            char("1", 30.6, font="Times-Roman", index=6),
            char(")", 35.7, font="Times-Roman", index=7),
        ]
        self.assertEqual([item["surface"] for item in composite_token_candidates(balanced, 1, "p1:line0", {})], ["K_ν"])
        self.assertEqual(composite_token_candidates(unbalanced, 1, "p1:line1", {}), [])
        self.assertEqual(composite_token_candidates(prose, 1, "p1:line2", {}), [])

    def test_single_letter_word_member_is_not_a_symbol(self) -> None:
        line = [
            char("t", 0, font="Times-Roman", index=0),
            char("h", 5.1, font="Times-Roman", index=1),
            char("e", 10.2, font="Times-Roman", index=2),
        ]
        candidate = {
            "surface_normalized": "h",
            "identity_status": "native",
            "size": 10.0,
            "font_family": "Times-Roman",
            "_index": 1,
        }
        self.assertFalse(is_single_letter_token(line, 1, candidate))
        # ``symbol_surface`` validates surface syntax only; the contextual
        # token decision belongs to ``is_single_letter_token``.
        self.assertEqual(symbol_surface({**candidate, "token_kind": "unsupported"}), "h")

    def test_positioned_subscript_letters_are_kept_as_symbols(self) -> None:
        base = char("m", 0, font="CMMI10", index=0)
        subscript = char("j", 5.02, font="CMMI8", index=1)
        subscript.update({"top": 12.2, "bottom": 20.2, "size": 8.0, "x1": 9.02})
        line = [base, subscript]
        candidates = line_symbol_candidates(line, 1, "p1:line0", {})
        self.assertEqual([item["surface"] for item in candidates], ["m", "j"])

    def test_structural_unknown_glyph_does_not_create_review_noise(self) -> None:
        operator = {"identity_status": "unresolved", "font_family": "CMSY10"}
        italic = {"identity_status": "unresolved", "font_family": "CMMI10"}
        unknown = {"identity_status": "unresolved", "font_family": "PublisherMath"}
        self.assertFalse(unresolved_identity_review(operator))
        self.assertTrue(unresolved_identity_review(italic))
        self.assertTrue(unresolved_identity_review(unknown))

    def test_thin_image_objects_are_not_formula_regions(self) -> None:
        class Page:
            images = [
                {"width": 442.92, "height": 0.48, "x0": 0, "top": 10, "x1": 442.92, "bottom": 10.48},
                {"width": 8, "height": 8, "x0": 20, "top": 20, "x1": 28, "bottom": 28},
            ]

        regions = image_regions(Page())
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0]["kind"], "image_or_glyph_mask")

    def test_image_objects_do_not_enter_body_symbol_review_queue(self) -> None:
        class Page:
            page_number = 1
            images = [{"width": 8, "height": 8, "x0": 20, "top": 20, "x1": 28, "bottom": 28}]

        self.assertEqual(visual_review_queue(Page(), [], [], [], {}), [])

    def test_evidence_levels_are_explicitly_partitioned(self) -> None:
        from extract import evidence_type_for_level, is_final_pool_evidence

        self.assertEqual(evidence_type_for_level(3), "direct_definition")
        self.assertEqual(evidence_type_for_level(2), "formula_context")
        self.assertEqual(evidence_type_for_level(1), "event_context")
        self.assertTrue(is_final_pool_evidence(3))
        self.assertTrue(is_final_pool_evidence(2))
        self.assertFalse(is_final_pool_evidence(1))

    def test_two_column_lines_are_not_joined_across_the_gutter(self) -> None:
        columns = [(72, ["where m is defined here", "and this is a second line"]), (320, ["The method is stable now", "a separate column continues here"])]
        chars = []
        index = 0
        for start, rows in columns:
            for row, text in enumerate(rows):
                for offset, value in enumerate(text):
                    item = char(value, start + offset * 5.1, font="Times-Roman", index=index)
                    item["top"] = 10 + row * 20
                    item["bottom"] = item["top"] + 10
                    chars.append(item)
                    index += 1
        lines = group_lines(chars)
        self.assertEqual(len(lines), 2)
        split = split_definition_line(lines[0], 306)
        self.assertEqual(len(split), 2)
        self.assertTrue(max(float(item["x1"]) for item in split[0]) <= 306)
        self.assertTrue(min(float(item["x0"]) for item in split[1]) >= 306)

    def test_rotated_margin_text_is_excluded_from_body_lines(self) -> None:
        rotated = char("D", 2, font="Times-Roman", index=0)
        rotated["upright"] = False
        body = char("x", 80, font="CMMI10", index=1)
        lines = group_lines([rotated, body])
        self.assertEqual([[item["text"] for item in line] for line in lines], [["x"]])

    def test_two_column_event_drops_singleton_lines_from_the_other_column(self) -> None:
        left = [char("D", 72, font="CMMI10", index=1), char("x", 100, font="CMMI10", index=2)]
        right = [char("r", 340, font="Times-Roman", index=3)]
        self.assertEqual(definition_event_column(left, 306), 0)
        self.assertEqual(definition_event_line(left, 306, 0), left)
        self.assertEqual(definition_event_line(right, 306, 0), [])
        self.assertEqual(definition_event_line(right, 306, 1), right)

    def test_definition_window_uses_raw_where_trigger_when_glyphs_are_missing(self) -> None:
        left = [char(value, 72 + offset * 5.1, font="Times-Roman", index=offset) for offset, value in enumerate("abstract text")]
        right = [char(value, 340 + offset * 5.1, font="CMMI10", index=100 + offset) for offset, value in enumerate("where x represents vector")]
        self.assertEqual(definition_window_column([left, right], [0, 1], 306, 1, {}), 1)
        self.assertEqual(definition_event_line(left, 306, 1), [])
        self.assertEqual(definition_event_line(right, 306, 1), right)

    def test_formula_context_accepts_a_relation_whose_symbols_are_on_the_previous_line(self) -> None:
        self.assertTrue(
            formula_definition_signal(
                "h i spectrogram, where and represent indices of frequency and time bins."
            )
        )

    def test_formula_event_reaches_a_split_relation_clause(self) -> None:
        lines = [
            [char("h", 0, index=0), char("i", 10, index=1)],
            text_chars("power spectrogram", 0, top=24, font="Times-Roman", start_index=10),
            text_chars("where and represent indices", 0, top=38, font="Times-Roman", start_index=40),
        ]
        records = [
            {"line_id": "p1:line0", "text": "h i", "definition_like": False, "formula_like": True},
            {"line_id": "p1:line1", "text": "power spectrogram", "definition_like": False, "formula_like": False},
            {"line_id": "p1:line2", "text": "where and represent indices", "definition_like": False, "formula_like": False},
        ]
        self.assertEqual(event_indexes(0, lines, records, 1, {}), [0, 1, 2])

    def test_symbol_evidence_is_local_to_its_definition_line(self) -> None:
        lines = [
            text_chars("alpha is the step size.", 0, top=0, font="Times-Roman", start_index=0),
            text_chars("beta is the momentum coefficient.", 0, top=14, font="Times-Roman", start_index=40),
            text_chars("unrelated paragraph follows.", 0, top=28, font="Times-Roman", start_index=80),
        ]
        records = [
            {"definition_like": True, "formula_like": False},
            {"definition_like": True, "formula_like": False},
            {"definition_like": False, "formula_like": False},
        ]
        alpha = symbol_evidence_text("alpha", 0, [0, 1, 2], lines, records, None, None)
        beta = symbol_evidence_text("beta", 1, [0, 1, 2], lines, records, None, None)
        self.assertIn("alpha is the step size", alpha)
        self.assertNotIn("beta is the momentum", alpha)
        self.assertIn("beta is the momentum", beta)
        self.assertNotIn("unrelated paragraph", beta)

    def test_formula_symbol_evidence_keeps_a_split_relation_clause(self) -> None:
        lines = [
            text_chars("h i", 0, top=0, font="CMMI10", start_index=0),
            text_chars("power spectrogram", 0, top=14, font="Times-Roman", start_index=20),
            text_chars("where h and i represent frequency and time bins.", 0, top=28, font="Times-Roman", start_index=50),
        ]
        records = [
            {"definition_like": False, "formula_like": True},
            {"definition_like": False, "formula_like": False},
            {"definition_like": True, "formula_like": False},
        ]
        evidence = symbol_evidence_text("h", 0, [0, 1, 2], lines, records, None, None)
        self.assertIn("where h and i represent frequency and time bins", evidence)

    def test_two_column_definition_finishes_after_an_interleaved_line(self) -> None:
        lines = [
            text_chars("where h and i represent frequency", 320, top=0, font="Times-Roman", start_index=0),
            text_chars("unrelated sentence.", 20, top=14, font="Times-Roman", start_index=80),
            text_chars("and time bins.", 320, top=28, font="Times-Roman", start_index=120),
        ]
        self.assertEqual(extend_event_to_sentence_end([0, 1], lines, 300, 1), [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
