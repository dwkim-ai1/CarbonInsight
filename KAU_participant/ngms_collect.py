# KAU_participant/ngms_collect.py
from __future__ import annotations

import time
from pathlib import Path
from typing import Tuple

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Frame,
    TimeoutError as PlaywrightTimeoutError,
)

# =========================
# Constants
# =========================

NGMS_ENTRY_URL = (
    "https://ngms.gir.go.kr:8443/"
    "subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
)

IFRAME_IDS = {
    "allocated": "mf_tac_layout_contents_50900501_body",
    "target_mgmt": "mf_tac_layout_contents_50900502_body",
    "spec_emission": "mf_tac_layout_contents_50900503_body",
    "public_sector": "mf_tac_layout_contents_50900504_body",
}

EXCEL_BUTTON_TEXTS = [
    "Excel 다운로드",
    "엑셀 다운로드",
    "엑셀",
    "Excel",
]

# =========================
# Low-level helpers
# =========================

def _launch_page(download_dir: Path) -> Page:
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()

    page.goto(NGMS_ENTRY_URL, wait_until="networkidle", timeout=60_000)
    page.wait_for_timeout(2_000)

    # attach handles for cleanup
    page._pw = pw
    page._browser = browser
    page._context = context
    page._download_dir = download_dir
    return page


def _cleanup_page(page: Page) -> None:
    try:
        page._context.close()
        page._browser.close()
        page._pw.stop()
    except Exception:
        pass


def _get_frame(page: Page, key: str) -> Frame:
    iframe_id = IFRAME_IDS[key]
    iframe = page.locator(f"iframe#{iframe_id}")
    iframe.wait_for(state="attached", timeout=30_000)

    frame = iframe.content_frame()
    if frame is None:
        raise RuntimeError(f"iframe content_frame 획득 실패: {iframe_id}")

    frame.wait_for_load_state("networkidle", timeout=60_000)
    return frame


def _try_click_dom(frame: Frame) -> bool:
    for text in EXCEL_BUTTON_TEXTS:
        locators = [
            frame.get_by_role("button", name=text),
            frame.get_by_text(text, exact=False),
        ]
        for loc in locators:
            try:
                if loc.count() > 0:
                    loc.first.click(timeout=3_000)
                    return True
            except Exception:
                pass
    return False


def _try_click_js(frame: Frame) -> bool:
    script = r"""
    (() => {
        const texts = %s;
        const els = Array.from(document.querySelectorAll("button,a,span,div"));
        for (const el of els) {
            const t = (el.innerText || "").trim();
            if (texts.some(x => t.includes(x))) {
                el.click();
                return true;
            }
        }
        return false;
    })();
    """ % EXCEL_BUTTON_TEXTS
    try:
        return bool(frame.evaluate(script))
    except Exception:
        return False


def _download_excel(page: Page, frame: Frame, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)

    with page.expect_download(timeout=180_000) as dlinfo:
        if _try_click_dom(frame):
            pass
        elif _try_click_js(frame):
            pass
        else:
            raise RuntimeError("Excel 다운로드 버튼 클릭 실패 (DOM/JS)")

    download = dlinfo.value
    out = download_dir / (download.suggested_filename or "ngms.xlsx")
    download.save_as(out)
    return out


def _read_excel(path: Path) -> pd.DataFrame:
    return pd.read_excel(path)


# =========================
# Core collector
# =========================

def _collect(
    key: str,
    download_dir: str,
    plan_period: str = "",
    designation_year: str = "",
    emission_year: str = "",
) -> Tuple[pd.DataFrame, str]:

    dldir = Path(download_dir)
    page = _launch_page(dldir)

    try:
        frame = _get_frame(page, key)
        excel_path = _download_excel(page, frame, dldir)
        df = _read_excel(excel_path)

        hint_parts = []
        if plan_period:
            hint_parts.append(f"plan_period={plan_period}")
        if designation_year:
            hint_parts.append(f"designation_year={designation_year}")
        if emission_year:
            hint_parts.append(f"emission_year={emission_year}")

        hint = ", ".join(hint_parts)
        return df, hint

    finally:
        _cleanup_page(page)


# =========================
# Public API (run_ngms.py 대응)
# =========================

def collect_allocated(
    download_dir: str,
    plan_period: str = "",
    designation_year: str = "",
    base_url: str | None = None,
) -> Tuple[pd.DataFrame, str]:
    return _collect(
        "allocated",
        download_dir,
        plan_period=plan_period,
        designation_year=designation_year,
    )


def collect_target_mgmt(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str | None = None,
) -> Tuple[pd.DataFrame, str]:
    return _collect(
        "target_mgmt",
        download_dir,
        plan_period=plan_period,
        emission_year=emission_year,
    )


def collect_spec_emission_stats(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str | None = None,
) -> Tuple[pd.DataFrame, str]:
    return _collect(
        "spec_emission",
        download_dir,
        plan_period=plan_period,
        emission_year=emission_year,
    )


def collect_public_sector_stats(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str | None = None,
) -> Tuple[pd.DataFrame, str]:
    return _collect(
        "public_sector",
        download_dir,
        plan_period=plan_period,
        emission_year=emission_year,
    )
