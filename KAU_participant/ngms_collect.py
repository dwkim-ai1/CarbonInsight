# KAU_participant/ngms_collect.py
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple, List, Union

import pandas as pd
from playwright.sync_api import Page, Frame, TimeoutError as PWTimeoutError


# =========
# Config
# =========

NGMS_HOST = "https://ngms.gir.go.kr:8443"

# 네가 제공한 subMain.do 구조(탭 + iframe) 기준
SUBMAIN_URL_TEMPLATE = (
    NGMS_HOST + "/subMain.do?link={link_xml}&menuNo={menu_no}"
)

# subMain.do 안 iframe src는 /websquare/ngms.do?w2xPath=...&menuNo=... 형태
IFRAME_ID_TEMPLATE = "mf_tac_layout_contents_{menu_no}_body"


@dataclass(frozen=True)
class CollectResult:
    df: pd.DataFrame
    hint: str
    downloaded_path: Path


# =========
# Small utils
# =========

def _ensure_dir(p: Union[str, Path]) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_filename(name: str) -> str:
    # Windows/Unix 안전 문자만 남김
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip()
    return name or "download.xlsx"


def _unique_path(dir_path: Path, filename: str) -> Path:
    base = _safe_filename(filename)
    path = dir_path / base
    if not path.exists():
        return path
    stem = path.stem
    suf = path.suffix
    for i in range(1, 10_000):
        cand = dir_path / f"{stem} ({i}){suf}"
        if not cand.exists():
            return cand
    # 마지막 폴백
    return dir_path / f"{stem}_{int(time.time())}{suf}"


def _dismiss_blocking_layers(page: Page) -> None:
    """
    subMain HTML에 등장하는 floating layer(예: 권한선택)가 뜨면
    다운로드 버튼 클릭을 막을 수 있어서, 일단 닫기 시도.
    """
    # 1) 고정 id로 닫기 (HTML에 mf_floatingLayer1_closeButton 존재)
    try:
        close_btn = page.locator("#mf_floatingLayer1_closeButton")
        if close_btn.count() > 0 and close_btn.first.is_visible():
            close_btn.first.click(timeout=1500)
    except Exception:
        pass

    # 2) '권한선택' 타이틀 영역이 보이면 주변 close 버튼 클릭 폴백
    try:
        title = page.get_by_text("권한선택", exact=True)
        if title.count() > 0 and title.first.is_visible():
            # 가까운 컨테이너 내 close 성격 요소 탐색
            container = title.first.locator("xpath=ancestor-or-self::*[contains(@class,'w2floatingLayer')][1]")
            cand = container.locator("[title='close'], .w2floatingLayer_close_button")
            if cand.count() > 0:
                cand.first.click(timeout=1500)
    except Exception:
        pass


def _wait_dom_settle(page: Page, timeout_ms: int = 30_000) -> None:
    """
    WebSquare 페이지는 'networkidle'만으로는 부족할 때가 많아서
    load/domcontentloaded + 짧은 settle sleep을 섞는다.
    """
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_load_state("load", timeout=timeout_ms)
    except Exception:
        pass
    time.sleep(0.2)


# =========
# Iframe resolution
# =========

def _get_ngms_frame(page: Page, menu_no: str, w2x_path: Optional[str] = None, timeout_ms: int = 60_000) -> Frame:
    """
    네가 제공한 subMain HTML 기준:
      iframe id = mf_tac_layout_contents_{menuNo}_body

    1) id 직접 매칭 (가장 안정)
    2) 실패 시: page.frames()에서 url에 menuNo / w2xPath 포함 프레임 탐색 (폴백)
    """
    deadline = time.time() + (timeout_ms / 1000.0)

    iframe_id = IFRAME_ID_TEMPLATE.format(menu_no=menu_no)
    css = f"iframe#{iframe_id}"

    last_frames: List[Frame] = []
    last_urls: List[str] = []

    while time.time() < deadline:
        _dismiss_blocking_layers(page)

        # 1) iframe id로 selector 대기 후 frame 획득
        try:
            page.wait_for_selector(css, timeout=1500)
            el = page.query_selector(css)
            if el:
                fr = el.content_frame()
                if fr:
                    return fr
        except Exception:
            pass

        # 2) 폴백: 프레임 URL 기반 탐색
        try:
            last_frames = page.frames
            last_urls = [f.url for f in last_frames]
            for fr in last_frames:
                u = fr.url or ""
                if "ngms.do" not in u:
                    continue
                if menu_no and f"menuNo={menu_no}" not in u:
                    continue
                if w2x_path and (("w2xPath=" + w2x_path) not in u):
                    continue
                return fr
        except Exception:
            pass

        time.sleep(0.2)

    raise RuntimeError(
        f"NGMS iframe을 찾지 못했습니다. "
        f"(menuNo={menu_no}, frames={len(last_frames)}, urls_sample={last_urls[:5]})"
    )


