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

from utilization_llm_fallback import MODEL, call_fallback, call_structure
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


class SheetStore:
    def __init__(self, gc, sheet_ids: list[str], quota: SheetsQuota, test_mode=False):
        self.gc, self.ids, self.quota, self.test_mode = gc, sheet_ids, quota, test_mode
        self.headers = list(REQUIRED)
        self.quarter_labels = default_quarter_labels()
        self.next_rows: dict[int, int] = {}
        self.ledger_next_rows: dict[int, int] = {}
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
        return ws, ledger

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

    def _insert_structure_row(self, ws, structure: dict, update: dict) -> int:
        rows = structure.get("rows", [])
        insert_at = max((row.get("row", 1) for row in rows), default=1) + 1
        labels = [update.get("section", ""), update.get("division", ""), update.get("item", ""), update.get("site", ""), update.get("unit", "")]
        if self.test_mode:
            LOG.info("DRY-RUN %s!A%d에 구조 행 생성 예정: %s", ws.title, insert_at, labels)
        else:
            self.quota.write(); ws.insert_row(labels, index=insert_at, value_input_option="USER_ENTERED")
        structure.setdefault("rows", []).append({"row": insert_at, "section": labels[0], "division": labels[1], "item": labels[2], "site": labels[3], "unit": labels[4]})
        return insert_at

    def apply_structured_updates(self, ws, updates: list[dict]) -> int:
        if not updates:
            return 0
        structure = self.sheet_structure(ws)
        applied = 0
        for update in updates:
            quarter = str(update.get("quarter") or "").strip()
            value = update.get("value")
            if not quarter or value in (None, ""):
                continue
            column = self._ensure_quarter_column(ws, quarter)
            row = self._match_structure_row(structure, update)
            if row is None:
                row = self._insert_structure_row(ws, structure, update)
            cell = f"{column_letter(column)}{row}"
            if self.test_mode:
                LOG.info("DRY-RUN %s!%s = %s", ws.title, cell, value)
            else:
                self.quota.write(); ws.update(cell, [[value]], value_input_option="USER_ENTERED")
            applied += 1
        return applied

    def append(self, ws, row):
        values = [row.get(header, "") for header in self.headers]
        row_index = self.next_rows.get(ws.id)
        if row_index is None:
            self._ensure_data_table(ws)
            row_index = self.next_rows[ws.id]
        if self.test_mode:
            LOG.info("DRY-RUN %s!A%d %s", ws.title, row_index, dict(zip(self.headers, values)))
        else:
            self.quota.write(); ws.update(f"A{row_index}", [values], value_input_option="USER_ENTERED")
        self.next_rows[ws.id] = row_index + 1

    def record_history(self, ledger, company: str, stock: str, corp: str, year: int, rcept: str, status: str, message: str, output_sheet: str):
        values = [datetime.now(KST).isoformat(), company, stock, corp, year, rcept, status, message[:500], output_sheet]
        if ledger is None or self.test_mode:
            LOG.info("DRY-RUN 장부 기록 %s", dict(zip(LEDGER_HEADERS, values)))
            return
        row_index = self.ledger_next_rows.get(ledger.id)
        if row_index is None:
            self._ensure_ledger(ledger)
            row_index = self.ledger_next_rows[ledger.id]
        self.quota.write(); ledger.update(f"A{row_index}", [values], value_input_option="USER_ENTERED")
        self.ledger_next_rows[ledger.id] = row_index + 1


