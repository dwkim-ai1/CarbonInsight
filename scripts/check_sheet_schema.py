"""Print the runtime schema inferred from destination spreadsheet 9."""
import json, os
import gspread

creds = json.loads(os.environ["GOOGLE_SHEETS_CREDS"])
sheet_id = os.getenv("gspread_id_9", "1Q4rsFXyZU_ltrXVpc91z-TWLHvsWJSDK1rxQRkXaKfQ")
book = gspread.service_account_from_dict(creds).open_by_key(sheet_id)
sheet = book.get_worksheet(0)
headers = sheet.row_values(1)
mode = "B" if len(book.worksheets()) == 1 and "기업명" in headers else "A"
print(f"mode={mode}")
print("headers=" + json.dumps(headers, ensure_ascii=False))
