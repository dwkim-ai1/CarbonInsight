import json
import os
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import pandas as pd
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


def overwrite_worksheet(worksheet, df: pd.DataFrame):
    worksheet.clear()
    values = [list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist()
    worksheet.update(values, value_input_option="RAW")


def read_worksheet_df(worksheet) -> pd.DataFrame:
    """
    시트를 DataFrame으로 읽음 (헤더 1행 가정)
    - 빈 시트면 빈 DF 반환
    """
    values = worksheet.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()
    header = values[0]
    rows = values[1:]
    # 길이 맞추기
    fixed = []
    for r in rows:
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        fixed.append(r[: len(header)])
    return pd.DataFrame(fixed, columns=header)


def _normalize_for_hash(df: pd.DataFrame, ignore_cols: Optional[List[str]] = None) -> pd.DataFrame:
    """
    변경 감지를 위한 정규화:
    - ignore_cols 제거
    - 컬럼 정렬
    - 값은 문자열로 통일, NaN/None -> ""
    - 행 순서 영향 제거를 위해 전체 행을 문자열로 만들어 정렬
    """
    if df is None or df.empty:
        return pd.DataFrame()

    tmp = df.copy()

    ignore_cols = ignore_cols or []
    for c in ignore_cols:
        if c in tmp.columns:
            tmp = tmp.drop(columns=[c])

    # 컬럼 정렬
    tmp = tmp.reindex(sorted(tmp.columns), axis=1)

    # 값 정규화
    tmp = tmp.astype(object).where(tmp.notna(), "")
    tmp = tmp.applymap(lambda x: str(x).strip())

    # 행 정렬(행 순서 변경을 변경으로 보지 않기 위해)
    row_strings = tmp.apply(lambda r: "\u241F".join(r.values.tolist()), axis=1)
    tmp = tmp.assign(__rowkey=row_strings).sort_values("__rowkey").drop(columns=["__rowkey"]).reset_index(drop=True)

    return tmp


def df_fingerprint(df: pd.DataFrame, ignore_cols: Optional[List[str]] = None) -> str:
    norm = _normalize_for_hash(df, ignore_cols=ignore_cols)
    if norm.empty:
        return "EMPTY"
    csv = norm.to_csv(index=False)
    return hashlib.sha256(csv.encode("utf-8")).hexdigest()


def append_snapshot(worksheet, df: pd.DataFrame):
    """
    히스토리 시트에 누적 append.
    - 시트가 비어 있으면 헤더 포함으로 시작
    - 있으면 기존 헤더 기준으로 컬럼 맞춰 append (없는 컬럼은 빈 값)
    """
    if df is None or df.empty:
        return

    existing = worksheet.get_all_values()
    if not existing:
        # 새 시트: 헤더 + 전체
        values = [list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist()
        worksheet.update(values, value_input_option="RAW")
        return

    header = existing[0]
    tmp = df.copy()
    # 헤더에 맞춰 컬럼 보정
    for c in header:
        if c not in tmp.columns:
            tmp[c] = ""
    tmp = tmp[header]  # 헤더 순서로 맞춤

    values = tmp.astype(object).where(tmp.notna(), "").values.tolist()
    worksheet.append_rows(values, value_input_option="RAW")
