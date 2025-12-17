# KAU_participant/ngms_collect.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd

from playwright.sync_api import (
    sync_playwright,
    Page,
    Frame,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
)

# =========================
# NGMS 고정 URL (사용자가 제공한 형태 유지)
# =========================
BASE = "https://ngms.gir.go.kr:8443"
URL_SUBMAIN = (
    f"{BASE}/subMain.do?"
    "link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
)

# 메뉴별 iframe src (HTML에서 확인됨)
W2_IFRAME_SRC = {
    "allocated": "/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501",
    "target":    "/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS022V.xml&menuNo=50900502",
    "statement": "/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS023V.xml&menuNo=50900503",
    "public":    "/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS026V.xml&menuNo=50900504",
}

# iframe title (HTML: <iframe ... title="할당대상업체">)
FRAME_TITLE = {
    "allocated": "할당대상업체",
    "target": "목표관리대상업체",
    "statement": "명세서배출량통계",
    "public": "공공부문 배출량통계",
}

# 다운로드 버튼 텍스트 (기존에 쓰던 정규식 유지)
EXCEL_BTN_RE = re.compile(r"Excel\s*다운로드", re.IGNORECASE)

DEFAULT_NAV_TIMEOUT = 60_000
DEFAULT_DL_TIMEOUT = 180_000


@dataclass
class CollectResult:
    df: pd.DataFrame
    hint: str = ""


