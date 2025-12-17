import os
from pathlib import Path

import pandas as pd

from KAU_participant.gsheet import get_gspread_client, now_kst_str, upsert_worksheet, overwrite_worksheet
from KAU_participant.ngms_collect import (
    collect_allocated,
    collect_target_mgmt,
    collect_statement_stats_latest_year,
)


SHEET_ALLOC = "NGMS_할당대상업체"
SHEET_TARGET = "NGMS_목표관리대상업체"
SHEET_STATS = "NGMS_명세서배출량통계"


def _attach_meta(df: pd.DataFrame, collected_at: str, source_hint: str) -> pd.DataFrame:
    out = df.copy()
    out["수집일시"] = collected_at
    out["원본업데이트표시"] = source_hint or ""
    return out


def main():
    sheet_id = os.environ["KAU_SHEET_ID"]
    download_dir = Path(os.environ.get("NGMS_DOWNLOAD_DIR", "tmp_downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    collected_at = now_kst_str()

    alloc_df, alloc_hint = collect_allocated(download_dir)
    target_df, target_hint = collect_target_mgmt(download_dir)
    stats_df, stats_hint = collect_statement_stats_latest_year(download_dir)

    alloc_df = _attach_meta(alloc_df, collected_at, alloc_hint)
    target_df = _attach_meta(target_df, collected_at, target_hint)
    stats_df = _attach_meta(stats_df, collected_at, stats_hint)

    gc = get_gspread_client()
    ss = gc.open_by_key(sheet_id)

    ws1 = upsert_worksheet(ss, SHEET_ALLOC, rows=max(1000, len(alloc_df) + 10), cols=max(30, len(alloc_df.columns) + 5))
    ws2 = upsert_worksheet(ss, SHEET_TARGET, rows=max(1000, len(target_df) + 10), cols=max(30, len(target_df.columns) + 5))
    ws3 = upsert_worksheet(ss, SHEET_STATS, rows=max(1000, len(stats_df) + 10), cols=max(30, len(stats_df.columns) + 5))

    overwrite_worksheet(ws1, alloc_df)
    overwrite_worksheet(ws2, target_df)
    overwrite_worksheet(ws3, stats_df)

    print("NGMS 업로드 완료")
    print(f"- {SHEET_ALLOC}: {len(alloc_df)} rows")
    print(f"- {SHEET_TARGET}: {len(target_df)} rows")
    print(f"- {SHEET_STATS}: {len(stats_df)} rows")


if __name__ == "__main__":
    main()
