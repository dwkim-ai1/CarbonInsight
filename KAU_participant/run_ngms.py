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
    collect_statement_stats,
)

# 월간 “현재값” 시트 (덮어쓰기)
SHEET_ALLOC_CUR = "NGMS_할당대상업체"
SHEET_TARGET_CUR = "NGMS_목표관리대상업체"
SHEET_STATS_CUR = "NGMS_명세서배출량통계"

# 변경 발생 시 “스냅샷 누적” 시트 (append)
SHEET_ALLOC_HIS = "할당대상업체"
SHEET_TARGET_HIS = "목표관리대상업체"
SHEET_STATS_HIS = "명세서배출량통계"


def _attach_meta(df: pd.DataFrame, collected_at: str, source_hint: str) -> pd.DataFrame:
    out = df.copy()
    out["수집일시"] = collected_at
    out["원본업데이트표시"] = source_hint or ""
    return out


def _update_with_history(ss, cur_title: str, his_title: str, new_df: pd.DataFrame):
    """
    월간 모드:
      1) NGMS_* 직전 데이터 vs 이번 데이터 비교(메타 제외)
      2) 다르면 히스토리 시트에 append
      3) NGMS_*는 항상 overwrite
    """
    ws_cur = upsert_worksheet(ss, cur_title, rows=max(1000, len(new_df) + 10), cols=max(30, len(new_df.columns) + 5))
    ws_his = upsert_worksheet(ss, his_title, rows=2000, cols=max(30, len(new_df.columns) + 5))

    old_df = read_worksheet_df(ws_cur)

    ignore_cols = ["수집일시", "원본업데이트표시"]
    old_fp = df_fingerprint(old_df, ignore_cols=ignore_cols)
    new_fp = df_fingerprint(new_df, ignore_cols=ignore_cols)

    changed = (old_fp != new_fp)

    if changed and not new_df.empty:
        append_snapshot(ws_his, new_df)

    overwrite_worksheet(ws_cur, new_df)

    return changed


def _append_only(ss, his_title: str, df: pd.DataFrame):
    """
    수동 모드:
      - NGMS_*는 업데이트 안 함
      - 히스토리 시트에만 append
    """
    ws_his = upsert_worksheet(ss, his_title, rows=2000, cols=max(30, len(df.columns) + 5))
    append_snapshot(ws_his, df)


def main():
    sheet_id = os.environ["KAU_SHEET_ID"]
    download_dir = Path(os.environ.get("NGMS_DOWNLOAD_DIR", "tmp_downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    # workflow_dispatch inputs
    plan_period = (os.environ.get("PLAN_PERIOD") or "").strip()
    designation_year = (os.environ.get("DESIGNATION_YEAR") or "").strip()
    emission_year = (os.environ.get("EMISSION_YEAR") or "").strip()
    manual_append_only = (os.environ.get("MANUAL_APPEND_ONLY") or "false").strip().lower() == "true"

    collected_at = now_kst_str()

    alloc_df, alloc_hint = collect_allocated(download_dir, plan_period=plan_period, designation_year=designation_year)
    target_df, target_hint = collect_target_mgmt(download_dir, designation_year=designation_year)
    stats_df, stats_hint = collect_statement_stats(download_dir, emission_year=emission_year)

    alloc_df = _attach_meta(alloc_df, collected_at, alloc_hint)
    target_df = _attach_meta(target_df, collected_at, target_hint)
    stats_df = _attach_meta(stats_df, collected_at, stats_hint)

    gc = get_gspread_client()
    ss = gc.open_by_key(sheet_id)

    if manual_append_only:
        # ✅ 수동: 과거/특정 조건 수집 결과를 히스토리에만 누적
        if not alloc_df.empty:
            _append_only(ss, SHEET_ALLOC_HIS, alloc_df)
        if not target_df.empty:
            _append_only(ss, SHEET_TARGET_HIS, target_df)
        if not stats_df.empty:
            _append_only(ss, SHEET_STATS_HIS, stats_df)

        print("수동(append-only) 완료: NGMS_*는 건드리지 않고 히스토리에만 누적했습니다.")
        print(f"- plan_period={plan_period or '(default)'}")
        print(f"- designation_year={designation_year or '(default)'}")
        print(f"- emission_year={emission_year or '(latest row)'}")
        return

    # ✅ 월간: NGMS_* 덮어쓰기 + 변경 시 히스토리 누적
    c1 = _update_with_history(ss, SHEET_ALLOC_CUR, SHEET_ALLOC_HIS, alloc_df)
    c2 = _update_with_history(ss, SHEET_TARGET_CUR, SHEET_TARGET_HIS, target_df)
    c3 = _update_with_history(ss, SHEET_STATS_CUR, SHEET_STATS_HIS, stats_df)

    print("월간 업데이트 완료 (NGMS_* overwrite, changed이면 히스토리에 누적)")
    print(f"- {SHEET_ALLOC_CUR} changed={c1}")
    print(f"- {SHEET_TARGET_CUR} changed={c2}")
    print(f"- {SHEET_STATS_CUR} changed={c3}")


if __name__ == "__main__":
    main()
