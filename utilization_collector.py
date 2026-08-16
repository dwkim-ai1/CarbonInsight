"""Ten-year OpenDART utilization collection orchestrator."""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import gspread

from utilization_llm_fallback import MODEL, call_fallback, call_framework, call_structure
from utilization_parser import UtilizationParser

LOG = logging.getLogger("utilization")
KST = timezone(timedelta(hours=9))
REQUIRED = ["기업명","종목코드","corp_code","사업연도","사업부문","품목","사업소","생산능력","생산실적","가동률(%)","단위","산출식","보고서명","접수번호(rcept_no)","DART URL","수집일시","원본표(JSON)"]
LEDGER_HEADERS = ["timestamp","기업명","종목코드","corp_code","사업연도","접수번호","상태","메시지","출력시트"]
ALIASES = {
    "기업명": ("기업명","회사명","법인명","corp_name","company"),
    "종목코드": ("종목코드","상장코드","stock_code","stock code","ticker","티커","종목번호"),
    "corp_code": ("corp_code","고유번호","법인코드","dart_corp_code","DART고유번호"),
    "bucket": ("그룹","묶음","bucket","shard"),
    "sheet_id": ("Gspread_ID","gspread_id","gspread id","sheet_id","spreadsheet_id"),
}
INVALID_SHEET_TITLE_CHARS = re.compile(r"[\\/?*\[\]:]")
LEDGER_SHEET_TITLE = "가동률"
FRAMEWORK_SHEET_TITLE = "_framework"
FRAMEWORK_SCHEMA_VERSION = "utilization-framework-v1"
FRAMEWORK_HEADERS = ["output_sheet","기업명","종목코드","corp_code","updated_at","schema_version","framework_json"]
QUARTER_HEADER_START_COLUMN = 6
DEFAULT_STRUCTURE_START_YEAR = 2016
QUARTER_PATTERN = re.compile(r"^[1-4]Q\d{2}$")


def env_bool(name: str) -> bool: return os.getenv(name, "false").lower() in {"1","true","yes"}


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        LOG.warning("⚠️ %s 값이 정수가 아니어서 기본값 %d를 사용합니다.", name, default)
        return default


def env_nonnegative_int(name: str, default: int = 0) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        LOG.warning("⚠️ %s 값이 정수가 아니어서 기본값 %d를 사용합니다.", name, default)
        return default


@dataclass
class SheetsQuota:
    read_per_minute: int
    write_per_minute: int
    last_read: float = 0.0
    last_write: float = 0.0

    @classmethod
    def from_env(cls) -> "SheetsQuota":
        quota = cls(
            env_int("GOOGLE_SHEETS_READ_REQUESTS_PER_MINUTE", 45),
            env_int("GOOGLE_SHEETS_WRITE_REQUESTS_PER_MINUTE", 30),
        )
        LOG.info("📉 Google Sheets quota throttle: read=%d/min, write=%d/min", quota.read_per_minute, quota.write_per_minute)
        return quota

    def read(self):
        self._wait("read")

    def write(self):
        self._wait("write")

    def _wait(self, kind: str):
        per_minute = self.read_per_minute if kind == "read" else self.write_per_minute
        delay = 60.0 / per_minute
        attr = "last_read" if kind == "read" else "last_write"
        elapsed = time.monotonic() - getattr(self, attr)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        setattr(self, attr, time.monotonic())


def credentials():
    value = os.environ["GOOGLE_SHEETS_CREDS"]
    return gspread.service_account_from_dict(json.loads(value))


def load_sheet_ids() -> list[str]:
    value = os.getenv("gspread_ids", "").strip()
    if value:
        try:
            ids = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("gspread_ids must be a JSON array of 10 Google Sheet IDs") from exc
        if not isinstance(ids, list):
            raise ValueError("gspread_ids must be a JSON array of 10 Google Sheet IDs")
        cleaned = [str(sheet_id).strip() for sheet_id in ids]
    else:
        legacy_names = [f"gspread_id_{i}" for i in range(1, 11)]
        missing = [name for name in legacy_names if not os.getenv(name, "").strip()]
        if missing:
            raise ValueError("Set gspread_ids to a JSON array of 10 Google Sheet IDs")
        cleaned = [os.environ[name].strip() for name in legacy_names]
    if len(cleaned) != 10 or any(not sheet_id for sheet_id in cleaned):
        raise ValueError("gspread_ids must contain exactly 10 non-empty Google Sheet IDs")
    return cleaned


def detect_columns(headers: list[str]) -> dict[str, str]:
    found = {}
    lowered = {str(header).strip().lower(): header for header in headers}
    for canonical, candidates in ALIASES.items():
        match = next((lowered.get(candidate.lower()) for candidate in candidates if candidate.lower() in lowered), None)
        if match: found[canonical] = match
    missing = []
    if "기업명" not in found: missing.append("기업명/회사명")
    if "corp_code" not in found and "종목코드" not in found: missing.append("corp_code 또는 종목코드/상장코드")
    if missing: raise ValueError(f"기업 목록 헤더 매핑 실패: {missing}; 실제 헤더={headers}")
    return found


def normalize_number(value, width: int) -> str:
    text = str(value or "").strip().replace("'", "")
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(width) if text.isdigit() else text


def stock_code(record: dict, columns: dict[str, str]) -> str:
    column = columns.get("종목코드")
    return normalize_number(record.get(column, ""), 6) if column else ""


def safe_sheet_title(company: str, stock: str) -> str:
    candidate = (stock or "").strip() or (company or "").strip() or "company"
    candidate = INVALID_SHEET_TITLE_CHARS.sub("_", candidate).strip("' ").strip()
    return (candidate or "company")[:100]


def normalize_label(value: object) -> str:
    return re.sub(r"[\s()\[\]{}_%㎥㎡,./\\-]+", "", str(value or "")).lower()


def label_match(query: object, candidate: object) -> bool:
    q, c = normalize_label(query), normalize_label(candidate)
    return bool(q and c and (q == c or q in c or c in q))


def column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def default_quarter_labels(start_year: int = DEFAULT_STRUCTURE_START_YEAR, end_year: int | None = None) -> list[str]:
    end_year = end_year or datetime.now(KST).year
    return [f"{quarter}Q{str(year)[-2:]}" for year in range(start_year, end_year + 1) for quarter in range(1, 5)]


