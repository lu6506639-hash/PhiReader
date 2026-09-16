"""Run a provider request against a real paper parse without mutating the library.

The API key is supplied at runtime and is never written to disk or printed.
This intentionally mirrors the browser summary request so a provider can be
verified against the real Nghia paper fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests


QWEN_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"


def build_request(provider: str, model: str, symbols: list[dict]) -> dict:
    prompt = "\n".join([
        "你是学术论文符号助手。根据每个符号的作者定义原文，为它写一个简短、准确的摘要。",
        "用简体中文输出摘要。",
        '只输出 JSON 对象，格式为 {"items":[{"id":"原样保留","meaning":"简短中文术语或短语，不超过24字"}]}。',
        "必须为输入数组中的每个 id 返回且仅返回一项，id 必须逐字复制；不要添加原文没有的事实，不要解释过程，不要使用 Markdown。",
        json.dumps([
            {"id": item["id"], "symbol": item["surface"], "definition": item.get("definition", "")[:1200]}
            for item in symbols
        ], ensure_ascii=False),
    ])
    body = {
        "model": model,
        "stream": False,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "你只返回包含 items 数组的合法 JSON 对象，不要输出 Markdown 代码围栏。用简体中文输出摘要。"},
            {"role": "user", "content": prompt},
        ],
    }
    if provider == "qwen":
        body.update({"enable_thinking": False, "max_completion_tokens": 4096, "temperature": 0.1})
    else:
        body.update({"thinking": {"type": "disabled"}, "max_tokens": 4096, "temperature": 0.1})
    return body


def check_response(payload: dict, expected_ids: set[str]) -> tuple[int, bool]:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    if not isinstance(content, str) or not content.strip():
        raise AssertionError(
            ("provider returned reasoning_content without final content" if reasoning else "provider returned no final content")
            + f" finish_reason={choice.get('finish_reason')} usage={payload.get('usage')}"
        )
    parsed = json.loads(content)
    items = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(items, list) or len(items) != len(expected_ids):
        raise AssertionError("provider returned an incomplete items array")
    received = {item.get("id") for item in items if isinstance(item, dict)}
    if received != expected_ids:
        raise AssertionError("provider returned mismatched symbol ids")
    for item in items:
        meaning = item.get("meaning")
        if not isinstance(meaning, str) or not meaning.strip() or len(meaning.strip()) > 24:
            raise AssertionError("provider returned an invalid Chinese meaning")
    usage = payload.get("usage") or {}
    return len(items), bool(reasoning)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["qwen", "deepseek"])
    parser.add_argument("api_key")
    parser.add_argument("--paper-dir", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-symbols", type=int, default=16)
    args = parser.parse_args()

    parse_path = args.paper_dir / "parse.json"
    data = json.loads(parse_path.read_text(encoding="utf-8"))
    symbols = data.get("symbols", [])[: args.max_symbols]
    if not symbols:
        raise SystemExit("paper parse contains no symbols")
    model = args.model or ("qwen3.7-flash" if args.provider == "qwen" else "deepseek-flash")
    endpoint = QWEN_ENDPOINT if args.provider == "qwen" else DEEPSEEK_ENDPOINT
    total = 0
    had_reasoning = False
    for offset in range(0, len(symbols), 16):
        chunk = symbols[offset : offset + 16]
        response = requests.post(
            endpoint,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {args.api_key.strip()}"},
            json=build_request(args.provider, model, chunk),
            timeout=120,
            verify=False,
        )
        if not response.ok:
            raise SystemExit(f"{args.provider} API {response.status_code}: {response.text[:240].replace(args.api_key, '[KEY]')}")
        count, chunk_reasoning = check_response(response.json(), {item["id"] for item in chunk})
        total += count
        had_reasoning = had_reasoning or chunk_reasoning
    print(f"live-summary-smoke=passed provider={args.provider} model={model} symbols={total} batches={(total + 15) // 16} reasoning_content={had_reasoning}")


if __name__ == "__main__":
    main()
