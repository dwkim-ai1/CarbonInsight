"""Auditable, tightly validated Ollama fallback for exceptional DART tables."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser

MODEL = "gemma4:31b-cloud"
SYSTEM_PROMPT = "너는 DART 정기보고서의 표를 tidy JSON으로 정형화하는 파서다. 입력에 없는 값은 만들지 말고 불확실하면 null로 남겨라."
USER_PROMPT = '''다음 표의 각 행을 JSON 배열로 반환하라.
스키마: [{"사업연도":null,"사업부문":null,"품목":null,"사업소":null,"생산능력":null,"생산실적":null,"가동률":null,"단위":null,"산출식":null,"confidence":"low"}]
숫자는 원문 그대로, 없는 필드는 null, 제NN기는 그대로, 설명 없이 JSON 배열만 반환.
<TABLE_HTML>{html}</TABLE_HTML>'''

NUMERIC_FIELDS = ("생산능력", "생산실적", "가동률")


@dataclass
class FallbackResult:
    rows: list[dict]
    status: str
    raw: str = ""


def _clean(raw: str) -> str:
    raw = re.sub(r"<\|think\|>.*?<\|/think\|>", "", raw, flags=re.S)
    match = re.search(r"\[[\s\S]*\]", raw)
    return match.group() if match else raw


def validate_response(raw: str, html: str) -> FallbackResult:
    try:
        data = json.loads(_clean(raw))
        if not isinstance(data, list): raise ValueError("not an array")
    except (ValueError, json.JSONDecodeError):
        return FallbackResult([], "llm_bad_json", raw)
    source = re.sub(r"<[^>]+>", " ", html)
    rows = []
    uncertain = False
    for item in data:
        if not isinstance(item, dict): continue
        for field in NUMERIC_FIELDS:
            value = item.get(field)
            if value is not None and str(value) not in source:
                item[field], item["confidence"], uncertain = None, "low", True
        rate = item.get("가동률")
        try:
            if rate is not None and not 0 <= float(str(rate).replace("%", "").replace(",", "")) <= 200:
                item["가동률"], item["confidence"], uncertain = None, "low", True
        except ValueError:
            item["가동률"], item["confidence"], uncertain = None, "low", True
        if item.get("사업연도") is None or item.get("가동률") is None: continue
        rows.append(item)
        uncertain |= item.get("confidence") == "low"
    return FallbackResult(rows, "llm_uncertain" if uncertain else "success", raw)


def call_fallback(html: str, client=None, retries: int = 2, sleep=time.sleep) -> FallbackResult:
    if client is None:
        import os
        from ollama import Client
        key = os.getenv("OLLAMA_API_KEY")
        if not key: return FallbackResult([], "llm_disabled")
        client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + key}, timeout=60)
    for attempt in range(retries + 1):
        try:
            response = client.chat(model=MODEL, stream=False, options={"temperature": 0.0, "num_ctx": 8192},
                                   messages=[{"role":"system","content":SYSTEM_PROMPT}, {"role":"user","content":USER_PROMPT.replace("{html}", html[:6000])}])
            raw = response["message"]["content"] if isinstance(response, dict) else response.message.content
            return validate_response(raw, html)
        except Exception:
            if attempt == retries: return FallbackResult([], "llm_error")
            sleep((1, 3)[min(attempt, 1)])
    return FallbackResult([], "llm_error")