def report_quarter_label(report, business_year: int) -> str:
    text = str(report.get("report_nm", ""))
    compact = text.replace(" ", "")
    month = None
    month_match = re.search(r"20\d{2}[.\-/년]*(03|06|09|12)", compact)
    if month_match:
        month = int(month_match.group(1))
    if month:
        quarter = {3: 1, 6: 2, 9: 3, 12: 4}[month]
    elif "반기" in compact:
        quarter = 2
    elif "3분기" in compact or "제3분기" in compact:
        quarter = 3
    elif "1분기" in compact or "제1분기" in compact or "분기보고서" in compact:
        quarter = 1
    else:
        quarter = 4
    return f"{quarter}Q{str(int(business_year))[-2:]}"


def report_business_year(report, default: int | None = None) -> int | None:
    value = report.get("bsns_year")
    if value not in (None, ""):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    match = re.search(r"(20\d{2})\s*(?:[.\-/년]\s*(?:0?[369]|12))?", str(report.get("report_nm", "")))
    if match:
        return int(match.group(1))
    return default


def is_periodic_report(report) -> bool:
    compact = str(report.get("report_nm", "")).replace(" ", "")
    return any(name in compact for name in ("사업보고서", "반기보고서", "분기보고서"))


def report_sort_key(report) -> tuple[int, int, str, str]:
    year = report_business_year(report, 0) or 0
    quarter = 0
    if year:
        parts = quarter_parts(report_quarter_label(report, year))
        quarter = parts[0] if parts else 0
    return (year, quarter, str(report.get("rcept_dt", "")), str(report.get("rcept_no", "")))


def target_business_years(now_year: int, years_back: int) -> list[int]:
    years_back = max(1, int(years_back))
    return list(range(now_year - years_back + 1, now_year + 1))


