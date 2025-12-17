from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pandas as pd
from playwright.sync_api import sync_playwright, Page, Frame, Download


BASE_URL = "https://ngms.gir.go.kr/hom/subMain.do"

# 프레임을 "식별"하기 위한 후보 텍스트들 (페이지별로 일부가 없어도 됨)
FRAME_PROBE_TEXTS = [
    "Excel", "엑셀", "다운로드", "조회", "검색",
    "할당", "목표", "명세서", "배출량",
]


# =========================
# iframe / 공통 유틸
# =========================
def _iter_frames(page: Page) -> List[Frame]:
    # page.frames는 중첩 프레임까지 포함
    return list(page.frames)


def _frame_has_any_text(frame: Frame, patterns: List[str]) -> bool:
    # 프레임이 살아있고, 내부에 특정 텍스트가 존재하는지 "짧게" 탐색
    try:
        for t in patterns:
            loc = frame.get_by_text(t, exact=False)
            # is_visible(timeout=...)는 locator API에 없어서 wait_for로 확인
            try:
                loc.first.wait_for(state="attached", timeout=500)
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _get_ngms_frame(page: Page, title_keyword: Optional[str] = None) -> Frame:
    """
    1) DOM에서 iframe element를 먼저 잡는다.
    2) iframe.content_frame()으로 Frame 객체를 얻는다.
    3) title_keyword가 주어지면 우선적으로 그 프레임을 선호하되,
       최종적으로는 '프레임 안에 실제 UI 텍스트(Excel/조회 등)가 존재하는지'로 확정한다.
    """
    # iframe이 늦게 붙는 케이스가 많아서 길게 대기
    deadline = time.time() + 60

    last_frames = []
    while time.time() < deadline:
        try:
            # iframe DOM이 생겼는지부터 확인
            iframes = page.query_selector_all("iframe")
            candidates: List[Frame] = []

            for el in iframes:
                try:
                    f = el.content_frame()
                    if f:
                        candidates.append(f)
                except Exception:
                    pass

            # 혹시 content_frame으로 못 잡히는 경우 대비: page.frames도 병합
            merged = []
            seen = set()
            for f in (candidates + _iter_frames(page)):
                if id(f) not in seen:
                    merged.append(f)
                    seen.add(id(f))

            last_frames = merged

            # 1) title_keyword 우선
            if title_keyword:
                for f in merged:
                    try:
                        if title_keyword in (f.title() or ""):
                            # title이 맞더라도 내부에 텍스트가 전혀 없으면 아직 로딩중일 수 있음
                            if _frame_has_any_text(f, FRAME_PROBE_TEXTS):
                                return f
                    except Exception:
                        pass

            # 2) URL 힌트 (완전 의존 X)
            for f in merged:
                try:
                    u = (f.url or "")
                    if ("ngms" in u) or ("websquare" in u) or ("subMain" in u):
                        if _frame_has_any_text(f, FRAME_PROBE_TEXTS):
                            return f
                except Exception:
                    pass

            # 3) 최후: "실제 텍스트 존재 여부"로 찾기
            for f in merged:
                if _frame_has_any_text(f, ["Excel", "엑셀", "조회", "검색", "다운로드"]):
                    return f

        except Exception:
            pass

        time.sleep(0.5)

    # 디버깅용으로 어떤 프레임이 있었는지 힌트를 남기고 싶다면 여기서 로깅 추가 가능
    urls = []
    for f in last_frames:
        try:
            urls.append(f.url or "")
        except Exception:
            urls.append("<err>")
    raise RuntimeError(f"NGMS iframe을 찾지 못했습니다. (frames={len(last_frames)}, urls_sample={urls[:5]})")


def _safe_click_any(frame: Frame, selectors_or_texts: List[str], timeout=30_000) -> None:
    """
    주어진 후보들 중 하나라도 클릭되면 성공.
    후보 문자열에 따라:
      - "text=..." 형태면 그대로 locator
      - 그 외는 get_by_text로 처리
    """
    last = None
    for s in selectors_or_texts:
        try:
            if s.startswith("text="):
                loc = frame.locator(s)
            else:
                loc = frame.get_by_text(s, exact=False)

            loc.first.wait_for(state="visible", timeout=timeout)
            loc.first.scroll_into_view_if_needed()
            loc.first.click(timeout=timeout)
            return
        except Exception as e:
            last = e
    raise RuntimeError(f"클릭 실패: {selectors_or_texts}, last={last}")


def _safe_search(frame: Frame) -> None:
    _safe_click_any(frame, ["조회", "검색", "Search", "text=button:has-text('조회')"])


def _try_set_filter(frame: Frame, label: str, value: str) -> None:
    if not value:
        return

    # 1) label 기반 input/select 시도
    try:
        frame.get_by_label(re.compile(label)).fill(value)
        frame.keyboard.press("Enter")
        return
    except Exception:
        pass

    # 2) 그냥 값 텍스트 클릭 (드롭다운이 열려있다는 가정)
    try:
        frame.get_by_text(value, exact=True).click()
        return
    except Exception:
        pass

    # 3) label 텍스트 근처 컴포넌트 클릭 시도(보수적)
    try:
        frame.get_by_text(label, exact=False).click()
        time.sleep(0.2)
        frame.get_by_text(value, exact=False).click()
        return
    except Exception:
        pass

    raise RuntimeError(f"필터 설정 실패: {label}={value}")


def _download_excel(frame: Frame, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)

    # 버튼 텍스트가 화면/기간별로 미세하게 다를 수 있어 후보를 넓힘
    download_btn_candidates = [
        "Excel 다운로드",
        "엑셀 다운로드",
        "Excel",
        "엑셀",
    ]

    with frame.expect_download(timeout=180_000) as dlinfo:
        _safe_click_any(frame, download_btn_candidates, timeout=60_000)

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
) -> Tuple[pd.DataFrame, str]:
    hints = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()

        page.goto(f"{BASE_URL}?menuNo={menu_no}", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=60_000)

        frame = _get_ngms_frame(page, frame_title)

        # 필터 적용
        for label, value in filters.items():
            if not value:
                continue
            try:
                _try_set_filter(frame, label, value)
            except Exception as e:
                hints.append(f"{label} 필터 실패({value}): {e}")

        # 조회/검색
        try:
            _safe_search(frame)
            # 조회 후 렌더 대기
            page.wait_for_timeout(800)
        except Exception as e:
            hints.append(f"조회 버튼 미확인/실패: {e}")

        excel = _download_excel(frame, download_dir)
        df = pd.read_excel(excel)

        ctx.close()
        browser.close()

    return df, "\n".join(hints)


# =========================
# 공개 수집 함수 3종
# =========================
def collect_allocated(download_dir: Path, plan_period: str = "", designation_year: str = ""):
    return _collect(
        menu_no="50900501",
        frame_title="할당",  # title에 정확히 안 잡혀도 됨(힌트용)
        download_dir=download_dir,
        filters={
            "계획기간": plan_period,
            "지정연도": designation_year,
        },
    )


def collect_target_mgmt(download_dir: Path, designation_year: str = ""):
    return _collect(
        menu_no="50900502",
        frame_title="목표",  # 힌트용
        download_dir=download_dir,
        filters={
            "지정연도": designation_year,
        },
    )


def collect_statement_stats(download_dir: Path, emission_year: str = ""):
    return _collect(
        menu_no="50900503",
        frame_title="명세서",  # 힌트용
        download_dir=download_dir,
        filters={
            "배출년도": emission_year,
        },
    )
