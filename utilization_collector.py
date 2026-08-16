"""Ten-year OpenDART utilization collection orchestrator."""
from __future__ import annotations

import json
import logging
import os
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
ALIASES = {"기업명": ("기업명","회사명","법인명"), "종목코드": ("종목코드","stock_code","종목번호"), "corp_code": ("corp_code","고유번호","법인코드"), "bucket": ("그룹","묶음","bucket")}


def env_bool(name: str) -> bool: return os.getenv(name, "false").lower() in {"1","true","yes"}


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
    for canonical, candidates in ALIASES.items():
        match = next((h for h in headers if h.strip().lower() in {c.lower() for c in candidates}), None)
        if match: found[canonical] = match
    missing = {"기업명","종목코드","corp_code"} - found.keys()
    if missing: raise ValueError(f"기업 목록 헤더 매핑 실패: {sorted(missing)}; 실제 헤더={headers}")
    return found


def distribute(records: list[dict], columns: dict[str, str]) -> tuple[dict[int, list[dict]], list[dict]]:
    shards = {i: [] for i in range(1, 11)}
    overflow = []
    for index, record in enumerate(records):
        raw = record.get(columns.get("bucket", ""), "")
        try: shard = int(raw) if raw != "" else index // 20 + 1
        except ValueError: shard = index // 20 + 1
        (shards[shard] if shard in shards else overflow).append(record)
    return shards, overflow


class SheetStore:
    def __init__(self, gc, sheet_ids: list[str], test_mode=False):
        self.gc, self.ids, self.test_mode = gc, sheet_ids, test_mode
        standard = gc.open_by_key(sheet_ids[8])
        first = standard.get_worksheet(0)
        existing = first.row_values(1)
        self.headers = existing + [h for h in REQUIRED if h not in existing]
        self.mode = "B" if len(standard.worksheets()) == 1 and "기업명" in self.headers else "A"
        LOG.info("✅ 표준 스키마 감지: 방식 %s, 헤더=%s", self.mode, self.headers)

    def setup(self, shard: int, company: str):
        book = self.gc.open_by_key(self.ids[shard - 1])
        title = "가동률" if self.mode == "B" else company[:90]
        try: ws = book.worksheet(title)
        except gspread.WorksheetNotFound: ws = book.add_worksheet(title, rows=1000, cols=max(20, len(self.headers)))
        if ws.row_values(1) != self.headers and not self.test_mode: ws.update("A1", [self.headers])
        try: history = book.worksheet("_처리이력")
        except gspread.WorksheetNotFound:
            history = book.add_worksheet("_처리이력", rows=2000, cols=6)
            if not self.test_mode: history.update("A1", [["timestamp","기업명","사업연도","접수번호","상태","메시지"]])
        return ws, history

    def append(self, ws, row):
        values = [row.get(header, "") for header in self.headers]
        if self.test_mode: LOG.info("DRY-RUN %s", dict(zip(self.headers, values)))
        else: ws.append_row(values, value_input_option="USER_ENTERED")


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
    try:
        gc = credentials()
        list_book = gc.open_by_key(os.environ["gspread_list"])
        records = list_book.get_worksheet(0).get_all_records()
        headers = list_book.get_worksheet(0).row_values(1)
        columns = detect_columns(headers)
        shards, overflow = distribute(records, columns)
        for number, companies in shards.items():
            LOG.info("📊 shard %d: %s", number, ", ".join(str(c.get(columns["기업명"], "")) for c in companies))
        ids = load_sheet_ids()
        store = SheetStore(gc, ids, env_bool("TEST_MODE"))
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
                position += 1; name = str(company[columns["기업명"]]); corp = str(company[columns["corp_code"]]).zfill(8)
                LOG.info("[%d/%d] %s 처리 중…", position, total, name)
                ws, history = store.setup(shard, name)
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
                            row.update({"기업명":name,"종목코드":company[columns["종목코드"]],"corp_code":corp,"보고서명":report.get("report_nm", ""),"접수번호(rcept_no)":rcept,"DART URL":f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}","수집일시":datetime.now(KST).isoformat()})
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
