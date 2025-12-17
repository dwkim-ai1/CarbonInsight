from __future__ import annotations

import os
import sys
import json
import time
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

import pandas as pd


# ----------------------------
# Utils
# ----------------------------

def _env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return "" if v is None else str(v).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name, "")
    if v == "":
        return default
    return v.lower() in ("1", "true", "t", "yes", "y", "on")


def _env_int(name: str, default: Optional[int] = None) -> Optional[int]:
    v = _env(name, "")
    if v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _safe_mkdir(p: Union[str, Path]) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _to_df(obj: Any) -> Optional[pd.DataFrame]:
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return obj
    # common dict/list conversions
    if isinstance(obj, (list, tuple)):
        try:
            return pd.DataFrame(obj)
        except Exception:
            return None
    if isinstance(obj, dict):
        # if dict has 'df'
        if "df" in obj and isinstance(obj["df"], pd.DataFrame):
            return obj["df"]
        try:
            return pd.DataFrame([obj])
        except Exception:
            return None
    return None


def _extract_payload(ret: Any) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    """
    collector return can be:
      - (df, hint/meta)
      - df
      - {"df": df, "file": "...", "hint": "...", ...}
      - {"file": "..."} only (no df)
    """
    meta: Dict[str, Any] = {}

    if ret is None:
        return None, meta

    # tuple return
    if isinstance(ret, tuple) and len(ret) == 2:
        df = _to_df(ret[0])
        hint = ret[1]
        if isinstance(hint, dict):
            meta.update(hint)
        else:
            meta["hint"] = hint
        return df, meta

    # dict return
    if isinstance(ret, dict):
        if "df" in ret:
            df = _to_df(ret.get("df"))
        else:
            df = None
        meta.update({k: v for k, v in ret.items() if k != "df"})
        return df, meta

    # df return
    df = _to_df(ret)
    return df, meta


def _save_df(df: pd.DataFrame, out_dir: Path, stem: str) -> Path:
    out_dir = _safe_mkdir(out_dir)
    out_path = out_dir / f"{stem}.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def _save_meta(meta: Dict[str, Any], out_dir: Path, stem: str) -> Path:
    out_dir = _safe_mkdir(out_dir)
    out_path = out_dir / f"{stem}.meta.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
    return out_path


# ----------------------------
# Collector registry (auto-detect)
# ----------------------------

@dataclass
class Job:
    key: str
    func_name_candidates: List[str]


DEFAULT_JOBS: List[Job] = [
    Job("allocated", ["collect_allocated", "collect_allocated_companies", "collect_allocation", "collect_alloc"]),
    Job("target_mgmt", ["collect_target_mgmt", "collect_target_management", "collect_target", "collect_goal_mgmt"]),
    Job("spec_stat", ["collect_spec_stat", "collect_spec_statistics", "collect_spec", "collect_emission_spec"]),
    Job("public_stat", ["collect_public_stat", "collect_public_statistics", "collect_public"]),
]


