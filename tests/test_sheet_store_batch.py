import sys
import types

sys.modules.setdefault("gspread", types.ModuleType("gspread"))

from utilization_collector import SheetStore, row_quarter_label, rows_to_structured_updates


class FakeQuota:
    def read(self): pass
    def write(self): pass


class FakeWorksheet:
    id = 1
    title = "000001"

    def __init__(self):
        self.calls = []

    def row_values(self, row):
        return ["", "", "", "", "", "1Q26"]

    def get_all_values(self):
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


def test_row_period_maps_to_matching_quarter_not_report_quarter_only():
    assert row_quarter_label({"사업연도": "당기"}, "1Q26") == "1Q26"
    assert row_quarter_label({"사업연도": "전기"}, "1Q26") == "1Q25"
    assert row_quarter_label({"사업연도": "2024년 3분기"}, "1Q26") == "3Q24"


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
