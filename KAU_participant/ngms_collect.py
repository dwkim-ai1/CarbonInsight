from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Tuple, Optional, List
from dataclasses import dataclass

import pandas as pd

from playwright.sync_api import (
    sync_playwright,
    Page,
    Frame,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

# ======================================================
# 기본 설정
# ======================================================
BASE = "https://ngms.gir.go.kr:8443"
SUBMAIN_URL = (
    f"{BASE}/subMain.do?"
    "link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
)

DEFAULT_NAV_TIMEOUT = 60_000
DEFAULT_DL_TIMEOUT = 180_000

EXCEL_BTN_RE = re.compile(r"Excel\s*다운로드", re.IGNORECASE)

FRAME_META = {
    "allocated": {
        "menuno": "50900501",
        "title": "할당대상업체",
        "w2x": "/hom/bbs/OGCMBBS021V.xml",
    },
    "target": {
        "menuno": "50900502",
        "title": "목표관리대상업체",
        "w2x": "/hom/bbs/OGCMBBS022V.xml",
    },
    "statement": {
        "menuno": "50900503",
        "title": "명세서배출량통계",
        "w2x": "/hom/bbs/OGCMBBS023V.xml",
    },
    "public": {
        "menuno": "50900504",
        "title": "공공부문 배출량통계",
        "w2x": "/hom/bbs/OGCMBBS026V.xml",
    },
}


@dataclass
class CollectResult:
    df: pd.DataFrame
    hint: str


# ======================================================
# 공통 유틸
# ======================================================
def _latest_file(download_dir: Path) -> Optional[Path]:
    files = list(download_dir.glob("*.xls*"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _wait_until(predicate, timeout_ms=20_000, interval=0.5) -> bool:
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ======================================================
# 페이지 / iframe 처리
# ======================================================
def _goto_submain(page: Page):
    print("[NGMS] goto subMain")
    page.goto(
        SUBMAIN_URL,
        wait_until="domcontentloaded",
        timeout=DEFAULT_NAV_TIMEOUT,
    )
    # WebSquare는 networkidle이 절대 오지 않음
    page.wait_for_selector("iframe", timeout=DEFAULT_NAV_TIMEOUT)


def _ensure_tab_open(page: Page, key: str):
    meta = FRAME_META[key]
    menuno = meta["menuno"]

    tab_sel = f"#mf_tac_layout_tab_{menuno}.w2tabcontrol_active"
    if page.locator(tab_sel).count() > 0:
        return

    print(f"[NGMS] open tab {key}")
    menu = page.locator(f'a.menuLink[menuno="{menuno}"]').first
    menu.click()
    page.wait_for_selector(tab_sel, timeout=DEFAULT_NAV_TIMEOUT)


def _get_ngms_frame(page: Page, key: str) -> Frame:
    meta = FRAME_META[key]
    title = meta["title"]
    menuno = meta["menuno"]
    w2x = meta["w2x"]

    print(f"[NGMS] locate iframe ({title})")

    # 1) title
    iframe = page.query_selector(f'iframe[title="{title}"]')
    if iframe:
        fr = iframe.content_frame()
        if fr:
            return fr

    # 2) id
    iframe = page.query_selector(f"#mf_tac_layout_contents_{menuno}_body")
    if iframe:
        fr = iframe.content_frame()
        if fr:
            return fr

    # 3) src match
    for iframe in page.query_selector_all("iframe"):
        src = iframe.get_attribute("src") or ""
        if w2x in src:
            fr = iframe.content_frame()
            if fr:
                return fr

    # 4) frame.url
    for fr in page.frames:
        if fr.url and w2x in fr.url:
            return fr

    urls = [fr.url for fr in page.frames if fr.url]
    raise RuntimeError(f"NGMS iframe not found: {urls[:5]}")


# ======================================================
# 다운로드 처리 (page/context/폴링 fallback)
# ======================================================
def _pick_excel_button(frame: Frame):
    loc = frame.locator("button, a").filter(has_text=EXCEL_BTN_RE)
    if loc.count() > 0:
        return loc.first
    raise RuntimeError("Excel 다운로드 버튼을 찾지 못했습니다.")


def _download_excel(
    page: Page,
    context: BrowserContext,
    frame: Frame,
    download_dir: Path,
) -> Path:
    before = _latest_file(download_dir)
    btn = _pick_excel_button(frame)

    print("[NGMS] click Excel download")

    # 1) page.expect_download
    try:
        with page.expect_download(timeout=DEFAULT_DL_TIMEOUT) as dlinfo:
            btn.click()
        dl = dlinfo.value
        out = download_dir / dl.suggested_filename
        dl.save_as(out)
        return out
    except Exception:
        pass

    # 2) context.expect_event
    try:
        with context.expect_event("download", timeout=DEFAULT_DL_TIMEOUT) as dlinfo:
            btn.click()
        dl = dlinfo.value
        out = download_dir / dl.suggested_filename
        dl.save_as(out)
        return out
    except Exception:
        pass

    # 3) polling fallback
    btn.click()

    ok = _wait_until(
        lambda: (_latest_file(download_dir) is not None)
        and (_latest_file(download_dir) != before),
        timeout_ms=DEFAULT_DL_TIMEOUT,
    )
    if not ok:
        raise RuntimeError("다운로드 실패 (이벤트/폴링 모두 실패)")

    return _latest_file(download_dir)


def _read_excel(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    return df.dropna(axis=0, how="all").dropna(axis=1, how="all")


# ======================================================
# 메인 수집 로직
# ======================================================
def _collect(
    key: str,
    download_dir: str,
    plan_period=None,
    designation_year=None,
) -> CollectResult:
    ddir = Path(download_dir)
    ddir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        _goto_submain(page)
        _ensure_tab_open(page, key)
        frame = _get_ngms_frame(page, key)

        print("[NGMS] iframe ready")

        excel = _download_excel(page, context, frame, ddir)
        print(f"[NGMS] downloaded: {excel.name}")

        df = _read_excel(excel)

        browser.close()

    return CollectResult(df=df, hint=f"downloaded={excel.name}")


# ======================================================
# 외부 API (run_ngms.py에서 import)
# ======================================================
def collect_allocated(download_dir: str, plan_period=None, designation_year=None) -> Tuple[pd.DataFrame, str]:
    r = _collect("allocated", download_dir, plan_period, designation_year)
    return r.df, r.hint


def collect_target_managed(download_dir: str, plan_period=None, designation_year=None) -> Tuple[pd.DataFrame, str]:
    r = _collect("target", download_dir, plan_period, designation_year)
    return r.df, r.hint


def collect_statement_stats(download_dir: str, plan_period=None, designation_year=None) -> Tuple[pd.DataFrame, str]:
    r = _collect("statement", download_dir, plan_period, designation_year)
    return r.df, r.hint


def collect_public_stats(download_dir: str, plan_period=None, designation_year=None) -> Tuple[pd.DataFrame, str]:
    r = _collect("public", download_dir, plan_period, designation_year)
    return r.df, r.hint
