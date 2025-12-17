from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Frame,
    Download,
)

BASE_URL = "https://ngms.gir.go.kr/hom/subMain.do"


# =========================
# iframe / 공통 유틸
# =========================
def _get_ngms_frame(page: Page, title_keyword: Optional[str] = None) -> Frame:
    deadline = time.time() + 20
    while time.time() < deadline:
        for f in page.frames:
            try:
                if title_keyword and f.title() and title_keyword in f.title():
                    return f
                if "websquare/ngms.do" in (f.url or ""):
                    return f
            except Exception:
                pass
        time.sleep(0.3)
    raise RuntimeError("NGMS iframe을 찾지 못했습니다.")


def _safe_click(frame: Frame, texts: List[str], timeout=30_000):
    last = None
    for t in texts:
        try:
            loc = frame.get_by_text(t, exact=False)
            loc.first.wait_for(state="visible", timeout=timeout)
            loc.first.scroll_into_view_if_needed()
            loc.first.click(timeout=timeout)
            return
        except Exception as e:
            last = e
    raise RuntimeError(f"클릭 실패: {texts}, last={last}")


def _safe_search(frame: Frame):
    _safe_click(frame, ["조회", "검색", "Search"])


def _safe_set_combo(frame: Frame, label: str, value: str) -> None:
    if not value:
        return
    try:
        frame.get_by_label(re.compile(label)).fill(value)
        frame.keyboard.press("Enter")
        return
    except Exception:
        pass
    try:
        frame.get_by_text(value, exact=True).click()
        return
    except Exception:
        pass


def _download_excel(frame: Frame, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    with frame.expect_download(timeout=120_000) as dlinfo:
        _safe_click(frame, ["Excel 다운로드", "엑셀 다운로드", "엑셀"])
    dl: Download = dlinfo.value
    out = download_dir / (dl.suggested_filename or "ngms.xlsx")
    dl.save_as(out)
    return out


def _collect(
    *,
    menu_no: str,
    frame_title: str,
    download_dir: Path,
    filters: Dict[str, str],
) -> Tuple[pd.DataFrame, str]:
    hints = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        page.goto(f"{BASE_URL}?menuNo={menu_no}", wait_until="networkidle")
        frame = _get_ngms_frame(page, frame_title)

        for label, value in filters.items():
            try:
                _safe_set_combo(frame, label, value)
            except Exception:
                hints.append(f"{label} 필터 실패({value})")

        try:
            _safe_search(frame)
        except Exception:
            hints.append("조회 버튼 미확인")

        excel = _download_excel(frame, download_dir)
        df = pd.read_excel(excel)

        ctx.close()
        browser.close()

    return df, "\n".join(hints)


# =========================
# 공개 수집 함수 3종
# =========================
def collect_allocated(download_dir: Path, plan_period="", designation_year=""):
    return _collect(
        menu_no="50900501",
        frame_title="할당대상업체",
        download_dir=download_dir,
        filters={
            "계획기간": plan_period,
            "지정연도": designation_year,
        },
    )


def collect_target_mgmt(download_dir: Path, designation_year=""):
    return _collect(
        menu_no="50900502",
        frame_title="목표관리대상업체",
        download_dir=download_dir,
        filters={
            "지정연도": designation_year,
        },
    )


def collect_statement_stats(download_dir: Path, emission_year=""):
    df, hint = _collect(
        menu_no="50900503",
        frame_title="명세서배출량통계",
        download_dir=download_dir,
        filters={
            "배출년도": emission_year,
        },
    )
    return df, hint
