import sys
import types

sys.modules.setdefault("gspread", types.ModuleType("gspread"))

from utilization_collector import SheetStore


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
