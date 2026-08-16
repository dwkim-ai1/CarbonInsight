"""Print the runtime schema inferred from destination spreadsheet 9."""
import json, os
import gspread


def sheet_id_9() -> str:
    value = os.getenv("gspread_ids", "").strip()
    if value:
        ids = json.loads(value)
        if not isinstance(ids, list) or len(ids) != 10:
            raise ValueError("gspread_ids must be a JSON array of 10 Google Sheet IDs")
        return str(ids[8]).strip()
    return os.getenv("gspread_id_9", "1Q4rsFXyZU_ltrXVpc91z-TWLHvsWJSDK1rxQRkXaKfQ")


creds = json.loads(os.environ["GOOGLE_SHEETS_CREDS"])
book = gspread.service_account_from_dict(creds).open_by_key(sheet_id_9())
sheet = book.get_worksheet(0)
headers = sheet.row_values(1)
mode = "B" if len(book.worksheets()) == 1 and "기업명" in headers else "A"
print(f"mode={mode}")
print("headers=" + json.dumps(headers, ensure_ascii=False))
