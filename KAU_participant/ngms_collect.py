from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Frame, Download


# 네가 제공한 베이스 패턴을 그대로 사용
BASE = "https://ngms.gir.go.kr:8443/subMain.do"
DEFAULT_LINK = "/hom/bbs/OGCMBBS021V.xml"

FRAME_PROBE_TEXTS = [
    "Excel", "엑셀", "다운로드", "조회", "검색",
    "할당", "목표", "명세서", "배출", "배출량",
]


def _build_url(menu_no: str, link: str = DEFAULT_LINK) -> str:
    # link 파라미터는 반드시 포함
    return f"{BASE}?link={link}&menuNo={menu_no}"


def _frame_has_any_text(frame: Frame, texts: List[str]) -> bool:
    try:
        for t in texts:
            loc = frame.get_by_text(t, exact=False)
            try:
                loc.first.wait_for(state="attached", timeout=600)
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _get_ngms_frame(page: Page, title_keyword: Optional[str] = None) -> Frame:
    """
    1) iframe이 있으면 iframe DOM -> content_frame() 기반으로 후보를 만든다.
    2) iframe이 '없으면' main_frame이 실제 화면일 수 있으므로 main_frame도 후보에 넣는다.
    3) 최종 선택은 '프레임 안에 실제 UI 텍스트가 존재하는지'로 확정한다.
    """
    deadline = time.time() + 60
    last_urls: List[str] = []

    while time.time() < deadline:
        candidates: List[Frame] = []

        # (A) iframe 후보 수집
        try:
            for el in page.query_selector_all("iframe"):
                try:
                    f = el.content_frame()
                    if f:
                        candidates.append(f)
                except Exception:
                    pass
        except Exception:
            pass

        # (B) iframe이 없거나, iframe이 비정상이어도 main_frame이 실제 화면일 수 있음
        candidates.append(page.main_frame)

        # 중복 제거
        uniq: List[Frame] = []
        seen = set()
        for f in candidates:
            if id(f) not in seen:
                uniq.append(f)
                seen.add(id(f))
        candidates = uniq

        # 디버그 힌트용 url 샘플
        last_urls = []
        for f in candidates:
            try:
                last_urls.append(f.url or "")
            except Exception:
                last_urls.append("<err>")

        # 1) title 힌트 우선 (있으면)
        if title_keyword:
            for f in candidates:
                try:
                    if title_keyword in (f.title() or "") and _frame_has_any_text(f, FRAME_PROBE_TEXTS):
                        return f
                except Exception:
                    pass

        # 2) probe text로 확정
        for f in candidates:
            if _frame_has_any_text(f, ["Excel", "엑셀", "조회", "검색", "다운로드"]):
                return f

        time.sleep(0.5)

    raise RuntimeError(
        f"NGMS frame을 찾지 못했습니다. (candidates={len(candidates)}, urls_sample={last_urls[:5]})"
    )


def _safe_click_any(frame: Frame, texts: List[str], timeout: int = 30_000) -> None:
    last = None
    for t in texts:
        try:
            loc = frame.get_by_role("button", name=re.compile(re.escape(t), re.I))
            loc.first.wait_for(state="visible", timeout=timeout)
            loc.first.scroll_into_view_if_needed()
            loc.first.click(timeout=timeout)
            return
        except Exception as e:
            last = e

        # role=button이 안 잡히는 사이트 대비: text로도 한번 더
        try:
            loc = frame.get_by_text(t, exact=False)
            loc.first.wait_for(state="visible", timeout=timeout)
            loc.first.scroll_into_view_if_needed()
            loc.first.click(timeout=timeout)
            return
        except Exception as e:
            last = e

    raise RuntimeError(f"클릭 실패: {texts}, last={last}")


def _safe_search(frame: Frame) -> None:
    _safe_click_any(frame, ["조회", "검색", "Search"], timeout=60_000)


def _try_set_filter(frame: Frame, label: str, value: str) -> None:
    if not value:
        return

    # label 기반 시도
    try:
        frame.get_by_label(re.compile(label)).fill(value)
        frame.keyboard.press("Enter")
        return
    except Exception:
        pass

    # label 텍스트 클릭 -> 값 클릭(드롭다운 류)
    try:
        frame.get_by_text(label, exact=False).click()
        frame.get_by_text(value, exact=False).click()
        return
    except Exception:
        pass

    # 마지막: 값만 찾아 클릭
    try:
        frame.get_by_text(value, exact=False).click()
        return
    except Exception:
        pass

    raise RuntimeError(f"필터 설정 실패: {label}={value}")


def _download_excel(frame: Frame, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)

    btn_candidates = ["Excel 다운로드", "엑셀 다운로드", "Excel", "엑셀"]

    with frame.expect_download(timeout=180_000) as dlinfo:
        _safe_click_any(frame, btn_candidates, timeout=60_000)

    dl: Download = dlinfo.value
    out = download_dir / (dl.suggested_filename or "ngms.xlsx")
    dl.save_as(out)
    return out


def _collect(
    *,
    menu_no: str,
    frame_title: Optional[str],
    download_dir: Path,
    filters: Dict[str, str],
    link: str = DEFAULT_LINK,
) -> Tuple[pd.DataFrame, str]:
    hints: List[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        url = _build_url(menu_no, link=link)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=60_000)

        frame = _get_ngms_frame(page, frame_title)

        for label, value in filters.items():
            if not value:
                continue
            try:
                _try_set_filter(frame, label, value)
            except Exception as e:
                hints.append(f"{label}={value} 필터 실패: {e}")

        try:
            _safe_search(frame)
            page.wait_for_timeout(800)
        except Exception as e:
            hints.append(f"조회/검색 실패: {e}")

        excel = _download_excel(frame, download_dir)
        df = pd.read_excel(excel)

        ctx.close()
        browser.close()

    return df, "\n".join(hints)


# ===== 공개 함수들 =====
def collect_allocated(download_dir: Path, plan_period: str = "", designation_year: str = ""):
    return _collect(
        menu_no="50900501",
        frame_title="할당",
        download_dir=download_dir,
        filters={"계획기간": plan_period, "지정연도": designation_year},
        link=DEFAULT_LINK,
    )


def collect_target_mgmt(download_dir: Path, designation_year: str = ""):
    return _collect(
        menu_no="50900502",
        frame_title="목표",
        download_dir=download_dir,
        filters={"지정연도": designation_year},
        link=DEFAULT_LINK,
    )


def collect_statement_stats(download_dir: Path, emission_year: str = ""):
    return _collect(
        menu_no="50900503",
        frame_title="명세서",
        download_dir=download_dir,
        filters={"배출년도": emission_year},
        link=DEFAULT_LINK,
    )