# =========================
# 공통 유틸
# =========================
def _latest_file(download_dir: Path, suffixes: Tuple[str, ...] = (".xls", ".xlsx")) -> Optional[Path]:
    files = [p for p in download_dir.glob("*") if p.suffix.lower() in suffixes and p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def _wait_until(predicate, timeout_ms: int = 20_000, interval: float = 0.2):
    t0 = time.time()
    while (time.time() - t0) * 1000 < timeout_ms:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _goto_submain(page: Page):
    page.goto(URL_SUBMAIN, wait_until="domcontentloaded", timeout=DEFAULT_NAV_TIMEOUT)
    # WebSquare는 로딩이 늦게 붙는 경우가 있어 약간 대기
    page.wait_for_load_state("networkidle", timeout=DEFAULT_NAV_TIMEOUT)


def _ensure_tab_open(page: Page, key: str):
    """
    subMain 상단 탭에 해당 메뉴가 이미 열려있지 않으면 메뉴 클릭으로 열림.
    HTML에서 메뉴 링크는 a.menuLink[menuno="..."] 형태.
    """
    # menuno 값은 HTML 내 메뉴: 50900501~04
    menuno = {
        "allocated": "50900501",
        "target": "50900502",
        "statement": "50900503",
        "public": "50900504",
    }[key]

    # 탭이 이미 활성화되어 있으면 스킵
    tab_id = f"mf_tac_layout_tab_{menuno}"
    if page.locator(f"#{tab_id}.w2tabcontrol_active").count() > 0:
        return

    # 메뉴 클릭(상단/전체메뉴 둘 다 존재하나 동일 class menuLink)
    menu = page.locator(f'a.menuLink[menuno="{menuno}"]').first
    menu.click()
    # 탭 활성화 대기
    page.wait_for_selector(f"#{tab_id}.w2tabcontrol_active", timeout=DEFAULT_NAV_TIMEOUT)


def _get_ngms_frame(page: Page, key: str) -> Frame:
    """
    iframe -> Frame 획득은 ElementHandle 기반으로만 수행(가장 안정적).
    우선순위:
      1) iframe[title=...]
      2) iframe#mf_tac_layout_contents_{menuno}_body
      3) 모든 iframe src / frame.url 매칭
    """
    title = FRAME_TITLE[key]
    menuno = {
        "allocated": "50900501",
        "target": "50900502",
        "statement": "50900503",
        "public": "50900504",
    }[key]

    # 1) title 기준 (HTML에 명시됨)
    try:
        page.wait_for_selector(f'iframe[title="{title}"]', timeout=DEFAULT_NAV_TIMEOUT)
        iframe_el = page.query_selector(f'iframe[title="{title}"]')
        if iframe_el:
            fr = iframe_el.content_frame()
            if fr:
                return fr
    except Exception:
        pass

    # 2) id 기준
    id_guess = f"mf_tac_layout_contents_{menuno}_body"
    try:
        page.wait_for_selector(f'iframe#{id_guess}', timeout=DEFAULT_NAV_TIMEOUT)
        iframe_el = page.query_selector(f'iframe#{id_guess}')
        if iframe_el:
            fr = iframe_el.content_frame()
            if fr:
                return fr
    except Exception:
        pass

    # 3) src / frame.url 매칭
    want_prefix = W2_IFRAME_SRC[key].split("&menuNo=")[0]

    # 3-1) DOM의 iframe src들로 매칭
    try:
        iframe_els = page.query_selector_all("iframe")
        for iframe_el in iframe_els:
            src = iframe_el.get_attribute("src") or ""
            if want_prefix in src:
                fr = iframe_el.content_frame()
                if fr:
                    return fr
    except Exception:
        pass

    # 3-2) page.frames url로 매칭
    last_frames: List[Frame] = page.frames
    for fr in last_frames:
        if fr.url and want_prefix in fr.url:
            return fr

    urls = [fr.url for fr in last_frames if fr.url]
    raise RuntimeError(
        f"NGMS iframe을 찾지 못했습니다. (frames={len(last_frames)}, urls_sample={urls[:5]})"
    )


def _pick_excel_button(frame: Frame):
    """
    WebSquare 내부는 role이 애매한 경우가 많아서, locator를 여러 개로 fallback.
    """
    # 1) role=button + name regex
    loc = frame.get_by_role("button", name=EXCEL_BTN_RE)
    if loc.count() > 0:
        return loc.first

    # 2) a / button 텍스트 기반
    loc = frame.locator("button, a").filter(has_text=EXCEL_BTN_RE)
    if loc.count() > 0:
        return loc.first

    # 3) input[type=button] value
    loc = frame.locator('input[type="button"], input[type="submit"]').filter(has_text=EXCEL_BTN_RE)
    if loc.count() > 0:
        return loc.first

    # 4) 최후: 텍스트 포함 요소
    loc = frame.locator("*").filter(has_text=EXCEL_BTN_RE)
    if loc.count() > 0:
        return loc.first

    raise RuntimeError("Excel 다운로드 버튼을 찾지 못했습니다.")


def _download_excel_with_fallback(
    page: Page,
    context: BrowserContext,
    frame: Frame,
    download_dir: Path,
    timeout_ms: int = DEFAULT_DL_TIMEOUT,
) -> Path:
    """
    클릭은 frame에서 수행하되,
    download 이벤트는
      1) page.expect_download()
      2) context.expect_event("download")
      3) 마지막 fallback: download_dir에서 최신파일 폴링
    순서로 잡는다.
    """
    before = _latest_file(download_dir)
    btn = _pick_excel_button(frame)

    # 1) page.expect_download
    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            btn.click()
        dl = dl_info.value
        out_path = download_dir / dl.suggested_filename
        dl.save_as(out_path)
        return out_path
    except PlaywrightTimeoutError:
        pass
    except Exception:
        # page에서 못 잡는 환경이 있음(iframe/새창/JS 다운로드 등)
        pass

    # 2) context.expect_event("download")
    try:
        with context.expect_event("download", timeout=timeout_ms) as dl_info:
            btn.click()
        dl = dl_info.value
        out_path = download_dir / dl.suggested_filename
        dl.save_as(out_path)
        return out_path
    except PlaywrightTimeoutError:
        pass
    except Exception:
        pass

    # 3) 폴링 fallback (서버가 파일 응답을 바로 떨어뜨리면 이벤트가 누락될 때가 있음)
    btn.click()

    ok = _wait_until(
        lambda: (_latest_file(download_dir) is not None)
        and (_latest_file(download_dir) != before),
        timeout_ms=timeout_ms,
        interval=0.5,
    )
    if not ok:
        raise RuntimeError("다운로드가 발생하지 않았습니다(이벤트/폴링 모두 실패).")

    latest = _latest_file(download_dir)
    if latest is None:
        raise RuntimeError("다운로드 파일을 찾지 못했습니다.")
    return latest


def _read_excel_to_df(excel_path: Path) -> pd.DataFrame:
    # 엔진은 자동으로 잡히지만, xls/xlsx 혼재 가능해서 read_excel만 사용
    df = pd.read_excel(excel_path)
    # 완전 빈 컬럼/행 정리(선택)
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return df


def _collect(key: str, download_dir: str, plan_period=None, designation_year=None) -> CollectResult:
    """
    plan_period/designation_year는 추후 UI 셀렉트 로직 붙일 자리.
    지금은 다운로드 안정성에 집중.
    """
    ddir = Path(download_dir)
    ddir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        _goto_submain(page)
        _ensure_tab_open(page, key)

        frame = _get_ngms_frame(page, key)

        # 프레임 내부가 느리게 렌더링될 수 있어 최소한의 안정 대기
        frame.wait_for_load_state("domcontentloaded", timeout=DEFAULT_NAV_TIMEOUT)

        # (선택) UI 파라미터 적용 자리: plan_period / designation_year
        # 지금은 버튼 클릭/다운로드만 안정화 목적이라 스킵

        excel_path = _download_excel_with_fallback(page, context, frame, ddir)

        df = _read_excel_to_df(excel_path)

        browser.close()

    hint = f"downloaded={excel_path.name}"
    return CollectResult(df=df, hint=hint)


# =========================
# 외부에 노출되는 API (run_ngms.py에서 import)
# =========================
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
