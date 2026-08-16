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

from utilization_llm_fallback import MODEL, call_fallback
from utilization_parser import UtilizationParser

LOG = logging.getLogger("utilization")
KST = timezone(timedelta(hours=9))
REQUIRED = ["기업명","종목코드","corp_code","사업연도","사업부문","품목","사업소","생산능력","생산실적","가동률(%)","단위","산출식","보고서명","접수번호(rcept_no)","DART URL","수집일시","원본표(JSON)"]
ALIASES = {
    "기업명": ("기업명","회사명","법인명","corp_name","company"),
    "종목코드": ("종목코드","상장코드","stock_code","stock code","ticker","티커","종목번호"),
    "corp_code": ("corp_code","고유번호","법인코드","dart_corp_code","DART고유번호"),
    "bucket": ("그룹","묶음","bucket","shard"),
    "sheet_id": ("Gspread_ID","gspread_id","gspread id","sheet_id","spreadsheet_id"),
}
INVALID_SHEET_TITLE_CHARS = re.compile(r"[\\/?*\[\]:]")
HISTORY_SHEET_TITLE = "_처리이력"
LEGACY_OUTPUT_SHEET_TITLE = "가동률"


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
        self.next_rows: dict[int, int] = {}
        LOG.info("✅ 출력 스키마 준비: 헤더=%s", self.headers)

    def _find_worksheet(self, worksheets, title: str):
        return next((ws for ws in worksheets if ws.title == title), None)

    def _template_worksheet(self, worksheets):
        for ws in worksheets:
            title = ws.title.strip()
            if title.startswith("_") or title == LEGACY_OUTPUT_SHEET_TITLE:
                continue
            if re.fullmatch(r"\d{6}", title):
                return ws
        for ws in worksheets:
            title = ws.title.strip()
            if title and not title.startswith("_") and title != LEGACY_OUTPUT_SHEET_TITLE:
                return ws
        return None

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

    def setup(self, shard: int, company: str, stock: str):
        self.quota.read(); book = self.gc.open_by_key(self.ids[shard - 1])
        title = safe_sheet_title(company, stock)
        self.quota.read(); worksheets = book.worksheets()
        ws = self._find_worksheet(worksheets, title)
        if ws is None:
            template = self._template_worksheet(worksheets)
            if self.test_mode:
                ws = template or worksheets[0]
                LOG.info("DRY-RUN 회사별 시트 생성 예정: %s", title)
            elif template is not None:
                self.quota.write(); book.batch_update({"requests":[{"duplicateSheet":{"sourceSheetId":template.id,"insertSheetIndex":len(worksheets),"newSheetName":title}}]})
                self.quota.read(); ws = book.worksheet(title)
                LOG.info("📄 회사별 시트 생성: %s -> %s", template.title, title)
            else:
                self.quota.write(); ws = book.add_worksheet(title, rows=1000, cols=max(20, len(self.headers)))
                LOG.info("📄 회사별 빈 시트 생성: %s", title)
        self._ensure_data_table(ws)
        try:
            self.quota.read(); history = book.worksheet(HISTORY_SHEET_TITLE)
        except gspread.WorksheetNotFound:
            if self.test_mode:
                history = None
                LOG.info("DRY-RUN %s 시트 생성 예정", HISTORY_SHEET_TITLE)
            else:
                self.quota.write(); history = book.add_worksheet(HISTORY_SHEET_TITLE, rows=2000, cols=6)
                self.quota.write(); history.update("A1", [["timestamp","기업명","사업연도","접수번호","상태","메시지"]])
        return ws, history

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
                ws, history = store.setup(shard, name, stock)
                for report in reports(dart, corp, list(range(now_year - years_back, now_year + 1))):
                    rcept = str(report.get("rcept_no", "")); year = int(report.get("bsns_year", now_year))
                    try:
                        html = dart.document(rcept)
                        rows = parser.parse(html, year)
                        if not rows and os.getenv("OLLAMA_API_KEY") and llm_calls < 5 * position:
                            llm_calls += 1; fallback = call_fallback(html)
                            rows = fallback.rows
                            for row in rows:
                                row["가동률(%)"] = row.pop("가동률", None)
                                row["원본표(JSON)"] = json.dumps({"source":"llm_fallback", "model":MODEL,
                                                                 "raw":fallback.raw, "html":str(html)[:6000]}, ensure_ascii=False)
                            if fallback.status != "success": LOG.warning("⚠️ %s: %s", name, fallback.status)
                        for row in rows:
                            row.update({"기업명":name,"종목코드":stock,"corp_code":corp,"보고서명":report.get("report_nm", ""),"접수번호(rcept_no)":rcept,"DART URL":f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}","수집일시":datetime.now(KST).isoformat()})
                            store.append(ws, row)
                        successes += bool(rows)
                    except Exception as exc:
                        LOG.warning("⚠️ 부분 실패 %s/%s: %s", name, rcept, exc)
                        if env_bool("DEBUG_MODE"): Path("debug").mkdir(exist_ok=True); Path(f"debug/{corp}_{rcept}.html").write_text(str(locals().get("html", "")), encoding="utf-8")
        LOG.info("✅ 수집 완료: 성공 보고서=%d, LLM 호출=%d", successes, llm_calls)
        return 0 if successes or env_bool("TEST_MODE") else 1
    except Exception as exc:
        LOG.error("❌ OpenDART 완전 실패: %s", exc); return 1


if __name__ == "__main__": sys.exit(main())
