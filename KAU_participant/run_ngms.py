# KAU_participant/run_ngms.py
from __future__ import annotations

import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd

from KAU_participant.ngms_collect import (
    collect_allocated,
    collect_target_managed,
    collect_statement_stats,
    collect_public_stats,
)


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _save_df(df: pd.DataFrame, out_path: Path) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return str(out_path)


def main():
    parser = argparse.ArgumentParser(description="NGMS 정보공개 데이터 수집 (Excel 다운로드 기반)")
    parser.add_argument(
        "--download-dir",
        default="downloads/ngms",
        help="playwright 다운로드 디렉토리 (기본: downloads/ngms)",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/ngms",
        help="결과 CSV 저장 디렉토리 (기본: outputs/ngms)",
    )
    parser.add_argument(
        "--plan-period",
        default=None,
        help="계획기간(예: 3차, 4차 등). ngms_collect에서 지원하는 값으로 입력",
    )
    parser.add_argument(
        "--designation-year",
        type=int,
        default=None,
        help="지정연도(예: 2024). ngms_collect에서 지원하는 값으로 입력",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="특정 항목만 실행: allocated|target|statement|public (콤마로 복수 가능)",
    )

    args = parser.parse_args()

    download_dir = _ensure_dir(Path(args.download_dir))
    output_dir = _ensure_dir(Path(args.output_dir))
    run_tag = _stamp()

    only = None
    if args.only:
        only = {x.strip().lower() for x in args.only.split(",") if x.strip()}

    results = []

    def should_run(key: str) -> bool:
        return (only is None) or (key in only)

    # 1) 할당대상업체
    if should_run("allocated"):
        df, hint = collect_allocated(
            download_dir=str(download_dir),
            plan_period=args.plan_period,
            designation_year=args.designation_year,
        )
        out = output_dir / f"{run_tag}_allocated.csv"
        results.append(("allocated", _save_df(df, out), hint))

    # 2) 목표관리대상업체
    if should_run("target"):
        df, hint = collect_target_managed(
            download_dir=str(download_dir),
            plan_period=args.plan_period,
            designation_year=args.designation_year,
        )
        out = output_dir / f"{run_tag}_target_managed.csv"
        results.append(("target", _save_df(df, out), hint))

    # 3) 명세서배출량통계
    if should_run("statement"):
        df, hint = collect_statement_stats(
            download_dir=str(download_dir),
            plan_period=args.plan_period,
            designation_year=args.designation_year,
        )
        out = output_dir / f"{run_tag}_statement_stats.csv"
        results.append(("statement", _save_df(df, out), hint))

    # 4) 공공부문 배출량통계
    if should_run("public"):
        df, hint = collect_public_stats(
            download_dir=str(download_dir),
            plan_period=args.plan_period,
            designation_year=args.designation_year,
        )
        out = output_dir / f"{run_tag}_public_stats.csv"
        results.append(("public", _save_df(df, out), hint))

    # 요약 출력
    print("\n=== NGMS Collect Summary ===")
    for key, path, hint in results:
        print(f"- {key}: {path}")
        if hint:
            print(f"  hint: {hint}")
    print("=== Done ===\n")


if __name__ == "__main__":
    main()