def reports(dart, corp_code: str, years: list[int]):
    """Fetch spaced annual filings: each normally contains three fiscal years."""
    for year in years[::3]:
        for attempt in range(3):
            try:
                frame = dart.list(corp_code, start=f"{year}0101", end=f"{year+1}0630", kind="A")
                if frame is not None and len(frame): yield frame.iloc[0]
                break
            except Exception as exc:
                if attempt == 2: LOG.warning("⚠️ 보고서 조회 실패: %s/%s %s", corp_code, year, exc)
                else: time.sleep(2 ** attempt)
        time.sleep(.2)


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
    years_back = int(os.getenv("YEARS_BACK", "10")); now_year = datetime.now(KST).year
    llm_max_calls = env_int("LLM_MAX_CALLS", 120)
    structure_mode = os.getenv("LLM_STRUCTURE_MODE", "true").lower() in {"1","true","yes"}
    successes = llm_calls = 0
    try:
        import OpenDartReader
        dart = OpenDartReader(os.environ["opendart_api"])
        parser = UtilizationParser()
        total = sum(len(shards[n]) for n in selected)
        position = 0
        for shard in selected:
            for company in shards[shard]:
                position += 1; name = str(company.get(columns["기업명"], "")).strip()
                try:
                    corp, stock = resolve_company_codes(dart, company, columns)
                except Exception as exc:
                    LOG.warning("⚠️ 기업 코드 매핑 실패 %s: %s", name, exc)
                    continue
                LOG.info("[%d/%d] %s 처리 중…", position, total, name)
                ws, ledger = store.setup(shard, name, stock)
                for report in reports(dart, corp, list(range(now_year - years_back, now_year + 1))):
                    rcept = str(report.get("rcept_no", "")); year = int(report.get("bsns_year", now_year))
                    quarter = report_quarter_label(report, year)
                    try:
                        html = dart.document(rcept)
                        if structure_mode and os.getenv("OLLAMA_API_KEY") and llm_calls < llm_max_calls:
                            llm_calls += 1
                            structure = store.sheet_structure(ws)
                            structured = call_structure(html, structure, quarter)
                            applied = store.apply_structured_updates(ws, structured.updates)
                            if applied:
                                store.record_history(ledger, name, stock, corp, year, rcept, f"structured_{structured.status}", f"{applied} cells via {MODEL}", ws.title)
                                successes += 1
                                continue
                            LOG.warning("⚠️ LLM 구조화 매핑 없음 %s/%s: %s", name, rcept, structured.status)
                        rows = parser.parse(html, year)
                        if not rows and os.getenv("OLLAMA_API_KEY") and llm_calls < llm_max_calls:
                            llm_calls += 1; fallback = call_fallback(html)
                            rows = fallback.rows
                            for row in rows:
                                row["가동률(%)"] = row.pop("가동률", None)
                                row["원본표(JSON)"] = json.dumps({"source":"llm_fallback", "model":MODEL,
                                                                 "raw":fallback.raw, "html":str(html)[:6000]}, ensure_ascii=False)
                            if fallback.status != "success": LOG.warning("⚠️ %s: %s", name, fallback.status)
                        for row in rows:
                            row.update({"기업명":name,"종목코드":stock,"corp_code":corp,"사업연도":row.get("사업연도") or year,"보고서명":report.get("report_nm", ""),"접수번호(rcept_no)":rcept,"DART URL":f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}","수집일시":datetime.now(KST).isoformat()})
                            store.append(ws, row)
                        status = "success" if rows else "no_rows"
                        store.record_history(ledger, name, stock, corp, year, rcept, status, f"{len(rows)} rows", ws.title)
                        successes += bool(rows)
                    except Exception as exc:
                        LOG.warning("⚠️ 부분 실패 %s/%s: %s", name, rcept, exc)
                        store.record_history(ledger, name, stock, corp, year, rcept, "failure", str(exc), ws.title)
                        if env_bool("DEBUG_MODE"): Path("debug").mkdir(exist_ok=True); Path(f"debug/{corp}_{rcept}.html").write_text(str(locals().get("html", "")), encoding="utf-8")
        LOG.info("✅ 수집 완료: 성공 보고서=%d, LLM 호출=%d", successes, llm_calls)
        return 0 if successes or env_bool("TEST_MODE") else 1
    except Exception as exc:
        LOG.error("❌ OpenDART 완전 실패: %s", exc); return 1


if __name__ == "__main__": sys.exit(main())
