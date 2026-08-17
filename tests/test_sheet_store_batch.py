import sys
import types

sys.modules.setdefault("gspread", types.ModuleType("gspread"))

from utilization_collector import FRAMEWORK_HEADERS, SheetStore, llm_utilization_context, merge_framework_selectors, reports, resolve_company_codes, row_quarter_label, rows_to_structured_updates, selected_company_work, selectors_from_sheet_structure, target_business_years
from utilization_parser import UtilizationParser


class FakeQuota:
    def read(self): pass
    def write(self): pass


class FakeWorksheet:
    def __init__(self, title="000001", worksheet_id=1, values=None, row_count=200, col_count=100):
        self.id = worksheet_id
        self.title = title
        self.values = values
        self.row_count = row_count
        self.col_count = col_count
        self.calls = []
        self.get_all_values_called = False

    def row_values(self, row):
        if self.values is not None and row <= len(self.values):
            return self.values[row - 1]
        return ["", "", "", "", "", "1Q26"]

    def get(self, range_name):
        values = self.values if self.values is not None else self.get_all_values()
        if range_name == "A2:E":
            return [list(row[:5]) for row in values[1:]]
        return values

    def get_all_values(self):
        self.get_all_values_called = True
        if self.values is not None:
            return self.values
        return [["", "", "", "", "", "1Q26"], ["가동률", "", "MDF", "", "(%)"]]

    def insert_row(self, *args, **kwargs):
        self.calls.append(("insert_row", args, kwargs))

    def insert_rows(self, *args, **kwargs):
        self.calls.append(("insert_rows", args, kwargs))

    def batch_update(self, *args, **kwargs):
        self.calls.append(("batch_update", args, kwargs))

    def update(self, *args, **kwargs):
        self.calls.append(("update", args, kwargs))

    def resize(self, **kwargs):
        self.calls.append(("resize", (), kwargs))
        self.row_count = kwargs.get("rows", self.row_count)
        self.col_count = kwargs.get("cols", self.col_count)


def test_structured_updates_use_single_batch_update_for_cells():
    ws = FakeWorksheet()
    store = SheetStore(None, ["dummy"], FakeQuota())
    applied = store.apply_structured_updates(ws, [
        {"section": "가동률", "item": "MDF", "unit": "(%)", "quarter": "1Q26", "value": "87"},
        {"section": "가동률", "item": "PB", "unit": "(%)", "quarter": "1Q26", "value": "91"},
    ])

    assert applied == 2
    assert [call[0] for call in ws.calls] == ["insert_row", "batch_update"]
    batch = ws.calls[1][1][0]
    assert batch == [{"range": "F2", "values": [["87"]]}, {"range": "F3", "values": [["91"]]}]


def test_section_only_update_inserts_data_row_not_header_row():
    ws = FakeWorksheet()
    store = SheetStore(None, ["dummy"], FakeQuota())
    store.apply_structured_updates(ws, [
        {"section": "가동률", "quarter": "1Q26", "value": "87", "source_label": "평균가동률"},
    ])

    assert ws.calls[0][0] == "insert_row"
    assert ws.calls[0][1][0] == ["가동률", "", "평균가동률", "", ""]


def test_structured_updates_can_block_new_rows_after_framework():
    ws = FakeWorksheet()
    store = SheetStore(None, ["dummy"], FakeQuota())
    applied = store.apply_structured_updates(ws, [
        {"section": "가동률", "item": "PB", "unit": "(%)", "quarter": "1Q26", "value": "91"},
    ], allow_new_rows=False)

    assert applied == 0
    assert ws.calls == []


def test_history_write_expands_grid_before_update():
    ws = FakeWorksheet("_처리이력", worksheet_id=2, values=[["timestamp","기업명","종목코드","corp_code","사업연도","접수번호","상태","메시지","출력시트"]], row_count=1, col_count=6)
    store = SheetStore(None, ["dummy"], FakeQuota())
    store._ensure_ledger(ws)

    store.record_history_rows(ws, [["t", "회사", "000001", "00123456", 2026, "r", "ok", "message", "000001"]])

    assert ws.calls[-2][0] == "resize"
    assert ws.calls[-2][2] == {"rows": 2, "cols": 9}
    assert ws.calls[-1][0] == "update"


def test_framework_json_roundtrip_uses_framework_sheet_cache():
    framework = FakeWorksheet("_framework", 9, [FRAMEWORK_HEADERS])
    store = SheetStore(None, ["dummy"], FakeQuota())
    selectors = [{"target_row": 2, "section": "가동률", "item": "MDF", "source_aliases": ["MDF"], "confidence": "high"}]

    row_index = store.save_framework(framework, "회사", "000001", "00123456", "000001", selectors)

    assert row_index == 2
    assert store.load_framework(framework, "000001") == selectors
    assert framework.calls[0][0] == "update"


def test_selectors_from_sheet_structure_uses_existing_sheet_rows():
    structure = {"rows": [
        {"row": 2, "section": "가동률", "division": "판지", "item": "평균가동률", "site": "", "unit": "%"},
    ]}

    selectors = selectors_from_sheet_structure(structure)

    assert selectors == [{
        "target_row": 2,
        "section": "가동률",
        "division": "판지",
        "item": "평균가동률",
        "site": "",
        "unit": "%",
        "source_aliases": ["평균가동률", "판지", "가동률"],
        "confidence": "medium",
    }]