def quarter_parts(label: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([1-4])Q(\d{2})", str(label or "").strip())
    return (int(match.group(1)), 2000 + int(match.group(2))) if match else None


def period_year(label: object, fallback_year: int) -> int | None:
    text = str(label or "")
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return int(match.group())
    if "전전기" in text:
        return fallback_year - 2
    if "전기" in text:
        return fallback_year - 1
    if "당기" in text:
        return fallback_year
    return None


def period_quarter(label: object, fallback_quarter: int) -> int:
    compact = str(label or "").replace(" ", "")
    match = re.search(r"([1-4])Q", compact, flags=re.I)
    if match:
        return int(match.group(1))
    month_match = re.search(r"(03|06|09|12)(?:월|\.|\-|/|$)", compact)
    if month_match:
        return {3: 1, 6: 2, 9: 3, 12: 4}[int(month_match.group(1))]
    if "반기" in compact:
        return 2
    if "3분기" in compact or "제3분기" in compact:
        return 3
    if "1분기" in compact or "제1분기" in compact:
        return 1
    return fallback_quarter


def row_quarter_label(row: dict, fallback_quarter: str) -> str:
    parts = quarter_parts(fallback_quarter)
    if not parts:
        return fallback_quarter
    fallback_q, fallback_year = parts
    period_label = row.get("사업연도") or row.get("기간") or ""
    year = period_year(period_label, fallback_year)
    quarter = period_quarter(period_label, fallback_q)
    return f"{quarter}Q{str(year)[-2:]}" if year else fallback_quarter


def selector_score(selector: dict, row: dict, section: str) -> int:
    score = 0
    if label_match(section, selector.get("section")):
        score += 5
    elif selector.get("section"):
        score -= 2
    if row.get("사업부문") and label_match(row.get("사업부문"), selector.get("division")):
        score += 3
    if row.get("품목") and label_match(row.get("품목"), selector.get("item")):
        score += 5
    if row.get("사업소") and label_match(row.get("사업소"), selector.get("site")):
        score += 3
    if row.get("단위") and label_match(row.get("단위"), selector.get("unit")):
        score += 1
    for alias in selector.get("source_aliases", []):
        if any(label_match(alias, row.get(field)) for field in ("사업부문", "품목", "사업소")):
            score += 4
            break
    return score


def selector_specific_match(selector: dict, row: dict) -> bool:
    aliases = selector.get("source_aliases", [])
    if isinstance(aliases, str):
        aliases = [aliases]
    row_fields = (row.get("사업부문"), row.get("품목"), row.get("사업소"))
    if any(alias and any(label_match(alias, field) for field in row_fields) for alias in aliases):
        return True
    if selector.get("item"):
        return any(label_match(selector.get("item"), field) for field in (row.get("품목"), row.get("사업소")))
    if selector.get("site"):
        return label_match(selector.get("site"), row.get("사업소"))
    if selector.get("division"):
        return any(label_match(selector.get("division"), field) for field in row_fields)
    return False


def select_target_row(selectors: list[dict], row: dict, section: str) -> int | None:
    best_score, best_row = 0, None
    for selector in selectors:
        if not label_match(section, selector.get("section")) or not selector_specific_match(selector, row):
            continue
        score = selector_score(selector, row, section)
        if score > best_score:
            best_score, best_row = score, selector.get("target_row")
    return best_row if best_score >= 5 else None


def rows_to_structured_updates(rows: list[dict], quarter: str, selectors: list[dict] | None = None, require_selector_match: bool = False) -> list[dict]:
    updates = []
    selectors = selectors or []
    fields = [
        ("생산능력", "생산능력", None),
        ("생산실적", "생산실적", None),
        ("가동률", "가동률(%)", "(%)"),
    ]
    for row in rows:
        row_quarter = row_quarter_label(row, quarter)
        for section, field, unit_override in fields:
            value = row.get(field)
            if value in (None, "", "-"):
                continue
            target_row = select_target_row(selectors, row, section)
            if require_selector_match and target_row is None:
                continue
            updates.append({
                "target_row": target_row,
                "section": section,
                "division": row.get("사업부문") or "",
                "item": row.get("품목") or "",
                "site": row.get("사업소") or "",
                "unit": unit_override or row.get("단위") or "",
                "quarter": row_quarter,
                "value": value,
                "source_label": row.get("품목") or row.get("사업소") or row.get("사업연도") or section,
                "confidence": "high",
            })
    return updates


def can_use_llm(call_count: int, max_calls: int, company_counts: dict[str, int], company_key: str, max_per_company: int) -> bool:
    return bool(os.getenv("OLLAMA_API_KEY") and call_count < max_calls and company_counts.get(company_key, 0) < max_per_company)


def resolve_company_codes(dart, record: dict, columns: dict[str, str]) -> tuple[str, str]:
    name = str(record.get(columns["기업명"], "")).strip()
    stock = stock_code(record, columns)
    corp_column = columns.get("corp_code")
    if corp_column:
        corp = normalize_number(record.get(corp_column, ""), 8)
        if corp:
            return corp, stock
    lookup = stock or name
    corp = dart.find_corp_code(lookup) if lookup else ""
    if not corp and name and lookup != name:
        corp = dart.find_corp_code(name)
    if not corp:
        raise ValueError(f"DART corp_code 조회 실패: 회사명={name}, lookup={lookup}")
    return str(corp).zfill(8), stock


def distribute(records: list[dict], columns: dict[str, str], sheet_ids: list[str]) -> tuple[dict[int, list[dict]], list[dict]]:
    shards = {i: [] for i in range(1, 11)}
    overflow = []
    sheet_column = columns.get("sheet_id")
    sheet_to_shard = {sheet_id: index for index, sheet_id in enumerate(sheet_ids, start=1)}
    for index, record in enumerate(records):
        shard = None
        if sheet_column:
            sheet_id = str(record.get(sheet_column, "")).strip()
            if sheet_id:
                shard = sheet_to_shard.get(sheet_id)
                if shard is None:
                    overflow.append(record)
                    continue
        if shard is None:
            raw = record.get(columns.get("bucket", ""), "")
            try: shard = int(raw) if raw != "" else index // 20 + 1
            except ValueError: shard = index // 20 + 1
        (shards[shard] if shard in shards else overflow).append(record)
    return shards, overflow


def selected_company_work(shards: dict[int, list[dict]], selected: list[int] | range, offset: int = 0, limit: int = 0) -> list[tuple[int, dict]]:
    work = [(shard, company) for shard in selected for company in shards[shard]]
    end = offset + limit if limit else None
    return work[offset:end]


class SheetStore:
    def __init__(self, gc, sheet_ids: list[str], quota: SheetsQuota, test_mode=False):
        self.gc, self.ids, self.quota, self.test_mode = gc, sheet_ids, quota, test_mode
        self.headers = list(REQUIRED)
        self.quarter_labels = default_quarter_labels()
        self.next_rows: dict[int, int] = {}
        self.ledger_next_rows: dict[int, int] = {}
        self.framework_ready: set[int] = set()
        self.framework_rows: dict[int, dict[str, tuple[int, list[str]]]] = {}
        LOG.info("✅ 출력 스키마 준비: 헤더=%s", self.headers)

    def _find_worksheet(self, worksheets, title: str):
        return next((ws for ws in worksheets if ws.title == title), None)

    def _ensure_data_table(self, ws):
        key = ws.id
        if key in self.next_rows:
            return
        self.quota.read(); values = ws.get_all_values()
        header_row = None
        for index, row_values in enumerate(values, start=1):
            if row_values[:len(self.headers)] == self.headers:
                header_row = index
                break
        if header_row is None:
            next_row = len(values) + 1 if values else 1
            if self.test_mode:
                LOG.info("DRY-RUN %s!A%d에 출력 헤더 작성 예정", ws.title, next_row)
            else:
                self.quota.write(); ws.update(f"A{next_row}", [self.headers])
            self.next_rows[key] = next_row + 1
        else:
            self.next_rows[key] = max(len(values) + 1, header_row + 1)

    def _ensure_quarter_headers(self, ws):
        self.quota.read(); headers = ws.row_values(1)
        if any(QUARTER_PATTERN.fullmatch(str(value or "")) for value in headers[QUARTER_HEADER_START_COLUMN - 1:]):
            return
        if self.test_mode:
            LOG.info("DRY-RUN %s!F1에 기본 분기 헤더 작성 예정", ws.title)
        else:
            self.quota.write(); ws.update(f"{column_letter(QUARTER_HEADER_START_COLUMN)}1", [self.quarter_labels])

    def _ensure_quarter_column(self, ws, quarter: str) -> int:
        self.quota.read(); headers = ws.row_values(1)
        for index, value in enumerate(headers, start=1):
            if str(value).strip() == quarter:
                return index
        if quarter in self.quarter_labels:
            column = QUARTER_HEADER_START_COLUMN + self.quarter_labels.index(quarter)
        else:
            column = max(len(headers) + 1, QUARTER_HEADER_START_COLUMN)
        if self.test_mode:
            LOG.info("DRY-RUN %s!%s1에 분기 헤더 %s 작성 예정", ws.title, column_letter(column), quarter)
        else:
            self.quota.write(); ws.update(f"{column_letter(column)}1", [[quarter]])
        return column

    def _ensure_ledger(self, ledger):
        if ledger is None:
            return
        key = ledger.id
        if key in self.ledger_next_rows:
            return
        self.quota.read(); values = ledger.get_all_values()
        header_row = None
        for index, row_values in enumerate(values, start=1):
            if row_values[:len(LEDGER_HEADERS)] == LEDGER_HEADERS:
                header_row = index
                break
        if header_row is None:
            next_row = len(values) + 1 if values else 1
            if self.test_mode:
                LOG.info("DRY-RUN %s!A%d에 장부 헤더 작성 예정", ledger.title, next_row)
            else:
                self.quota.write(); ledger.update(f"A{next_row}", [LEDGER_HEADERS])
            self.ledger_next_rows[key] = next_row + 1
        else:
            self.ledger_next_rows[key] = max(len(values) + 1, header_row + 1)

    def _ensure_framework_store(self, book, worksheets):
        framework = self._find_worksheet(worksheets, FRAMEWORK_SHEET_TITLE)
        if framework is None:
            if self.test_mode:
                LOG.info("DRY-RUN %s framework JSON 시트 생성 예정", FRAMEWORK_SHEET_TITLE)
                return None
            self.quota.write(); framework = book.add_worksheet(FRAMEWORK_SHEET_TITLE, rows=500, cols=len(FRAMEWORK_HEADERS))
            LOG.info("🧾 framework JSON 시트 생성: %s", FRAMEWORK_SHEET_TITLE)
        key = framework.id
        if key not in self.framework_ready:
            self.quota.read(); headers = framework.row_values(1)
            if headers[:len(FRAMEWORK_HEADERS)] != FRAMEWORK_HEADERS:
                if self.test_mode:
                    LOG.info("DRY-RUN %s!A1 framework 헤더 작성 예정", framework.title)
                else:
                    self.quota.write(); framework.update("A1", [FRAMEWORK_HEADERS])
            self.framework_ready.add(key)
        return framework

    def _framework_row_data(self, framework) -> dict[str, tuple[int, list[str]]]:
        if framework is None:
            return {}
        key = framework.id
        if key not in self.framework_rows:
            self.quota.read(); values = framework.get_all_values()
            rows = {}
            if values:
                headers = [str(value or "").strip() for value in values[0]]
                try:
                    output_col = headers.index("output_sheet")
                except ValueError:
                    output_col = 0
                for index, row_values in enumerate(values[1:], start=2):
                    padded = list(row_values) + [""] * len(FRAMEWORK_HEADERS)
                    output_sheet = str(padded[output_col] or "").strip()
                    if output_sheet:
                        rows[output_sheet] = (index, padded[:len(FRAMEWORK_HEADERS)])
            self.framework_rows[key] = rows
        return self.framework_rows[key]

    def load_framework(self, framework, output_sheet: str) -> list[dict]:
        rows = self._framework_row_data(framework)
        row_info = rows.get(output_sheet)
        if not row_info:
            return []
        values = list(row_info[1]) + [""] * len(FRAMEWORK_HEADERS)
        raw = str(values[FRAMEWORK_HEADERS.index("framework_json")] or "").strip()
        if not raw:
            return []
        try:
            payload = json.loads(raw)
            selectors = payload.get("selectors", payload.get("framework", [])) if isinstance(payload, dict) else payload
        except (ValueError, json.JSONDecodeError) as exc:
            LOG.warning("⚠️ %s framework JSON 로드 실패: %s", output_sheet, exc)
            return []
        if not isinstance(selectors, list):
            LOG.warning("⚠️ %s framework JSON 형식 오류: selectors가 배열이 아닙니다.", output_sheet)
            return []
        cleaned = [selector for selector in selectors if isinstance(selector, dict)]
        if cleaned:
            LOG.info("🧾 %s framework JSON 재사용: %d selectors", output_sheet, len(cleaned))
        return cleaned

    def save_framework(self, framework, company: str, stock: str, corp: str, output_sheet: str, selectors: list[dict]) -> int:
        if framework is None or not selectors:
            return 0
        rows = self._framework_row_data(framework)
        row_info = rows.get(output_sheet)
        row_index = row_info[0] if row_info else max((item[0] for item in rows.values()), default=1) + 1
        payload = {"schema_version": FRAMEWORK_SCHEMA_VERSION, "selectors": selectors}
        values = [
            output_sheet,
            company,
            stock,
            corp,
            datetime.now(KST).isoformat(),
            FRAMEWORK_SCHEMA_VERSION,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ]
        end_col = column_letter(len(FRAMEWORK_HEADERS))
        if self.test_mode:
            LOG.info("DRY-RUN %s!A%d:%s%d framework JSON 저장 예정", framework.title, row_index, end_col, row_index)
        else:
            self.quota.write(); framework.update(f"A{row_index}:{end_col}{row_index}", [values], value_input_option="USER_ENTERED")
        rows[output_sheet] = (row_index, values)
        return row_index

    def setup(self, shard: int, company: str, stock: str):
        self.quota.read(); book = self.gc.open_by_key(self.ids[shard - 1])
        title = safe_sheet_title(company, stock)
        self.quota.read(); worksheets = book.worksheets()
        ws = self._find_worksheet(worksheets, title)
        if ws is None:
            if self.test_mode:
                ws = worksheets[0]
                LOG.info("DRY-RUN 회사별 시트 생성 예정: %s", title)
            else:
                cols = max(QUARTER_HEADER_START_COLUMN + len(self.quarter_labels), len(self.headers), 30)
                self.quota.write(); ws = book.add_worksheet(title, rows=200, cols=cols)
                LOG.info("📄 회사별 빈 시트 생성: %s", title)
        self._ensure_quarter_headers(ws)
        ledger = self._find_worksheet(worksheets, LEDGER_SHEET_TITLE)
        if ledger is None:
            if self.test_mode:
                ledger = None
                LOG.info("DRY-RUN %s 장부 시트 생성 예정", LEDGER_SHEET_TITLE)
            else:
                self.quota.write(); ledger = book.add_worksheet(LEDGER_SHEET_TITLE, rows=2000, cols=max(9, len(LEDGER_HEADERS)))
                LOG.info("📒 장부 시트 생성: %s", LEDGER_SHEET_TITLE)
        self._ensure_ledger(ledger)
        framework = self._ensure_framework_store(book, worksheets)
        return ws, ledger, framework

    def sheet_structure(self, ws) -> dict:
        self._ensure_quarter_headers(ws)
        self.quota.read(); values = ws.get_all_values()
        first_row = values[0] if values else []
        quarters = []
        for col, label in enumerate(first_row, start=1):
            text = str(label or "").strip()
            if col >= QUARTER_HEADER_START_COLUMN and text:
                quarters.append({"col": col, "label": text})
        rows = []
        section = division = ""
        for index, row in enumerate(values[1:], start=2):
            padded = list(row) + [""] * 5
            if padded[:len(self.headers)] == self.headers:
                break
            a, b, c, d, e = [str(value or "").strip() for value in padded[:5]]
            if a:
                section = a
                if not any([b, c, d, e]):
                    division = ""
                    continue
            if b and not any([c, d, e]):
                division = b
                continue
            if c or e or (b and section):
                rows.append({"row": index, "section": section, "division": division if c or e else "", "item": c or b, "site": d, "unit": e})
        return {"title": ws.title, "quarters": quarters, "rows": rows[:160]}

    def _match_structure_row(self, structure: dict, update: dict) -> int | None:
        target = update.get("target_row")
        if target:
            known = {row["row"] for row in structure.get("rows", [])}
            if target in known:
                return target
        best_score, best_row = 0, None
        for row in structure.get("rows", []):
            score = 0
            if update.get("section"):
                if label_match(update["section"], row.get("section")):
                    score += 4
                elif label_match(update["section"], row.get("division")):
                    score += 3
                else:
                    score -= 2
            if update.get("division") and label_match(update["division"], row.get("division")):
                score += 3
            if update.get("item") and label_match(update["item"], row.get("item")):
                score += 5
            elif update.get("source_label") and label_match(update["source_label"], row.get("item")):
                score += 3
            if update.get("site") and label_match(update["site"], row.get("site")):
                score += 2
            if update.get("unit") and label_match(update["unit"], row.get("unit")):
                score += 1
            if score > best_score:
                best_score, best_row = score, row.get("row")
        return best_row if best_score >= 5 else None

    def _structure_labels(self, update: dict) -> list[str]:
        section = str(update.get("section", "") or "")
        division = str(update.get("division", "") or "")
        item = str(update.get("item", "") or "")
        site = str(update.get("site", "") or "")
        unit = str(update.get("unit", "") or "")
        aliases = update.get("source_aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if not any([item, site, unit]) and (section or division):
            item = str(update.get("source_label") or next((alias for alias in aliases if alias), "") or division or section)
        return [section, division, item, site, unit]

    def _insert_structure_rows(self, ws, structure: dict, labels_list: list[list[str]], insert_at: int | None = None) -> list[int]:
        if not labels_list:
            return []
        rows = structure.get("rows", [])
        insert_at = insert_at or max((row.get("row", 1) for row in rows), default=1) + 1
        if self.test_mode:
            LOG.info("DRY-RUN %s!A%d부터 구조 행 %d개 생성 예정", ws.title, insert_at, len(labels_list))
        else:
            self.quota.write()
            if len(labels_list) == 1:
                ws.insert_row(labels_list[0], index=insert_at, value_input_option="USER_ENTERED")
            else:
                ws.insert_rows(labels_list, row=insert_at, value_input_option="USER_ENTERED")
        inserted_rows = []
        for offset, labels in enumerate(labels_list):
            row_number = insert_at + offset
            inserted_rows.append(row_number)
            structure.setdefault("rows", []).append({"row": row_number, "section": labels[0], "division": labels[1], "item": labels[2], "site": labels[3], "unit": labels[4]})
        return inserted_rows

    def _insert_structure_row(self, ws, structure: dict, update: dict) -> int:
        return self._insert_structure_rows(ws, structure, [self._structure_labels(update)])[0]

    def apply_framework(self, ws, selectors: list[dict]) -> list[dict]:
        if not selectors:
            return []
        structure = self.sheet_structure(ws)
        bound = []
        labels_to_insert = []
        planned: dict[tuple[str, ...], int] = {}
        insert_at = max((row.get("row", 1) for row in structure.get("rows", [])), default=1) + 1
        for selector in selectors:
            row = self._match_structure_row(structure, selector)
            if row is None:
                labels = self._structure_labels(selector)
                key = tuple(labels)
                row = planned.get(key)
                if row is None:
                    row = insert_at + len(labels_to_insert)
                    planned[key] = row
                    labels_to_insert.append(labels)
                    structure.setdefault("rows", []).append({"row": row, "section": labels[0], "division": labels[1], "item": labels[2], "site": labels[3], "unit": labels[4]})
            prepared = dict(selector)
            prepared["target_row"] = row
            bound.append(prepared)
        if labels_to_insert:
            if self.test_mode:
                LOG.info("DRY-RUN %s!A%d부터 selector 구조 행 %d개 생성 예정", ws.title, insert_at, len(labels_to_insert))
            else:
                self.quota.write()
                if len(labels_to_insert) == 1:
                    ws.insert_row(labels_to_insert[0], index=insert_at, value_input_option="USER_ENTERED")
                else:
                    ws.insert_rows(labels_to_insert, row=insert_at, value_input_option="USER_ENTERED")
        return bound

    def _ensure_quarter_columns(self, ws, structure: dict, quarters: list[str]) -> dict[str, int]:
        quarter_columns = {str(item.get("label") or "").strip(): int(item["col"])
                           for item in structure.get("quarters", []) if str(item.get("label") or "").strip() and str(item.get("col", "")).isdigit()}
        planned_updates = []
        used_columns = set(quarter_columns.values())
        for quarter in quarters:
            if quarter in quarter_columns:
                continue
            if quarter in self.quarter_labels:
                column = QUARTER_HEADER_START_COLUMN + self.quarter_labels.index(quarter)
            else:
                column = max(used_columns or {QUARTER_HEADER_START_COLUMN - 1}) + 1
            while column in used_columns:
                column += 1
            quarter_columns[quarter] = column
            used_columns.add(column)
            planned_updates.append({"range": f"{column_letter(column)}1", "values": [[quarter]]})
            structure.setdefault("quarters", []).append({"col": column, "label": quarter})
        if planned_updates:
            if self.test_mode:
                LOG.info("DRY-RUN %s 분기 헤더 %d개 batch 작성 예정", ws.title, len(planned_updates))
            else:
                self.quota.write(); ws.batch_update(planned_updates, value_input_option="USER_ENTERED")
        return quarter_columns

    def _batch_update_cells(self, ws, cells: dict[str, object]) -> int:
        if not cells:
            return 0
        requests = [{"range": cell, "values": [[value]]} for cell, value in cells.items()]
        if self.test_mode:
            LOG.info("DRY-RUN %s 셀 %d개 batch update 예정", ws.title, len(requests))
            return len(requests)
        for index in range(0, len(requests), 500):
            self.quota.write(); ws.batch_update(requests[index:index + 500], value_input_option="USER_ENTERED")
        return len(requests)

    def apply_structured_updates(self, ws, updates: list[dict], allow_new_rows: bool = True) -> int:
        if not updates:
            return 0
        structure = self.sheet_structure(ws)
        quarters = []
        for update in updates:
            quarter = str(update.get("quarter") or "").strip()
            if quarter and quarter not in quarters:
                quarters.append(quarter)
        quarter_columns = self._ensure_quarter_columns(ws, structure, quarters)
        labels_to_insert = []
        planned: dict[tuple[str, ...], int] = {}
        insert_at = max((row.get("row", 1) for row in structure.get("rows", [])), default=1) + 1
        cells: dict[str, object] = {}
        for update in updates:
            quarter = str(update.get("quarter") or "").strip()
            value = update.get("value")
            if not quarter or value in (None, ""):
                continue
            column = quarter_columns[quarter]
            row = self._match_structure_row(structure, update)
            if row is None:
                if not allow_new_rows:
                    LOG.warning("⚠️ %s framework 밖 update skip: %s/%s", ws.title, update.get("section"), update.get("source_label") or update.get("item"))
                    continue
                labels = self._structure_labels(update)
                key = tuple(labels)
                row = planned.get(key)
                if row is None:
                    row = insert_at + len(labels_to_insert)
                    planned[key] = row
                    labels_to_insert.append(labels)
                    structure.setdefault("rows", []).append({"row": row, "section": labels[0], "division": labels[1], "item": labels[2], "site": labels[3], "unit": labels[4]})
            cell = f"{column_letter(column)}{row}"
            cells[cell] = value
        if labels_to_insert:
            if self.test_mode:
                LOG.info("DRY-RUN %s!A%d부터 parser 구조 행 %d개 생성 예정", ws.title, insert_at, len(labels_to_insert))
            else:
                self.quota.write()
                if len(labels_to_insert) == 1:
                    ws.insert_row(labels_to_insert[0], index=insert_at, value_input_option="USER_ENTERED")
                else:
                    ws.insert_rows(labels_to_insert, row=insert_at, value_input_option="USER_ENTERED")
        return self._batch_update_cells(ws, cells)

    def append(self, ws, row):
        self.append_rows(ws, [row])

    def append_rows(self, ws, rows: list[dict]) -> int:
        if not rows:
            return 0
        values = [[row.get(header, "") for header in self.headers] for row in rows]
        row_index = self.next_rows.get(ws.id)
        if row_index is None:
            self._ensure_data_table(ws)
            row_index = self.next_rows[ws.id]
        end_row = row_index + len(values) - 1
        end_col = column_letter(len(self.headers))
        if self.test_mode:
            LOG.info("DRY-RUN %s!A%d:%s%d raw row %d개 batch append 예정", ws.title, row_index, end_col, end_row, len(values))
        else:
            self.quota.write(); ws.update(f"A{row_index}:{end_col}{end_row}", values, value_input_option="USER_ENTERED")
        self.next_rows[ws.id] = end_row + 1
        return len(values)

    def history_values(self, company: str, stock: str, corp: str, year: int | str, rcept: str, status: str, message: str, output_sheet: str) -> list:
        return [datetime.now(KST).isoformat(), company, stock, corp, year, rcept, status, str(message)[:500], output_sheet]

    def record_history(self, ledger, company: str, stock: str, corp: str, year: int, rcept: str, status: str, message: str, output_sheet: str):
        self.record_history_rows(ledger, [self.history_values(company, stock, corp, year, rcept, status, message, output_sheet)])

    def record_history_rows(self, ledger, rows: list[list]) -> int:
        if not rows:
            return 0
        if ledger is None or self.test_mode:
            for values in rows:
                LOG.info("DRY-RUN 장부 기록 %s", dict(zip(LEDGER_HEADERS, values)))
            return len(rows)
        row_index = self.ledger_next_rows.get(ledger.id)
        if row_index is None:
            self._ensure_ledger(ledger)
            row_index = self.ledger_next_rows[ledger.id]
        end_row = row_index + len(rows) - 1
        end_col = column_letter(len(LEDGER_HEADERS))
        self.quota.write(); ledger.update(f"A{row_index}:{end_col}{end_row}", rows, value_input_option="USER_ENTERED")
        self.ledger_next_rows[ledger.id] = end_row + 1
        return len(rows)


def reports(dart, corp_code: str, years: list[int]):
    """Fetch regular quarterly, half-year, and annual filings for the requested fiscal years."""
    target_years = sorted({int(year) for year in years})
    seen: set[str] = set()
    collected = []
    for year in target_years:
        for attempt in range(3):
            try:
                frame = dart.list(corp_code, start=f"{year}0101", end=f"{year+1}0630", kind="A")
                if frame is not None and len(frame):
                    for _, report in frame.iterrows():
                        rcept = str(report.get("rcept_no", "")).strip()
                        business_year = report_business_year(report, year)
                        if not rcept or rcept in seen or business_year not in target_years or not is_periodic_report(report):
                            continue
                        seen.add(rcept)
                        collected.append(report)
                break
            except Exception as exc:
                if attempt == 2: LOG.warning("⚠️ 보고서 조회 실패: %s/%s %s", corp_code, year, exc)
                else: time.sleep(2 ** attempt)
        time.sleep(.2)
    for report in sorted(collected, key=report_sort_key):
        yield report


def main() -> int:
    logging.basicConfig(level=logging.DEBUG if env_bool("DEBUG_MODE") else logging.INFO, format="%(asctime)s %(message)s")
    quota = SheetsQuota.from_env()
    try:
        gc = credentials()
        ids = load_sheet_ids()
        quota.read(); list_book = gc.open_by_key(os.environ["gspread_list"])
        first_sheet = list_book.get_worksheet(0)
        quota.read(); records = first_sheet.get_all_records()
        quota.read(); headers = first_sheet.row_values(1)
        columns = detect_columns(headers)
        shards, overflow = distribute(records, columns, ids)
        if overflow:
            LOG.warning("⚠️ shard 배정 제외: %d건. Gspread_ID가 gspread_ids와 일치하는지 확인하세요.", len(overflow))
        for number, companies in shards.items():
            LOG.info("📊 shard %d: %s", number, ", ".join(str(c.get(columns["기업명"], "")) for c in companies))
        store = SheetStore(gc, ids, quota, env_bool("TEST_MODE"))
    except Exception as exc:
        LOG.error("❌ 시트 인증/초기화 완전 실패: %s", exc); return 1
    target = os.getenv("TARGET_SHARD", "all")
    selected = range(1, 11) if target == "all" else [int(target)]
    years_back = env_int("YEARS_BACK", 10); now_year = datetime.now(KST).year
    business_years = target_business_years(now_year, years_back)
    llm_max_calls = env_int("LLM_MAX_CALLS", 240)
    llm_max_calls_per_company = env_int("LLM_MAX_CALLS_PER_COMPANY", 1)
    llm_per_report_max_calls = env_int("LLM_PER_REPORT_MAX_CALLS", 10000)
    llm_per_report_max_calls_per_company = env_int("LLM_PER_REPORT_MAX_CALLS_PER_COMPANY", 60)
    llm_exception_max_calls = env_int("LLM_EXCEPTION_MAX_CALLS", 60)
    llm_exception_max_calls_per_company = env_int("LLM_EXCEPTION_MAX_CALLS_PER_COMPANY", 1)
    structure_mode = os.getenv("LLM_STRUCTURE_MODE", "true").lower() in {"1","true","yes"}
    llm_per_report_mode = os.getenv("LLM_PER_REPORT_MODE", "false").lower() in {"1","true","yes"}
    if llm_per_report_mode and not structure_mode:
        LOG.warning("⚠️ LLM_PER_REPORT_MODE는 framework 구조가 필요하므로 LLM_STRUCTURE_MODE를 활성화합니다.")
        structure_mode = True
    value_fallback_mode = os.getenv("LLM_VALUE_FALLBACK_MODE", "false").lower() in {"1","true","yes"}
    exception_fallback_mode = os.getenv("LLM_EXCEPTION_FALLBACK_MODE", "true").lower() in {"1","true","yes"}
    structured_requires_selectors = os.getenv("STRUCTURED_WRITES_REQUIRE_SELECTORS", "true").lower() in {"1","true","yes"}
    if llm_per_report_mode and not structured_requires_selectors:
        LOG.warning("⚠️ LLM_PER_REPORT_MODE에서는 framework 밖 row 생성을 막기 위해 STRUCTURED_WRITES_REQUIRE_SELECTORS를 활성화합니다.")
        structured_requires_selectors = True
    append_raw_rows = os.getenv("APPEND_RAW_ROWS", "false").lower() in {"1","true","yes"}
    force_reprocess = env_bool("FORCE_REPROCESS")
    LOG.info("📆 대상 사업연도: %s", ",".join(str(year) for year in business_years))
    LOG.info("🤖 LLM 호출 상한: 일반=%d/%d per company, 보고서별=%d/%d per company, 예외=%d/%d per company, 프레임워크=%s, 보고서별=%s, 값 fallback=%s, 예외 fallback=%s, selector필수=%s, raw append=%s",
             llm_max_calls, llm_max_calls_per_company, llm_per_report_max_calls,
             llm_per_report_max_calls_per_company, llm_exception_max_calls,
             llm_exception_max_calls_per_company, structure_mode, llm_per_report_mode, value_fallback_mode,
             exception_fallback_mode, structured_requires_selectors, append_raw_rows)
    successes = regular_llm_calls = per_report_llm_calls = exception_llm_calls = 0
    llm_company_counts: dict[str, int] = {}
    llm_per_report_company_counts: dict[str, int] = {}
    llm_exception_company_counts: dict[str, int] = {}
    try:
        import OpenDartReader
        dart = OpenDartReader(os.environ["opendart_api"])
        parser = UtilizationParser()
        company_offset = env_nonnegative_int("COMPANY_OFFSET", 0)
        company_limit = env_nonnegative_int("COMPANY_LIMIT", 0)
        company_work = selected_company_work(shards, selected, company_offset, company_limit)
        if company_offset or company_limit:
            LOG.info("🧩 기업 chunk: offset=%d, limit=%s, selected=%d", company_offset, company_limit or "all", len(company_work))
        total = len(company_work)
        for position, (shard, company) in enumerate(company_work, start=1):
                name = str(company.get(columns["기업명"], "")).strip()
                try:
                    corp, stock = resolve_company_codes(dart, company, columns)
                except Exception as exc:
                    LOG.warning("⚠️ 기업 코드 매핑 실패 %s: %s", name, exc)
                    continue
                LOG.info("[%d/%d] %s 처리 중…", position, total, name)
                ws, ledger, framework_store = store.setup(shard, name, stock)
                llm_key = stock or corp
                selectors: list[dict] = []
                framework_attempted = False
                company_updates: list[dict] = []
                legacy_rows: list[dict] = []
                history_rows: list[list] = []
                llm_structure_snapshot = None
                if structure_mode and not force_reprocess:
                    saved_selectors = store.load_framework(framework_store, ws.title)
                    if saved_selectors:
                        selectors = store.apply_framework(ws, saved_selectors)
                        if selectors != saved_selectors:
                            store.save_framework(framework_store, name, stock, corp, ws.title, selectors)
                        framework_attempted = True
                        llm_structure_snapshot = store.sheet_structure(ws)
                        history_rows.append(store.history_values(name, stock, corp, now_year, "", "framework_reused", f"{len(selectors)} selectors from JSON", ws.title))
                for report in reports(dart, corp, business_years):
                    rcept = str(report.get("rcept_no", "")); year = report_business_year(report, now_year) or now_year
                    quarter = report_quarter_label(report, year)
                    try:
                        html = dart.document(rcept)
                        parse_result = parser.parse_with_diagnostics(html, year)
                        rows = parse_result.rows
                        if parse_result.status != "success":
                            LOG.warning("⚠️ parser signal %s/%s: %s (%s)", name, rcept, parse_result.status, parse_result.message)
                        can_llm = can_use_llm(regular_llm_calls, llm_max_calls, llm_company_counts, llm_key, llm_max_calls_per_company)
                        if structure_mode and not framework_attempted and can_llm:
                            framework_attempted = True
                            regular_llm_calls += 1
                            llm_company_counts[llm_key] = llm_company_counts.get(llm_key, 0) + 1
                            framework = call_framework(html, store.sheet_structure(ws))
                            selectors = store.apply_framework(ws, framework.selectors)
                            store.save_framework(framework_store, name, stock, corp, ws.title, selectors)
                            llm_structure_snapshot = store.sheet_structure(ws)
                            history_rows.append(store.history_values(name, stock, corp, year, rcept, f"framework_{framework.status}", f"{len(selectors)} selectors via {MODEL}; saved JSON", ws.title))
                        if llm_per_report_mode:
                            if not selectors:
                                history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                         "llm_per_report_skipped",
                                                                         "no framework selectors; skipped parser writes in per-report mode", ws.title))
                                continue
                            can_per_report_llm = can_use_llm(per_report_llm_calls, llm_per_report_max_calls,
                                                             llm_per_report_company_counts, llm_key,
                                                             llm_per_report_max_calls_per_company)
                            if not can_per_report_llm:
                                history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                         "llm_per_report_skipped",
                                                                         "no OLLAMA_API_KEY or per-report budget exhausted", ws.title))
                                continue
                            llm_per_report_company_counts[llm_key] = llm_per_report_company_counts.get(llm_key, 0) + 1
                            per_report_llm_calls += 1
                            structure = call_structure(html, llm_structure_snapshot or store.sheet_structure(ws), quarter)
                            if structure.updates:
                                company_updates.extend(structure.updates)
                                history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                         f"llm_per_report_{structure.status}",
                                                                         f"{len(structure.updates)} cells queued via {MODEL}", ws.title))
                                successes += 1
                            else:
                                history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                         f"llm_per_report_{structure.status}",
                                                                         f"0 cells via {MODEL}", ws.title))
                            continue
                        updates = []
                        if rows:
                            updates = rows_to_structured_updates(rows, quarter, selectors, structured_requires_selectors and structure_mode)
                            if updates:
                                company_updates.extend(updates)
                                history_rows.append(store.history_values(name, stock, corp, year, rcept, "structured_parser_queued", f"{len(updates)} cells queued for company batch write", ws.title))
                                successes += 1
                                continue
                            if structured_requires_selectors and structure_mode:
                                history_rows.append(store.history_values(name, stock, corp, year, rcept, "structured_parser_unmatched", f"{len(rows)} parsed rows skipped outside LLM selectors", ws.title))
                        can_exception_llm = can_use_llm(exception_llm_calls, llm_exception_max_calls, llm_exception_company_counts,
                                                        llm_key, llm_exception_max_calls_per_company)
                        selector_miss = bool(rows) and structured_requires_selectors and structure_mode and not updates
                        if exception_fallback_mode and (parse_result.needs_llm or selector_miss) and can_exception_llm:
                            llm_exception_company_counts[llm_key] = llm_exception_company_counts.get(llm_key, 0) + 1
                            exception_llm_calls += 1
                            structure = call_structure(html, store.sheet_structure(ws), quarter)
                            if structure.updates:
                                company_updates.extend(structure.updates)
                                history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                         f"llm_exception_{structure.status}",
                                                                        f"{parse_result.status}; {len(structure.updates)} cells queued via {MODEL}", ws.title))
                                successes += 1
                                continue
                            history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                     f"llm_exception_{structure.status}",
                                                                     f"{parse_result.status}; 0 cells via {MODEL}", ws.title))
                        elif exception_fallback_mode and (parse_result.needs_llm or selector_miss):
                            history_rows.append(store.history_values(name, stock, corp, year, rcept,
                                                                     "llm_exception_skipped",
                                                                     f"{parse_result.status if parse_result.needs_llm else 'selector_miss'}; no OLLAMA_API_KEY or exception budget exhausted", ws.title))
                        can_llm = can_use_llm(regular_llm_calls, llm_max_calls, llm_company_counts, llm_key, llm_max_calls_per_company)
                        if value_fallback_mode and not rows and can_llm:
                            llm_company_counts[llm_key] = llm_company_counts.get(llm_key, 0) + 1
                            regular_llm_calls += 1; fallback = call_fallback(html)
                            rows = fallback.rows
                            for row in rows:
                                row["가동률(%)"] = row.pop("가동률", None)
                                row["원본표(JSON)"] = json.dumps({"source":"llm_fallback", "model":MODEL,
                                                                 "raw":fallback.raw, "html":str(html)[:6000]}, ensure_ascii=False)
                            if fallback.status != "success": LOG.warning("⚠️ %s: %s", name, fallback.status)
                        for row in rows:
                            row.update({"기업명":name,"종목코드":stock,"corp_code":corp,"사업연도":row.get("사업연도") or year,"보고서명":report.get("report_nm", ""),"접수번호(rcept_no)":rcept,"DART URL":f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}","수집일시":datetime.now(KST).isoformat()})
                            if append_raw_rows:
                                legacy_rows.append(row)
                        status = "raw_rows_queued" if rows and append_raw_rows else "parsed_unwritten" if rows else "no_rows"
                        raw_message = "queued" if append_raw_rows else "not_appended"
                        history_rows.append(store.history_values(name, stock, corp, year, rcept, status, f"{len(rows)} rows {raw_message}; parser={parse_result.status}", ws.title))
                        successes += bool(rows and append_raw_rows)
                    except Exception as exc:
                        LOG.warning("⚠️ 부분 실패 %s/%s: %s", name, rcept, exc)
                        history_rows.append(store.history_values(name, stock, corp, year, rcept, "failure", str(exc), ws.title))
                        if env_bool("DEBUG_MODE"): Path("debug").mkdir(exist_ok=True); Path(f"debug/{corp}_{rcept}.html").write_text(str(locals().get("html", "")), encoding="utf-8")
                if company_updates:
                    try:
                        applied = store.apply_structured_updates(ws, company_updates, allow_new_rows=not ((structured_requires_selectors and structure_mode) or llm_per_report_mode))
                        history_rows.append(store.history_values(name, stock, corp, now_year, "", "batch_structured_write", f"{applied} cells from {len(company_updates)} queued updates", ws.title))
                        LOG.info("✅ %s batch structured write: %d cells", name, applied)
                    except Exception as exc:
                        LOG.warning("⚠️ 기업 구조 데이터 batch write 실패 %s: %s", name, exc)
                        history_rows.append(store.history_values(name, stock, corp, now_year, "", "batch_structured_failure", str(exc), ws.title))
                if legacy_rows:
                    try:
                        appended = store.append_rows(ws, legacy_rows)
                        history_rows.append(store.history_values(name, stock, corp, now_year, "", "batch_raw_append", f"{appended} rows", ws.title))
                        LOG.info("✅ %s batch raw append: %d rows", name, appended)
                    except Exception as exc:
                        LOG.warning("⚠️ 기업 raw row batch append 실패 %s: %s", name, exc)
                        history_rows.append(store.history_values(name, stock, corp, now_year, "", "batch_raw_failure", str(exc), ws.title))
                try:
                    store.record_history_rows(ledger, history_rows)
                except Exception as exc:
                    LOG.warning("⚠️ 장부 batch write 실패 %s: %s", name, exc)
        LOG.info("✅ 수집 완료: 성공 보고서=%d, LLM 호출=%d (일반=%d, 보고서별=%d, 예외=%d)",
                 successes, regular_llm_calls + per_report_llm_calls + exception_llm_calls,
                 regular_llm_calls, per_report_llm_calls, exception_llm_calls)
        return 0 if successes or env_bool("TEST_MODE") else 1
    except Exception as exc:
        LOG.error("❌ OpenDART 완전 실패: %s", exc); return 1


if __name__ == "__main__": sys.exit(main())