# =========
# Download
# =========

def _click_excel_download(page: Page, frame: Frame, timeout_ms: int = 30_000) -> None:
    """
    'Excel 다운로드' UI는 role/button이 아닐 수도 있어 여러 후보를 폴백으로 클릭.
    """
    candidates = [
        # 1) role 기준 (기존 코드의 의도)
        lambda: frame.get_by_role("button", name=re.compile(r"Excel\s*다운로드")),
        # 2) 텍스트 기반 (a, button 등 혼재 가능)
        lambda: frame.locator("text=/^\\s*Excel\\s*다운로드\\s*$/"),
        lambda: frame.locator("a:has-text('Excel')").locator("text=다운로드"),
        # 3) title/alt 등 속성 기반 폴백
        lambda: frame.locator("[title*='Excel'][title*='다운로드']"),
        lambda: frame.locator("[alt*='Excel'][alt*='다운로드']"),
        # 4) 흔한 클래스/아이콘 계열 폴백(있으면 잡힘)
        lambda: frame.locator("button:has-text('Excel')"),
        lambda: frame.locator("a:has-text('Excel')"),
    ]

    _dismiss_blocking_layers(page)
    _wait_dom_settle(page, timeout_ms=timeout_ms)

    last_err: Optional[Exception] = None
    for make in candidates:
        try:
            loc = make()
            if loc.count() == 0:
                continue
            # WebSquare는 화면에 보이기 전/가려진 상태가 많아서 "visible"을 한번 더 확인
            loc.first.wait_for(state="visible", timeout=2000)
            loc.first.click(timeout=timeout_ms)
            return
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"'Excel 다운로드' 버튼을 찾거나 클릭하지 못했습니다. last_err={last_err!r}")


def _download_excel(page: Page, frame: Frame, download_dir: Union[str, Path], timeout_ms: int = 180_000) -> Path:
    """
    중요: Frame에는 expect_download가 없음.
    => 반드시 page.expect_download()로 감싸고, 클릭은 frame locator로 수행.
    """
    download_dir = _ensure_dir(download_dir)

    _dismiss_blocking_layers(page)

    with page.expect_download(timeout=timeout_ms) as dlinfo:
        _click_excel_download(page, frame, timeout_ms=30_000)

    dl = dlinfo.value
    suggested = dl.suggested_filename or "ngms.xlsx"
    save_path = _unique_path(download_dir, suggested)
    dl.save_as(str(save_path))
    return save_path


# =========
# Read Excel
# =========

def _read_excel(path: Union[str, Path]) -> pd.DataFrame:
    """
    NGMS 엑셀은 시트/헤더가 매번 다를 수 있어, 일단 1시트 기본으로 읽고
    빈 행/열 정리 정도만 한다.
    """
    path = Path(path)
    df = pd.read_excel(path)  # 기본: 첫 시트, 첫 행을 header로
    # 가벼운 정리
    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.to_series().astype(str).str.match(r"^Unnamed")]
    return df


# =========
# Core collect
# =========

def _collect(
    page: Page,
    *,
    menu_no: str,
    link_xml: str,
    download_dir: Union[str, Path],
    w2x_path: Optional[str] = None,
    before_download: Optional[Callable[[Page, Frame], None]] = None,
    timeout_ms: int = 90_000,
) -> CollectResult:
    """
    1) subMain.do (탭/iframe 있는 페이지)로 이동
    2) iframe 획득
    3) (선택) before_download로 조건 설정(기간/년도 선택 등)
    4) Excel 다운로드
    5) 엑셀 읽어 df 반환
    """
    url = SUBMAIN_URL_TEMPLATE.format(link_xml=link_xml, menu_no=menu_no)
    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    _wait_dom_settle(page, timeout_ms=timeout_ms)
    _dismiss_blocking_layers(page)

    frame = _get_ngms_frame(page, menu_no=menu_no, w2x_path=w2x_path, timeout_ms=timeout_ms)

    if before_download:
        try:
            before_download(page, frame)
        except Exception:
            # before_download는 환경에 따라 실패할 수 있으니 다운로드는 계속 시도
            pass

    excel_path = _download_excel(page, frame, download_dir=download_dir, timeout_ms=180_000)
    df = _read_excel(excel_path)

    hint = f"menuNo={menu_no}, link={link_xml}, file={excel_path.name}, rows={len(df):,}, cols={len(df.columns):,}"
    return CollectResult(df=df, hint=hint, downloaded_path=excel_path)


# =========
# Public APIs (각 메뉴 수집)
# =========

