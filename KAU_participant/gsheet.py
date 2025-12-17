import json
import os
from datetime import datetime, timezone, timedelta
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def now_kst_str() -> str:
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).strftime("%Y-%m-%d %H:%M:%S")


def get_gspread_client():
    raw = os.environ["GOOGLE_SHEETS_CREDS"]
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return gspread.authorize(creds)


def upsert_worksheet(spreadsheet, title: str, rows: int = 1000, cols: int = 30):
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)


def overwrite_worksheet(worksheet, df):
    # 전체 덮어쓰기(헤더 포함)
    worksheet.clear()
    values = [list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist()
    worksheet.update(values, value_input_option="RAW")
