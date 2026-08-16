"""Validate and print company-to-shard mapping without collecting DART data."""
import json, os
import gspread
from utilization_collector import detect_columns, distribute

gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SHEETS_CREDS"]))
ws = gc.open_by_key(os.environ["gspread_list"]).get_worksheet(0)
columns = detect_columns(ws.row_values(1)); shards, overflow = distribute(ws.get_all_records(), columns)
for shard, rows in shards.items(): print(shard, [r[columns["기업명"]] for r in rows])
print("overflow", [r[columns["기업명"]] for r in overflow])
