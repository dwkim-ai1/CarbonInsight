"""Auditable, tightly validated Ollama fallback for exceptional DART tables."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

MODEL = "gemma4:31b-cloud"
SYSTEM_PROMPT = "너는 DART 정기보고서의 표를 tidy JSON으로 정형화하는 파서다. 입력에 없는 값은 만들지 말고 불확실하면 null로 남겨라."
USER_PROMPT = '''다음 표의 각 행을 JSON 배열로 반환하라.
스키마: [{"사업연도":null,"사업부문":null,"품목":null,"사업소":null,"생산능력":null,"생산실적":null,"가동률":null,"단위":null,"산출식":null,"confidence":"low"}]
숫자는 원문 그대로, 없는 필드는 null, 제NN기는 그대로, 설명 없이 JSON 배열만 반환.
<TABLE_HTML>{html}</TABLE_HTML>'''
STRUCTURE_SYSTEM_PROMPT = "너는 DART 생산능력/생산실적/가동률 표를 회사별 Google Sheet 구조에 맞게 셀 업데이트 JSON으로 매핑하는 파서다. 입력에 없는 숫자는 만들지 말고, 기존 시트 행을 우선 매칭하라."
STRUCTURE_USER_PROMPT = '''DART HTML과 현재 회사별 가동률 시트 구조를 비교해서 대상 분기 셀에 쓸 업데이트만 JSON 배열로 반환하라.

규칙:
- 설명 없이 JSON 배열만 반환한다.
- 값은 DART 원문에 있는 숫자/문자열만 사용한다. 계산하거나 추정하지 않는다.
- 기존 시트 row와 맞으면 target_row를 넣는다.
- 맞는 행이 없으면 target_row는 null로 두고 section/division/item/site/unit을 채워 새 구조 행을 만들 수 있게 한다.
- section은 생산능력, 생산실적, 가동률, 가동가능시간, 실제가동시간, 평균가동률 등 원문/시트의 상위 항목을 쓴다.
- quarter는 반드시 대상 분기를 사용한다.
- confidence는 high, medium, low 중 하나다.

스키마:
[{"target_row":null,"section":"생산능력","division":null,"item":null,"site":null,"unit":null,"quarter":"{quarter}","value":null,"source_label":null,"confidence":"low"}]

대상 분기: {quarter}
현재 시트 구조 JSON:
<SHEET_STRUCTURE>{sheet_structure}</SHEET_STRUCTURE>
DART HTML:
<TABLE_HTML>{html}</TABLE_HTML>'''
FRAMEWORK_SYSTEM_PROMPT = "너는 DART 생산능력/생산실적/가동률 표와 회사별 Google Sheet를 비교해서 반복 사용 가능한 selector 프레임워크를 만드는 설계자다. 숫자 값은 추출하지 말고 행 구조와 매칭 규칙만 반환하라."
FRAMEWORK_USER_PROMPT = '''DART HTML과 현재 회사별 가동률 시트 구조를 비교해서 이후 parser가 값을 채울 때 사용할 selector 프레임워크만 JSON 배열로 반환하라.

규칙:
- 설명 없이 JSON 배열만 반환한다.
- 숫자 value는 절대 반환하지 않는다.
- 기존 시트 row와 맞으면 target_row를 넣는다.
- 맞는 행이 없으면 target_row는 null로 두고 section/division/item/site/unit을 채워 새 구조 행을 만들 수 있게 한다.
- source_aliases에는 DART 표에서 같은 행을 찾는 데 쓸 원문 라벨 후보를 넣는다.
- confidence는 high, medium, low 중 하나다.

스키마:
[{"target_row":null,"section":"생산능력","division":null,"item":null,"site":null,"unit":null,"source_aliases":[],"confidence":"low"}]

현재 시트 구조 JSON:
<SHEET_STRUCTURE>{sheet_structure}</SHEET_STRUCTURE>
DART HTML:
<TABLE_HTML>{html}</TABLE_HTML>'''

NUMERIC_FIELDS = ("생산능력", "생산실적", "가동률")


@dataclass
class FallbackResult:
    rows: list[dict]
    status: str
    raw: str = ""


@dataclass
class StructureResult:
    updates: list[dict]
    status: str
    raw: str = ""


@dataclass
class FrameworkResult:
    selectors: list[dict]
    status: str
    raw: str = ""


def _strip_thinking(raw: str) -> str:
    return re.sub(r"<\|think\|>.*?<\|/think\|>", "", str(raw), flags=re.S)


def _clean(raw: str) -> str:
    raw = _strip_thinking(raw)
    match = re.search(r"\[[\s\S]*\]", raw)
    return match.group() if match else raw


def _clean_json(raw: str) -> str:
    raw = _strip_thinking(raw)
    array = re.search(r"\[[\s\S]*\]", raw)
    if array:
        return array.group()
    obj = re.search(r"\{[\s\S]*\}", raw)
    return obj.group() if obj else raw


def _source_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html)))


def _number_tokens(text: str) -> set[str]:
    tokens = set()
    for match in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", str(text)):
        token = match.group().replace(",", "")
        tokens.add(token)
        if token.endswith(".0"):
            tokens.add(token[:-2])
    return tokens


def _value_in_source(value: object, source: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text in source:
        return True
    value_numbers = _number_tokens(text)
    if value_numbers:
        return bool(value_numbers & _number_tokens(source))
    return text.replace(" ", "") in source.replace(" ", "")


def validate_response(raw: str, html: str) -> FallbackResult:
    try:
        data = json.loads(_clean(raw))
        if not isinstance(data, list): raise ValueError("not an array")
    except (ValueError, json.JSONDecodeError):
        return FallbackResult([], "llm_bad_json", raw)
    source = _source_text(html)
    rows = []
    uncertain = False
    for item in data:
        if not isinstance(item, dict): continue
        for field in NUMERIC_FIELDS:
            value = item.get(field)
            if value is not None and not _value_in_source(value, source):
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


def validate_structure_response(raw: str, html: str, sheet_structure: dict, quarter: str) -> StructureResult:
    try:
        data = json.loads(_clean_json(raw))
        if isinstance(data, dict):
            data = data.get("updates", [])
        if not isinstance(data, list):
            raise ValueError("not an array")
    except (ValueError, json.JSONDecodeError):
        return StructureResult([], "llm_bad_json", raw)

    source = _source_text(html)
    known_rows = {int(row["row"]): row for row in sheet_structure.get("rows", []) if str(row.get("row", "")).isdigit()}
    known_quarters = {item.get("label") for item in sheet_structure.get("quarters", []) if item.get("label")}
    updates = []
    uncertain = False
    for item in data:
        if not isinstance(item, dict):
            continue
        value = item.get("value") if item.get("value") is not None else item.get("값")
        if value in (None, "", "-"):
            continue
        if not _value_in_source(value, source):
            uncertain = True
            continue
        target_row = item.get("target_row") or item.get("row")
        try:
            target_row = int(target_row) if target_row not in (None, "") else None
        except (TypeError, ValueError):
            target_row = None
        if target_row is not None and target_row not in known_rows:
            target_row = None
        row_hint = known_rows.get(target_row, {}) if target_row is not None else {}
        target_quarter = str(item.get("quarter") or quarter).strip()
        if known_quarters and target_quarter not in known_quarters:
            target_quarter = quarter
        update = {
            "target_row": target_row,
            "section": str(item.get("section") or row_hint.get("section") or "").strip(),
            "division": str(item.get("division") or row_hint.get("division") or "").strip(),
            "item": str(item.get("item") or row_hint.get("item") or "").strip(),
            "site": str(item.get("site") or row_hint.get("site") or "").strip(),
            "unit": str(item.get("unit") or row_hint.get("unit") or "").strip(),
            "quarter": target_quarter,
            "value": str(value).strip(),
            "source_label": str(item.get("source_label") or "").strip(),
            "confidence": str(item.get("confidence") or "low").strip().lower(),
        }
        if not update["section"] and target_row is None:
            uncertain = True
            continue
        updates.append(update)
        uncertain |= update["confidence"] != "high"
    return StructureResult(updates, "llm_uncertain" if uncertain else "success", raw)


def validate_framework_response(raw: str, sheet_structure: dict) -> FrameworkResult:
    try:
        data = json.loads(_clean_json(raw))
        if isinstance(data, dict):
            data = data.get("selectors", data.get("framework", []))
        if not isinstance(data, list):
            raise ValueError("not an array")
    except (ValueError, json.JSONDecodeError):
        return FrameworkResult([], "llm_bad_json", raw)

    known_rows = {int(row["row"]): row for row in sheet_structure.get("rows", []) if str(row.get("row", "")).isdigit()}
    selectors = []
    uncertain = False
    for item in data:
        if not isinstance(item, dict):
            continue
        target_row = item.get("target_row") or item.get("row")
        try:
            target_row = int(target_row) if target_row not in (None, "") else None
        except (TypeError, ValueError):
            target_row = None
        if target_row is not None and target_row not in known_rows:
            target_row = None
        row_hint = known_rows.get(target_row, {}) if target_row is not None else {}
        aliases = item.get("source_aliases") or item.get("aliases") or item.get("source_label") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [str(alias).strip() for alias in aliases if str(alias or "").strip()]
        selector = {
            "target_row": target_row,
            "section": str(item.get("section") or row_hint.get("section") or "").strip(),
            "division": str(item.get("division") or row_hint.get("division") or "").strip(),
            "item": str(item.get("item") or row_hint.get("item") or "").strip(),
            "site": str(item.get("site") or row_hint.get("site") or "").strip(),
            "unit": str(item.get("unit") or row_hint.get("unit") or "").strip(),
            "source_aliases": aliases,
            "confidence": str(item.get("confidence") or "low").strip().lower(),
        }
        if not any(selector[field] for field in ("section", "division", "item", "site")) and target_row is None:
            uncertain = True
            continue
        selectors.append(selector)
        uncertain |= selector["confidence"] != "high"
    return FrameworkResult(selectors, "llm_uncertain" if uncertain else "success", raw)


def _client():
    import os
    from ollama import Client
    key = os.getenv("OLLAMA_API_KEY")
    if not key:
        return None
    return Client(host="https://ollama.com", headers={"Authorization": "Bearer " + key}, timeout=90)


def call_fallback(html: str, client=None, retries: int = 2, sleep=time.sleep) -> FallbackResult:
    if client is None:
        client = _client()
        if client is None: return FallbackResult([], "llm_disabled")
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


def call_structure(html: str, sheet_structure: dict, quarter: str, client=None, retries: int = 2, sleep=time.sleep) -> StructureResult:
    if client is None:
        client = _client()
        if client is None: return StructureResult([], "llm_disabled")
    structure_json = json.dumps(sheet_structure, ensure_ascii=False, separators=(",", ":"))[:7000]
    prompt = (STRUCTURE_USER_PROMPT
              .replace("{quarter}", quarter)
              .replace("{sheet_structure}", structure_json)
              .replace("{html}", str(html)[:12000]))
    for attempt in range(retries + 1):
        try:
            response = client.chat(model=MODEL, stream=False, options={"temperature": 0.0, "num_ctx": 16384},
                                   messages=[{"role":"system","content":STRUCTURE_SYSTEM_PROMPT}, {"role":"user","content":prompt}])
            raw = response["message"]["content"] if isinstance(response, dict) else response.message.content
            return validate_structure_response(raw, html, sheet_structure, quarter)
        except Exception:
            if attempt == retries: return StructureResult([], "llm_error")
            sleep((1, 3)[min(attempt, 1)])
    return StructureResult([], "llm_error")


def call_framework(html: str, sheet_structure: dict, client=None, retries: int = 2, sleep=time.sleep) -> FrameworkResult:
    if client is None:
        client = _client()
        if client is None: return FrameworkResult([], "llm_disabled")
    structure_json = json.dumps(sheet_structure, ensure_ascii=False, separators=(",", ":"))[:7000]
    prompt = (FRAMEWORK_USER_PROMPT
              .replace("{sheet_structure}", structure_json)
              .replace("{html}", str(html)[:12000]))
    for attempt in range(retries + 1):
        try:
            response = client.chat(model=MODEL, stream=False, options={"temperature": 0.0, "num_ctx": 16384},
                                   messages=[{"role":"system","content":FRAMEWORK_SYSTEM_PROMPT}, {"role":"user","content":prompt}])
            raw = response["message"]["content"] if isinstance(response, dict) else response.message.content
            return validate_framework_response(raw, sheet_structure)
        except Exception:
            if attempt == retries: return FrameworkResult([], "llm_error")
            sleep((1, 3)[min(attempt, 1)])
    return FrameworkResult([], "llm_error")
