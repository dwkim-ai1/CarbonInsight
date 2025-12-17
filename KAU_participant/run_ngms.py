import os
from pathlib import Path

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

# 현재값(덮어쓰기)
CUR_ALLOC = "NGMS_할당대상업체"
CUR_TARGET = "NGMS_목표관리대상업체"
CUR_STATS = "NGMS_명세서배출량통계"

# 히스토리(누적)
HIS_ALLOC = "할당대상업체"
HIS_TARGET = "목표관리대상업체"
HIS_STATS = "명세서배출량통계"


def attach_meta(df, hint):
    df = df.copy()
    df["수집일시"] = now_kst_str()
    df["원본업데이트표시"] = hint
    return df


def update_with_history(ss, cur_name, his_name, new_df):
    ws_cur = upsert_worksheet(ss, cur_name)
    ws_his = upsert_worksheet(ss, his_name)

    old_df = read_worksheet_df(ws_cur)
    ignore = ["수집일시", "원본업데이트표시"]

    if df_fingerprint(old_df, ignore) != df_fingerprint(new_df, ignore):
        append_snapshot(ws_his, new_df)

    overwrite_worksheet(ws_cur, new_df)


def main():
    download_dir = Path(os.environ.get("NGMS_DOWNLOAD_DIR", "tmp_downloads"))
    plan_period = os.environ.get("PLAN_PERIOD", "")
    designation_year = os.environ.get("DESIGNATION_YEAR", "")
    emission_year = os.environ.get("EMISSION_YEAR", "")
    manual_only = os.environ.get("MANUAL_APPEND_ONLY", "false").lower() == "true"

    alloc_df, alloc_hint = collect_allocated(download_dir, plan_period, designation_year)
    target_df, target_hint = collect_target_mgmt(download_dir, designation_year)
    stats_df, stats_hint = collect_statement_stats(download_dir, emission_year)

    alloc_df = attach_meta(alloc_df, alloc_hint)
    target_df = attach_meta(target_df, target_hint)
    stats_df = attach_meta(stats_df, stats_hint)

    ss = get_gspread_client().open_by_key(os.environ["KAU_SHEET_ID"])

    if manual_only:
        append_snapshot(upsert_worksheet(ss, HIS_ALLOC), alloc_df)
        append_snapshot(upsert_worksheet(ss, HIS_TARGET), target_df)
        append_snapshot(upsert_worksheet(ss, HIS_STATS), stats_df)
        print("수동 실행: 히스토리 시트에만 append 완료")
        return

    update_with_history(ss, CUR_ALLOC, HIS_ALLOC, alloc_df)
    update_with_history(ss, CUR_TARGET, HIS_TARGET, target_df)
    update_with_history(ss, CUR_STATS, HIS_STATS, stats_df)

    print("월간 실행 완료: NGMS_* overwrite + 변경 시 히스토리 누적")


if __name__ == "__main__":
    main()
