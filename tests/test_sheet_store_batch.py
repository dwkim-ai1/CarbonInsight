import sys
import types

sys.modules.setdefault("gspread", types.ModuleType("gspread"))

from utilization_collector import FRAMEWORK_HEADERS, SheetStore, reports, row_quarter_label, rows_to_structured_updates, selected_company_work, target_business_years


class FakeQuota:
    def read(self): pass
    def write(self): pass


class FakeWorksheet:
    def __init__(self, title="000001", worksheet_id=1, values=None):
        self.id = worksheet_id
        self.title = title
        self.values = values
        self.calls = []

    def row_values(self, row):
        if self.values is not None and row <= len(self.values):
            return self.values[row - 1]
        return ["", "", "", "", "", "1Q26"]

    def get_all_values(self):
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


def test_framework_json_roundtrip_uses_framework_sheet_cache():
    framework = FakeWorksheet("_framework", 9, [FRAMEWORK_HEADERS])
    store = SheetStore(None, ["dummy"], FakeQuota())
    selectors = [{"target_row": 2, "section": "가동률", "item": "MDF", "source_aliases": ["MDF"], "confidence": "high"}]

    row_index = store.save_framework(framework, "회사", "000001", "00123456", "000001", selectors)

    assert row_index == 2
    assert store.load_framework(framework, "000001") == selectors
    assert framework.calls[0][0] == "update"


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
                {"rcept_no": "3", "bsns_year": "2025", "report_nm": "분기보고서 (2025.09)", "rcept_dt": "20251114"},
                {"rcept_no": "1", "bsns_year": "2025", "report_nm": "분기보고서 (2025.03)", "rcept_dt": "20250515"},
                {"rcept_no": "2", "bsns_year": "2025", "report_nm": "반기보고서 (2025.06)", "rcept_dt": "20250814"},
                {"rcept_no": "4", "bsns_year": "2025", "report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260331"},
                {"rcept_no": "x", "bsns_year": "2024", "report_nm": "분기보고서 (2024.09)", "rcept_dt": "20241114"},
            ])

    result = list(reports(FakeDart(), "00123456", [2025]))

    assert [row["rcept_no"] for row in result] == ["1", "2", "3", "4"]