def test_sheet_structure_reads_only_header_and_label_columns_without_row_cap():
    values = [["", "", "", "", "", "1Q26", "2Q26", ""]]
    for index in range(170):
        values.append(["가동률", "", f"품목{index}", "", "%", "87", "91", "ignored"])
    ws = FakeWorksheet(values=values)
    store = SheetStore(None, ["dummy"], FakeQuota())

    structure = store.sheet_structure(ws)

    assert not ws.get_all_values_called
    assert [quarter["label"] for quarter in structure["quarters"]] == ["1Q26", "2Q26"]
    assert len(structure["rows"]) == 170
    assert structure["rows"][-1]["item"] == "품목169"


def test_merge_framework_selectors_tracks_aliases_and_seen_periods():
    existing = [{
        "target_row": 2,
        "section": "한국남동발전(주)",
        "division": "발전/전기",
        "item": "생산능력",
        "site": "삼천포",
        "unit": "MW",
        "source_aliases": ["삼천포"],
        "first_seen_quarter": "2Q26",
        "confidence": "medium",
    }]
    sheet_selectors = [{
        "target_row": 2,
        "section": "한국남동발전(주)",
        "division": "발전/전기",
        "item": "생산능력",
        "site": "삼천포",
        "unit": "MW",
        "source_aliases": ["생산능력"],
        "confidence": "medium",
    }]
    updates = [{
        "section": "한국남동발전",
        "division": "발전/전기",
        "item": "생산능력",
        "site": "삼천포발전소",
        "unit": "MW",
        "quarter": "4Q24",
        "value": "2,120",
        "source_label": "삼천포발전소",
        "confidence": "high",
    }]

    merged = merge_framework_selectors(existing, sheet_selectors, updates, "20260515000001")

    assert len(merged) == 1
    assert merged[0]["first_seen_quarter"] == "4Q24"
    assert merged[0]["last_seen_quarter"] == "2Q26"
    assert "삼천포발전소" in merged[0]["source_aliases"]
    assert merged[0]["source_rcept_nos"] == ["20260515000001"]


def test_llm_utilization_context_keeps_relevant_tables_not_document_prefix():
    html = "<html><body>" + ("<p>무관한 앞부분</p>" * 300) + """
    <table><tr><td>구분</td><td>기타</td></tr><tr><td>A</td><td>B</td></tr></table>
    <p>1. 한국남동발전(주)</p><p>가. 생산능력</p>
    <table><tr><td>구분</td><td>생산능력</td><td>생산실적</td><td>가동률</td></tr>
    <tr><td rowspan="2">전력</td><td>100</td><td>87</td><td>87%</td></tr></table>
    </body></html>"""

    context = llm_utilization_context(UtilizationParser(), html, max_chars=1000)

    assert "한국남동발전" in context
    assert "rowspan" in context
    assert "가동률" in context
    assert "87%" in context
    assert "무관한 앞부분" not in context


def test_row_period_maps_to_matching_quarter_not_report_quarter_only():
    assert row_quarter_label({"사업연도": "당기"}, "1Q26") == "1Q26"
    assert row_quarter_label({"사업연도": "전기"}, "1Q26") == "1Q25"
    assert row_quarter_label({"사업연도": "2024년 3분기"}, "1Q26") == "3Q24"


def test_target_business_years_is_exact_count_including_current_year():
    assert target_business_years(2026, 10) == list(range(2017, 2027))
    assert target_business_years(2026, 1) == [2026]


def test_selected_company_work_supports_offset_and_limit():
    shards = {1: [{"name": "a"}, {"name": "b"}], 2: [{"name": "c"}]}

    work = selected_company_work(shards, [1, 2], offset=1, limit=1)

    assert work == [(1, {"name": "b"})]


def test_resolve_company_codes_uses_stock_resolver_before_dart_lookup():
    class FakeResolver:
        def find(self, stock, name):
            assert stock == "015760"
            assert name == "한국전력공사"
            return "00159193"

    class FakeDart:
        def find_corp_code(self, lookup):
            raise AssertionError("resolver should avoid OpenDartReader lookup")

    record = {"회사명": "한국전력공사", "상장코드": "015760"}
    columns = {"기업명": "회사명", "종목코드": "상장코드"}

    assert resolve_company_codes(FakeDart(), record, columns, FakeResolver()) == ("00159193", "015760")


def test_selector_required_skips_unmatched_parser_rows():
    rows = [{"품목": "PB", "가동률(%)": "91.2", "사업연도": "전기"}]
    selectors = [{"target_row": 2, "section": "가동률", "item": "MDF"}]
    assert rows_to_structured_updates(rows, "1Q26", selectors, require_selector_match=True) == []


def test_selector_required_keeps_matched_rows_and_period():
    rows = [{"품목": "MDF", "가동률(%)": "91.2", "사업연도": "전기"}]
    selectors = [{"target_row": 2, "section": "가동률", "item": "MDF"}]
    updates = rows_to_structured_updates(rows, "1Q26", selectors, require_selector_match=True)
    assert updates[0]["target_row"] == 2
    assert updates[0]["quarter"] == "1Q25"


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def iterrows(self):
        return enumerate(self.rows)


def test_reports_collects_all_periodic_filings_in_requested_years():
    class FakeDart:
        def list(self, corp_code, start, end, kind):
            assert kind == "A"
            return FakeFrame([
                {"rcept_no": "3", "report_nm": "분기보고서 (2025.09)", "rcept_dt": "20251114"},
                {"rcept_no": "1", "report_nm": "분기보고서 (2025.03)", "rcept_dt": "20250515"},
                {"rcept_no": "2", "report_nm": "반기보고서 (2025.06)", "rcept_dt": "20250814"},
                {"rcept_no": "4", "report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260331"},
                {"rcept_no": "x", "report_nm": "분기보고서 (2024.09)", "rcept_dt": "20241114"},
            ])

    result = list(reports(FakeDart(), "00123456", [2025]))

    assert [row["rcept_no"] for row in result] == ["1", "2", "3", "4"]