def collect_allocated(
    page: Page,
    download_dir: Union[str, Path],
    plan_period: Optional[str] = None,
    designation_year: Optional[str] = None,
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 할당대상업체 (menuNo=50900501, /hom/bbs/OGCMBBS021V.xml)
    """
    def _before(page: Page, frame: Frame) -> None:
        # NOTE:
        # 네가 준 HTML은 "외부 subMain"까지만이라, 내부 iframe(WebSquare 화면)의
        # 실제 select id/name은 여기서 확정할 수 없음.
        # 대신, 가능한 범용 패턴으로 '기간/년도'가 보이면 잡고, 실패해도 다운로드는 진행.
        _dismiss_blocking_layers(page)

        # plan_period / designation_year가 있으면 "보이는 combobox/select"에서 라벨 매칭 시도
        if plan_period:
            _try_select_any(frame, keyword_regex=r"(계획|기간|plan)", value=plan_period)
        if designation_year:
            _try_select_any(frame, keyword_regex=r"(지정|연도|년도|year)", value=designation_year)

        # 조회 버튼류 폴백 클릭(있으면 반영)
        _try_click_any(frame, [
            r"조회", r"검색", r"Search", r"확인"
        ])

        time.sleep(0.2)

    result = _collect(
        page,
        menu_no="50900501",
        link_xml="/hom/bbs/OGCMBBS021V.xml",
        w2x_path="/hom/bbs/OGCMBBS021V.xml",
        download_dir=download_dir,
        before_download=_before,
    )
    return result.df, result.hint


def collect_target_managed(
    page: Page,
    download_dir: Union[str, Path],
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 목표관리대상업체 (menuNo=50900502, /hom/bbs/OGCMBBS022V.xml)
    """
    result = _collect(
        page,
        menu_no="50900502",
        link_xml="/hom/bbs/OGCMBBS022V.xml",
        w2x_path="/hom/bbs/OGCMBBS022V.xml",
        download_dir=download_dir,
    )
    return result.df, result.hint


def collect_statement_stats(
    page: Page,
    download_dir: Union[str, Path],
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 명세서배출량통계 (menuNo=50900503, /hom/bbs/OGCMBBS023V.xml)
    """
    result = _collect(
        page,
        menu_no="50900503",
        link_xml="/hom/bbs/OGCMBBS023V.xml",
        w2x_path="/hom/bbs/OGCMBBS023V.xml",
        download_dir=download_dir,
    )
    return result.df, result.hint


def collect_public_stats(
    page: Page,
    download_dir: Union[str, Path],
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 공공부문 배출량통계 (menuNo=50900504, /hom/bbs/OGCMBBS026V.xml)
    """
    result = _collect(
        page,
        menu_no="50900504",
        link_xml="/hom/bbs/OGCMBBS026V.xml",
        w2x_path="/hom/bbs/OGCMBBS026V.xml",
        download_dir=download_dir,
    )
    return result.df, result.hint


# =========
# Generic helpers for "unknown inner DOM" (safe fallbacks)
# =========

def _try_select_any(frame: Frame, keyword_regex: str, value: str) -> bool:
    """
    내부 iframe 화면의 select/combobox가 어떤 id/name인지 모를 때,
    'label/text 근처' 또는 '모든 select'를 훑어 label/value 매칭 시도.
    실패해도 예외 던지지 않고 False 반환.
    """
    try:
        # 1) select 태그 직접 훑기
        sels = frame.locator("select")
        n = min(sels.count(), 30)
        for i in range(n):
            s = sels.nth(i)
            try:
                # 주변 텍스트에 키워드가 있으면 우선
                around = s.locator("xpath=ancestor-or-self::*[1]/..")
                txt = (around.inner_text(timeout=300) or "").strip()
                if re.search(keyword_regex, txt, re.IGNORECASE):
                    _select_option_best_effort(s, value)
                    return True
            except Exception:
                continue

        # 2) 아무 select나 label로 맞으면 선택
        for i in range(n):
            s = sels.nth(i)
            try:
                _select_option_best_effort(s, value)
                return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def _select_option_best_effort(select_locator, value: str) -> None:
    """
    label/value/text 여러 방식으로 select_option 시도.
    """
    # label 우선
    try:
        select_locator.select_option(label=value, timeout=800)
        return
    except Exception:
        pass
    # value 우선
    try:
        select_locator.select_option(value=value, timeout=800)
        return
    except Exception:
        pass
    # 부분일치: 옵션 텍스트 중 포함되는 것 찾기
    opts = select_locator.locator("option")
    m = min(opts.count(), 500)
    for i in range(m):
        o = opts.nth(i)
        t = (o.inner_text(timeout=200) or "").strip()
        if value in t:
            v = o.get_attribute("value")
            if v is not None:
                select_locator.select_option(value=v, timeout=800)
                return
    raise RuntimeError(f"select_option 실패: {value}")


def _try_click_any(frame: Frame, patterns: List[str]) -> bool:
    """
    조회/검색 버튼 등이 있을 때 눌러서 조건 반영을 유도.
    """
    for pat in patterns:
        try:
            loc = frame.locator(f"text=/{pat}/")
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=1500)
                return True
        except Exception:
            continue
    return False
