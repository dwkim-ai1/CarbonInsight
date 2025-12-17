import os
from pathlib import Path

import pandas as pd

from KAU_participant.gsheet import (
    get_gspread_client,
    now_kst_str,
    upsert_worksheet,
    overwrite_worksheet,
    read_worksheet_df,
    df_fingerprint,
    append_snapshot,
)
from KAU_participant.ngms_collect import (
    collect_allocated,
    collect_target_mgmt,
    collect_statement_stats_latest_year,
)


# 월간 “현재값” 시트 (덮어쓰기)
SHEET_ALLOC_CUR = "NGMS_할당대상업체"
SHEET_TARGET_CUR = "NGMS_목표관리대상업체"
SHEET_STATS_CUR = "NGMS_명세서배출량통계"

# 변경 발생 시 “스냅샷 누적” 시트
SHEET_ALLOC_HIS = "할당대상업체"
SHEET_TARGET_HIS = "목표관리대상업체"
SHEET_STATS_HIS = "명세서배출량통계"


def _attach_meta(df: pd.DataFrame, collected_at: str, source_hint: str) -> pd.DataFrame:
    out = df.copy()
    # 히스토리/현재 시트 모두 동일한 메타 컬럼 유지(원하시면 히스토리에만 붙이도록도 가능)
    out["수집일시"] = collected_at
    out["원본업데이트표시"] = source_hint or ""
    return out


def _update_with_history(ss, cur_title: str, his_title: str, new_df: pd.DataFrame):
    """
    1) 현재(cur) 시트의 직전 데이터와 new_df 비교
    2) 다르면 히스토리(his) 시트에 new_df 스냅샷 append
    3) 현재(cur) 시트는 항상 new_df로 overwrite
    """
    ws_cur = upsert_worksheet(ss, cur_title, rows=max(1000, len(new_df) + 10), cols=max(30, len(new_df.columns) + 5))
    ws_his = upsert_worksheet(ss, his_title, rows=2000, cols=max(30, len(new_df.columns) + 5))

    old_df = read_worksheet_df(ws_cur)

    # 메타 컬럼은 비교에서 제외(매월 수집일시가 바뀌므로 항상 변경으로 뜨는 것 방지)
    ignore_cols = ["수집일시", "원본업데이트표시"]
    old_fp = df_fingerprint(old_df, ignore_cols=ignore_cols)
    new_fp = df_fingerprint(new_df, ignore_cols=ignore_cols)

    changed = (old_fp != new_fp)

    if changed and not new_df.empty:
        append_snapshot(ws_his, new_df)

    overwrite_worksheet(ws_cur, new_df)

    return changed, old_fp, new_fp


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

    c1, _, _ = _update_with_history(ss, SHEET_ALLOC_CUR, SHEET_ALLOC_HIS, alloc_df)
    c2, _, _ = _update_with_history(ss, SHEET_TARGET_CUR, SHEET_TARGET_HIS, target_df)
    c3, _, _ = _update_with_history(ss, SHEET_STATS_CUR, SHEET_STATS_HIS, stats_df)

    print("NGMS 업데이트 완료")
    print(f"- {SHEET_ALLOC_CUR} overwrite, changed={c1} (changed이면 '{SHEET_ALLOC_HIS}'에 누적)")
    print(f"- {SHEET_TARGET_CUR} overwrite, changed={c2} (changed이면 '{SHEET_TARGET_HIS}'에 누적)")
    print(f"- {SHEET_STATS_CUR} overwrite, changed={c3} (changed이면 '{SHEET_STATS_HIS}'에 누적)")


if __name__ == "__main__":
    main()
