"""Focused tests for the deterministic definition gloss extractor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from parser.extract import definition_meaning, definition_summary, line_math_token_candidates


def glyph(index: int, text: str, x0: float, fontname: str = "CMMI10") -> dict:
    return {
        "index": index,
        "text": text,
        "decoded": text,
        "x0": x0,
        "x1": x0 + 4,
        "top": 0,
        "bottom": 10,
        "size": 10,
        "fontname": fontname,
    }


def main() -> None:
    assert definition_meaning("V", "V is an n x m matrix whose columns are data vectors.").startswith("an n")
    assert definition_meaning("D", "D(A||B) is called the divergence of A from B.").startswith("the divergence")
    assert definition_meaning("n", "n-dimensional vectors") == "vectors"
    chinese_v = "式中：V (D,G) 为二分类交叉熵函数；E 为数学期望"
    chinese_c = "设 C、H、W 分别为特征图的通道数"
    assert definition_meaning("V", chinese_v) == "二分类交叉熵函数"
    assert definition_meaning("C", chinese_c) == "特征图的通道数"
    assert definition_summary("V", chinese_v) == "二分类交叉熵函数"
    assert definition_summary("q", "where q is used in the update rule") != "作者定义的数学符号"
    noisy_event = "min Phi x b 2 + g(x), (1.1) where mu > 0 is a regularization parameter and g : X -> R is a convex function."
    assert definition_summary("Phi", noisy_event) == "作者定义的数学符号"
    flattened = "where E h i p i represents the cross correlation operation"
    assert definition_meaning("i", flattened) == "作者定义的数学符号"
    function_line = [glyph(0, "p", 0), glyph(1, "(", 5), glyph(2, "x", 9), glyph(3, ")", 14)]
    composite_surfaces = {item["surface_normalized"] for item in line_math_token_candidates(function_line, 1, "p1:line1", {})}
    assert "p(x)" in composite_surfaces
    code_line = [glyph(index, value, index * 5) for index, value in enumerate("zeros(1)")]
    code_surfaces = {item["surface_normalized"] for item in line_math_token_candidates(code_line, 1, "p1:line2", {})}
    assert "zeros(1)" not in code_surfaces
    equation_line = [glyph(index, value, index * 5) for index, value in enumerate("s(x)=a")]
    equation_surfaces = {item["surface_normalized"] for item in line_math_token_candidates(equation_line, 1, "p1:line3", {})}
    assert "s(x)" in equation_surfaces
    assert "s(x)=a" not in equation_surfaces
    print("definition-meaning=passed")


if __name__ == "__main__":
    main()
