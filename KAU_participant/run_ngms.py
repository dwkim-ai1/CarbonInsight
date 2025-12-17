# KAU_participant/run_ngms.py
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Dict, Tuple, Optional, Callable

import pandas as pd

from KAU_participant.ngms_collect import (
    collect_allocated,
    collect_target_mgmt,
    collect_spec_emission_stats,
    collect_public_sector_stats,
)

# =========================
# Helpers
# =========================

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return "" if v is None else str(v).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name, "")
    if not v:
        return default
    return v.lower() in ("1", "true", "t", "yes", "y", "on")


def _ensure_dir(p: str | Path) -> Path:
    path = Path(p).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_csv_safe(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, encoding="utf-8-sig")


def _print_section(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _run_one(
    name: str,
    fn: Callable[[], Tuple[pd.DataFrame, str]],
    out_dir: Path,
    manual_append_only: bool,
) -> Dict[str, str]:
    """
    항목 단위 실행:
    - 실패해도 전체 파이프라인은 계속 진행
    - 성공 시 csv 저장
    - manual_append_only면 파일이 이미 있으면 덮어쓰지 않음
    """
    _print_section(f"[START] {name}")
    result: Dict[str, str] = {"name": name, "status": "UNKNOWN", "hint": "", "csv": "", "error": ""}

    try:
        df, hint = fn()
        result["hint"] = hint

        csv_path = out_dir / f"{name}.csv"
        if manual_append_only and csv_path.exists():
            result["status"] = "SKIPPED"
            result["csv"] = str(csv_path)
            print(f"- manual_append_only=true 이므로 기존 파일 존재 시 스킵: {csv_path}")
            return result

        _to_csv_safe(df, csv_path)
        result["status"] = "OK"
        result["csv"] = str(csv_path)

        print(f"- rows={len(df):,}, cols={len(df.columns):,}")
        print(f"- saved: {csv_path}")
        if hint:
            print(f"- hint: {hint}")
        return result

    except Exception as e:
        result["status"] = "FAIL"
        result["error"] = f"{type(e).__name__}: {e}"
        print(f"- ERROR: {result['error']}")
        print("--- traceback ---")
        traceback.print_exc()
        return result

    finally:
        print(f"[END] {name}")


# =========================
# Main
# =========================

def main() -> int:
    """
    환경변수(사용자 CI/로컬 공통):
      - NGMS_DOWNLOAD_DIR (default: tmp_downloads)
      - NGMS_BASE_URL (default: https://ngms.gir.go.kr:8443)

      - PLAN_PERIOD (선택)          : 힌트용(현재 자동 필터 조작은 안 함)
      - DESIGNATION_YEAR (선택)     : 힌트용
      - EMISSION_YEAR (선택)        : 힌트용

      - OUT_DIR (default: ngms_outputs)
      - MANUAL_APPEND_ONLY (default: false) : true면 기존 csv 존재 시 덮어쓰기 안 함
    """
    download_dir = _env("NGMS_DOWNLOAD_DIR", "tmp_downloads")
    base_url = _env("NGMS_BASE_URL", "https://ngms.gir.go.kr:8443")

    plan_period = _env("PLAN_PERIOD", "")
    designation_year = _env("DESIGNATION_YEAR", "")
    emission_year = _env("EMISSION_YEAR", "")

    out_dir = _ensure_dir(_env("OUT_DIR", "ngms_outputs"))
    manual_append_only = _env_bool("MANUAL_APPEND_ONLY", False)

    _print_section("NGMS batch collect - config")
    print(f"- NGMS_BASE_URL      : {base_url}")
    print(f"- NGMS_DOWNLOAD_DIR  : {Path(download_dir).resolve()}")
    print(f"- OUT_DIR            : {out_dir}")
    print(f"- PLAN_PERIOD        : {plan_period}")
    print(f"- DESIGNATION_YEAR   : {designation_year}")
    print(f"- EMISSION_YEAR      : {emission_year}")
    print(f"- MANUAL_APPEND_ONLY : {manual_append_only}")

    jobs = [
        (
            "allocated",
            lambda: collect_allocated(
                download_dir=download_dir,
                plan_period=plan_period,
                designation_year=designation_year,
                base_url=base_url,
            ),
        ),
        (
            "target_mgmt",
            lambda: collect_target_mgmt(
                download_dir=download_dir,
                plan_period=plan_period,
                emission_year=emission_year,
                base_url=base_url,
            ),
        ),
        (
            "spec_emission_stats",
            lambda: collect_spec_emission_stats(
                download_dir=download_dir,
                plan_period=plan_period,
                emission_year=emission_year,
                base_url=base_url,
            ),
        ),
        (
            "public_sector_stats",
            lambda: collect_public_sector_stats(
                download_dir=download_dir,
                plan_period=plan_period,
                emission_year=emission_year,
                base_url=base_url,
            ),
        ),
    ]

    results: list[Dict[str, str]] = []
    for name, fn in jobs:
        results.append(_run_one(name, fn, out_dir, manual_append_only))

    _print_section("NGMS batch collect - summary")
    ok = [r for r in results if r["status"] == "OK"]
    skipped = [r for r in results if r["status"] == "SKIPPED"]
    fail = [r for r in results if r["status"] == "FAIL"]

    print(f"- OK     : {len(ok)}")
    print(f"- SKIPPED: {len(skipped)}")
    print(f"- FAIL   : {len(fail)}")

    for r in results:
        line = f"[{r['status']}] {r['name']}"
        if r["csv"]:
            line += f" -> {r['csv']}"
        if r["error"]:
            line += f" | {r['error']}"
        print(line)

    # 하나라도 실패하면 exit code 1
    return 0 if len(fail) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