def _resolve_func(mod: Any, candidates: List[str]) -> Optional[Callable[..., Any]]:
    for name in candidates:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    # env / args
    download_dir = Path(_env("NGMS_DOWNLOAD_DIR", "tmp_downloads"))
    out_dir = Path(_env("NGMS_OUT_DIR", "tmp_outputs"))

    # 빈 문자열이면 None으로 떨어지게
    plan_period = _env("PLAN_PERIOD", "")
    if plan_period == "":
        plan_period = None  # type: ignore

    designation_year = _env_int("DESIGNATION_YEAR", None)
    emission_year = _env_int("EMISSION_YEAR", None)

    manual_append_only = _env_bool("MANUAL_APPEND_ONLY", False)

    # downloads clean policy (optional)
    clean_downloads = _env_bool("CLEAN_DOWNLOAD_DIR", True)
    clean_outputs = _env_bool("CLEAN_OUTPUT_DIR", False)

    if clean_downloads and download_dir.exists():
        shutil.rmtree(download_dir, ignore_errors=True)
    if clean_outputs and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)

    _safe_mkdir(download_dir)
    _safe_mkdir(out_dir)

    print("[run_ngms] download_dir =", str(download_dir))
    print("[run_ngms] out_dir      =", str(out_dir))
    print("[run_ngms] plan_period  =", plan_period)
    print("[run_ngms] designation_year =", designation_year)
    print("[run_ngms] emission_year    =", emission_year)
    print("[run_ngms] manual_append_only =", manual_append_only)

    # import collector module (single source of truth)
    try:
        from KAU_participant import ngms_collect as collect_mod
    except Exception as e:
        print("[run_ngms] ERROR: failed to import KAU_participant.ngms_collect")
        raise

    # allow module-level init hook (optional)
    # e.g., ngms_collect.ensure_playwright_deps()
    init_hook = getattr(collect_mod, "init", None)
    if callable(init_hook):
        try:
            init_hook()
        except Exception:
            print("[run_ngms] ngms_collect.init() failed (ignored).")

    # Determine which jobs exist in this ngms_collect.py
    jobs_to_run: List[Tuple[str, Callable[..., Any]]] = []
    for job in DEFAULT_JOBS:
        fn = _resolve_func(collect_mod, job.func_name_candidates)
        if fn is None:
            print(f"[run_ngms] skip '{job.key}': no function found among {job.func_name_candidates}")
            continue
        jobs_to_run.append((job.key, fn))

    if not jobs_to_run:
        raise RuntimeError(
            "ngms_collect.py에서 실행 가능한 수집 함수를 찾지 못했습니다. "
            "예: collect_allocated / collect_target_mgmt / collect_spec_stat ..."
        )

    run_id = _timestamp()
    failures: List[Tuple[str, str]] = []

    for key, fn in jobs_to_run:
        print(f"\n[run_ngms] === RUN {key} ({fn.__name__}) ===")
        try:
            # Flexible call: pass only supported kwargs
            kwargs: Dict[str, Any] = {}
            # most collectors accept download_dir
            kwargs["download_dir"] = str(download_dir)

            # optional common params
            if plan_period is not None:
                kwargs["plan_period"] = plan_period
            if designation_year is not None:
                kwargs["designation_year"] = designation_year
            if emission_year is not None:
                kwargs["emission_year"] = emission_year

            # some implementations use different names
            # We'll try calling with decreasing kwargs if TypeError occurs.
            ret = _call_flexible(fn, kwargs)

            df, meta = _extract_payload(ret)

            # persist
            stem = f"{run_id}.{key}"
            if df is not None and not df.empty:
                csv_path = _save_df(df, out_dir, stem)
                print(f"[run_ngms] saved df -> {csv_path}")
            else:
                print(f"[run_ngms] df empty or None for '{key}'")

            if meta:
                meta_path = _save_meta(meta, out_dir, stem)
                print(f"[run_ngms] saved meta -> {meta_path}")

        except Exception:
            tb = traceback.format_exc()
            failures.append((key, tb))
            print(f"[run_ngms] FAILED '{key}':\n{tb}")

    print("\n[run_ngms] === DONE ===")
    if failures:
        print(f"[run_ngms] failures: {len(failures)}")
        for k, tb in failures:
            print(f"\n--- {k} ---\n{tb}")
        # CI에서 실패로 처리하고 싶으면 아래를 1로
        fail_exit = _env_bool("FAIL_ON_PARTIAL_ERROR", True)
        if fail_exit:
            raise SystemExit(1)
    else:
        print("[run_ngms] all jobs succeeded.")


def _call_flexible(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Any:
    """
    TypeError: unexpected keyword argument 를 만났을 때,
    kwargs를 하나씩 줄여가며 호출을 성사시키는 fallback.
    """
    try:
        return fn(**kwargs)
    except TypeError as e:
        msg = str(e)
        # if it's not about unexpected kwargs, re-raise
        if "unexpected keyword argument" not in msg and "got an unexpected keyword argument" not in msg:
            raise

        # Drop kwargs until it works
        keys = list(kwargs.keys())
        # keep download_dir as last resort param
        keys_sorted = [k for k in keys if k != "download_dir"] + (["download_dir"] if "download_dir" in kwargs else [])
        cur = dict(kwargs)

        for k in keys_sorted:
            if k in cur and k != "download_dir":
                cur.pop(k, None)
                try:
                    return fn(**cur)
                except TypeError as e2:
                    msg2 = str(e2)
                    if "unexpected keyword argument" in msg2 or "got an unexpected keyword argument" in msg2:
                        continue
                    raise

        # final attempt: only download_dir if present, else no-arg
        if "download_dir" in kwargs:
            try:
                return fn(download_dir=kwargs["download_dir"])
            except Exception:
                pass
        return fn()


if __name__ == "__main__":
    main()
