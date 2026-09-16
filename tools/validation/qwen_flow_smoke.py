"""Exercise the Qwen summary path without contacting the real API."""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "5173"
    paper = {
        "paperId": "local-probe",
        "paperPath": "/probe/paper.pdf",
        "parsePath": "/probe/parse.json",
        "title": "Qwen probe paper",
        "metadata": {
            "title": "Qwen probe paper",
            "authors": "Probe Author",
            "year": "2026",
            "producer": "Probe PDF",
            "creator": "",
            "pages": 1,
            "file_size": 128,
        },
        "symbols": [{
            "id": "symbol:alpha:0",
            "surface": "α",
            "meaning": "作者定义的数学符号",
            "meaning_source": "local",
            "definition": "α is the regularization weight.",
            "location": "第 1 页",
            "status": "confirmed",
            "occurrences": 1,
            "occurrence_locations": [],
        }],
        "totals": {
            "candidate_chars": 1,
            "definition_lines": 1,
            "formula_lines": 1,
            "definition_events": 1,
            "final_symbols": 1,
            "unresolved_candidates": 0,
            "visual_fallback_regions": 0,
            "visual_review_items": 0,
            "review_symbols": 0,
        },
        "visualReviewQueue": [],
    }
    updated_symbols = [{**paper["symbols"][0], "meaning": "正则化权重", "meaning_source": "qwen"}]
    tiny_png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    init_script = f"""
        window.__PHI_PROBE__ = {{ invokes: [] }};
        window.__TAURI_INTERNALS__ = {{
          invoke: async (command, args) => {{
            window.__PHI_PROBE__.invokes.push({{ command, args }});
            if (command === 'list_papers') return [{json.dumps(paper, ensure_ascii=False)}];
            if (command === 'render_pdf_page') return {{ imageData: {json.dumps(tiny_png)}, pageWidth: 100, pageHeight: 100, occurrences: [{{ surface: 'α', bbox: [10, 10, 20, 20] }}] }};
            if (command === 'update_symbol_meanings') return {{
              paperId: 'local-probe', paperPath: '/probe/paper.pdf', parsePath: '/probe/parse.json',
              metadata: {json.dumps(paper['metadata'], ensure_ascii=False)},
              symbols: {json.dumps(updated_symbols, ensure_ascii=False)},
              totals: {json.dumps(paper['totals'], ensure_ascii=False)}, llmUsed: true, visualReviewQueue: []
            }};
            throw new Error('unexpected mocked command: ' + command);
          }}
        }};
        localStorage.setItem('phireader.apiKey', 'probe-only-key');
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(init_script)
        captured: list[dict[str, str]] = []

        def fulfill(route) -> None:
            captured.append({
                "authorization": route.request.headers.get("authorization", ""),
                "body": route.request.post_data or "",
            })
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"choices": [{"message": {"content": '{"items":[{"id":"symbol:alpha:0","meaning":"regularization weight"}]}'}}]}),
            )

        page.route("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", fulfill)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.get_by_role("heading", name="论文库").wait_for()
        page.get_by_role("button", name="设置").click()
        page.get_by_role("heading", name="设置").wait_for()
        page.locator("select[aria-label='界面语言']").select_option("en")
        page.get_by_role("button", name="Library").first.click()
        page.locator("article.paper-card").first.click()
        page.get_by_role("heading", name="Symbol index").wait_for()
        assert len(captured) == 0, "opening a paper must not call the summary API"
        sidebar_handle = page.get_by_role("separator", name="Resize left sidebar")
        assert sidebar_handle.count() == 1
        before_sidebar_width = page.locator(".sidebar").bounding_box()["width"]
        handle_box = sidebar_handle.bounding_box()
        page.mouse.move(handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + 120)
        page.mouse.down()
        page.mouse.move(handle_box["x"] + 56, handle_box["y"] + 120, steps=4)
        page.mouse.up()
        assert page.locator(".sidebar").bounding_box()["width"] > before_sidebar_width
        assert page.get_by_role("separator", name="Resize page thumbnail rail").count() == 1
        assert page.get_by_role("separator", name="Resize symbol sidebar").count() == 1
        page.get_by_role("button", name="Generate summary").click()
        page.get_by_text("Generated 1 symbol summaries").wait_for()

        assert len(captured) == 1
        assert captured[0]["authorization"] == "Bearer probe-only-key"
        assert '"model":"qwen3.7-flash"' in captured[0]["body"]
        qwen_request = json.loads(captured[0]["body"])
        assert qwen_request["stream"] is False
        assert qwen_request["enable_thinking"] is False
        assert qwen_request["max_completion_tokens"] == 4096
        assert "max_tokens" not in qwen_request
        assert qwen_request["temperature"] == 0.1
        assert "用 English 输出摘要" in captured[0]["body"]
        assert "α is the regularization weight" in captured[0]["body"]
        invokes = page.evaluate("window.__PHI_PROBE__.invokes")
        assert any(item["command"] == "update_symbol_meanings" for item in invokes)
        update_call = next(item for item in invokes if item["command"] == "update_symbol_meanings")
        assert update_call["args"]["source"] == "qwen"
        assert page.get_by_text("正则化权重", exact=True).count() >= 1
        browser.close()
    print("qwen flow smoke: ok (mocked endpoint, no real request)")


if __name__ == "__main__":
    main()
