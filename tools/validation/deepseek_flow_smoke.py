"""Exercise the DeepSeek thinking toggle and response isolation without network calls."""
from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else "5173"
    paper = {
        "paperId": "deepseek-probe",
        "paperPath": "/probe/paper.pdf",
        "parsePath": "/probe/parse.json",
        "title": "DeepSeek probe paper",
        "metadata": {
            "title": "DeepSeek probe paper",
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
    updated_symbols = [{**paper["symbols"][0], "meaning": "正则化权重", "meaning_source": "deepseek"}]
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
              paperId: 'deepseek-probe', paperPath: '/probe/paper.pdf', parsePath: '/probe/parse.json',
              metadata: {json.dumps(paper['metadata'], ensure_ascii=False)},
              symbols: {json.dumps(updated_symbols, ensure_ascii=False)},
              totals: {json.dumps(paper['totals'], ensure_ascii=False)}, llmUsed: true, visualReviewQueue: []
            }};
            throw new Error('unexpected mocked command: ' + command);
          }}
        }};
        localStorage.setItem('phireader.modelProvider', 'deepseek');
        localStorage.setItem('phireader.modelName', 'deepseek-flash');
        localStorage.setItem('phireader.apiKey.deepseek', 'probe-deepseek-key');
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
                body=json.dumps({
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "This private thought is not the answer and is not JSON.",
                            "content": '{"items":[{"id":"symbol:alpha:0","meaning":"正则化权重"}]}',
                        }
                    }]
                }),
            )

        page.route("https://api.deepseek.com/chat/completions", fulfill)
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.get_by_role("heading", name="论文库").wait_for()
        page.get_by_role("button", name="当前阅读").click()
        page.get_by_role("heading", name="符号索引").wait_for()
        page.get_by_role("button", name="生成摘要", exact=True).click()
        page.get_by_text("已生成 1 个符号摘要").wait_for()

        assert len(captured) == 1
        assert captured[0]["authorization"] == "Bearer probe-deepseek-key"
        deepseek_request = json.loads(captured[0]["body"])
        assert deepseek_request["model"] == "deepseek-flash"
        assert deepseek_request["stream"] is False
        assert deepseek_request["thinking"] == {"type": "disabled"}
        assert deepseek_request["max_tokens"] == 4096
        assert deepseek_request["temperature"] == 0.1
        assert "reasoning_effort" not in deepseek_request
        assert "enable_thinking" not in deepseek_request
        assert page.get_by_text("正则化权重", exact=True).count() >= 1
        update_call = next(item for item in page.evaluate("window.__PHI_PROBE__.invokes") if item["command"] == "update_symbol_meanings")
        assert update_call["args"]["source"] == "deepseek"
        browser.close()
    print("deepseek flow smoke: ok (mocked endpoint, thinking disabled explicitly)")


if __name__ == "__main__":
    main()
