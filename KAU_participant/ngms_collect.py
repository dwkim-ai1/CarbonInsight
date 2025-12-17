# KAU_participant/ngms_collect.py
from __future__ import annotations

import os
import re
import time
import glob
import uuid
import shutil
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple, List

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Browser,
    BrowserContext,
    Download,
    TimeoutError as PlaywrightTimeoutError,
)

# =========================
# Public API (run_ngms.py가 import하는 함수들)
# =========================

def collect_allocated(
    download_dir: str,
    plan_period: str = "",
    designation_year: str = "",
    base_url: str = "https://ngms.gir.go.kr:8443",
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 할당대상업체 (menuNo=50900501 / w2xPath=/hom/bbs/OGCMBBS021V.xml)
    - 현재: UI 필터(계획기간/지정연도 등)를 '확정'해서 조작하지 않고,
      'Excel 다운로드'만 최대한 안정적으로 받아서 읽는다.
    """
    return _collect_excel(
        key="allocated",
        menu_no="50900501",
        w2x_path="/hom/bbs/OGCMBBS021V.xml",
        download_dir=download_dir,
        base_url=base_url,
        hint_extra=_hint_kv(plan_period=plan_period, designation_year=designation_year),
    )


def collect_target_mgmt(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str = "https://ngms.gir.go.kr:8443",
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 목표관리대상업체 (menuNo=50900502 / w2xPath=/hom/bbs/OGCMBBS022V.xml)
    """
    return _collect_excel(
        key="target_mgmt",
        menu_no="50900502",
        w2x_path="/hom/bbs/OGCMBBS022V.xml",
        download_dir=download_dir,
        base_url=base_url,
        hint_extra=_hint_kv(plan_period=plan_period, emission_year=emission_year),
    )


def collect_spec_emission_stats(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str = "https://ngms.gir.go.kr:8443",
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 명세서배출량통계 (menuNo=50900503 / w2xPath=/hom/bbs/OGCMBBS023V.xml)
    """
    return _collect_excel(
        key="spec_emission_stats",
        menu_no="50900503",
        w2x_path="/hom/bbs/OGCMBBS023V.xml",
        download_dir=download_dir,
        base_url=base_url,
        hint_extra=_hint_kv(plan_period=plan_period, emission_year=emission_year),
    )


def collect_public_sector_stats(
    download_dir: str,
    plan_period: str = "",
    emission_year: str = "",
    base_url: str = "https://ngms.gir.go.kr:8443",
) -> Tuple[pd.DataFrame, str]:
    """
    정보공개 > 공공부문 배출량통계 (menuNo=50900504 / w2xPath=/hom/bbs/OGCMBBS026V.xml)
    """
    return _collect_excel(
        key="public_sector_stats",
        menu_no="50900504",
        w2x_path="/hom/bbs/OGCMBBS026V.xml",
        download_dir=download_dir,
        base_url=base_url,
        hint_extra=_hint_kv(plan_period=plan_period, emission_year=emission_year),
    )


# =========================
# Internal config / helpers
# =========================

EXCEL_BTN_RE = re.compile(r"Excel\s*다운로드", re.I)

@dataclass(frozen=True)
class CollectResult:
    file_path: Path
    df: pd.DataFrame
    hint: str


def _hint_kv(**kwargs: str) -> str:
    items = []
    for k, v in kwargs.items():
        v = (v or "").strip()
        if v:
            items.append(f"{k}={v}")
    return ", ".join(items)


def _ensure_dir(p: str | Path) -> Path:
    path = Path(p).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_tag() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _is_probably_excel(path: Path) -> bool:
    # 확장자 기준 1차 판단
    ext = path.suffix.lower()
    if ext in (".xlsx", ".xls"):
        return True
    # 일부 서버는 확장자 없이 내려주기도 해서, 앞부분 매직만 최소 체크
    try:
        b = path.read_bytes()[:8]
    except Exception:
        return False
    # xlsx: PK..
    if len(b) >= 2 and b[0:2] == b"PK":
        return True
    # xls: D0 CF 11 E0 ...
    if b.startswith(b"\xD0\xCF\x11\xE0"):
        return True
    return False


def _read_excel_robust(path: Path) -> pd.DataFrame:
    """
    파일이 HTML/오류 페이지로 내려온 경우를 최대한 빨리 식별해서
    '엑셀로 못 읽는다'를 명확히 터뜨리기.
    """
    if not _is_probably_excel(path):
        # 디버그를 위해 앞부분만 남김(너무 길게는 금지)
        try:
            head = path.read_text(errors="ignore")[:500]
        except Exception:
            head = "<cannot read as text>"
        raise RuntimeError(
            f"다운로드 파일이 엑셀 형식이 아닙니다: {path.name}\n"
            f"(확장자={path.suffix!r})\n"
            f"--- file head (first 500 chars) ---\n{head}"
        )

    # 기본은 첫 시트 전체 로드
    return pd.read_excel(path)


def _list_files(download_dir: Path) -> List[Path]:
    return sorted([p for p in download_dir.glob("*") if p.is_file()], key=lambda x: x.stat().st_mtime)


def _latest_new_file(download_dir: Path, since_ts: float) -> Optional[Path]:
    candidates = []
    for p in _list_files(download_dir):
        try:
            if p.stat().st_mtime >= since_ts:
                candidates.append(p)
        except FileNotFoundError:
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.stat().st_mtime)


def _safe_unique_name(key: str, suggested: str) -> str:
    suggested = (suggested or "").strip()
    if not suggested:
        suggested = f"{key}.xlsx"
    base, ext = os.path.splitext(suggested)
    if not ext:
        ext = ".xlsx"
    # 항상 유니크하게(덮어쓰기 방지)
    return f"{key}_{_now_tag()}_{uuid.uuid4().hex[:8]}{ext}"


def _make_menu_url(base_url: str, w2x_path: str, menu_no: str) -> str:
    # 사용자가 제공한 형태를 그대로 유지
    # https://ngms.gir.go.kr:8443/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501
    return f"{base_url}/subMain.do?link={w2x_path}&menuNo={menu_no}"


# =========================
# Playwright core
# =========================

def _collect_excel(
    key: str,
    menu_no: str,
    w2x_path: str,
    download_dir: str,
    base_url: str,
    hint_extra: str = "",
    total_timeout_sec: int = 300,
) -> Tuple[pd.DataFrame, str]:
    """
    - menu URL을 직접 열고
    - iframe(#mf_tac_layout_contents_{menu_no}_body)로 진입
    - "Excel 다운로드"를 클릭
    - 다운로드 이벤트는 page/context 레벨로 먼저 잡고,
      실패하면 디렉토리 폴링으로 fallback
    """
    download_path = _ensure_dir(download_dir)

    t0 = time.time()
    hint_parts = [f"key={key}", f"menuNo={menu_no}", f"w2xPath={w2x_path}"]
    if hint_extra:
        hint_parts.append(hint_extra)
    hint = " | ".join(hint_parts)

    pw = None
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # CI에서 무한정 기다리는 것 방지
        page.set_default_timeout(60_000)
        page.set_default_navigation_timeout(60_000)

        url = _make_menu_url(base_url, w2x_path, menu_no)
        page.goto(url, wait_until="domcontentloaded")

        # iframe이 늦게 붙는 케이스 대응
        iframe_sel = f"#mf_tac_layout_contents_{menu_no}_body"
        page.wait_for_selector(iframe_sel, timeout=60_000)

        frame = _get_iframe_content_frame(page, iframe_sel)

        # 다운로드는 "page/context 레벨"에서 먼저 잡는다(프레임은 expect_download 없음)
        saved = _download_excel_with_fallback(
            page=page,
            context=context,
            frame=frame,
            download_dir=download_path,
            key=key,
            total_deadline=t0 + total_timeout_sec,
        )

        df = _read_excel_robust(saved)
        return df, hint

    finally:
        # 무조건 종료(“계속 실행 중” 방지)
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        try:
            if pw is not None:
                pw.stop()
        except Exception:
            pass


def _get_iframe_content_frame(page: Page, iframe_selector: str):
    """
    locator/FrameLocator 혼동으로 생기는
    - TypeError: 'FrameLocator' object is not callable
    같은 오류를 피하기 위해,
    항상 element_handle() -> content_frame()로 접근.
    """
    el = page.locator(iframe_selector).element_handle(timeout=30_000)
    if el is None:
        raise RuntimeError(f"iframe element를 찾지 못했습니다: {iframe_selector}")
    fr = el.content_frame()
    if fr is None:
        raise RuntimeError(f"iframe content_frame을 얻지 못했습니다: {iframe_selector}")
    return fr


def _download_excel_with_fallback(
    page: Page,
    context: BrowserContext,
    frame,
    download_dir: Path,
    key: str,
    total_deadline: float,
) -> Path:
    """
    안정화 핵심:
    1) 다운로드 이벤트는 frame이 아니라 page/context 레벨에서 수집
    2) click은 가능한 '한 번'만 수행 (이벤트 못 잡았다고 중복 클릭하지 않기)
    3) 그래도 못 잡으면 디렉토리 변화 폴링으로 fallback
    """
    # 이벤트 수집 버퍼
    downloads: List[Download] = []

    def _on_download(d: Download):
        downloads.append(d)

    page.on("download", _on_download)
    context.on("download", _on_download)

    # 클릭 전 디렉토리 상태/시간 기록(폴링 fallback용)
    click_ts = time.time()
    before_names = {p.name for p in _list_files(download_dir)}

    # 버튼 찾기 + 1회 클릭
    btn = _find_excel_download_clickable(frame)
    _safe_click(btn)

    # 1) 이벤트 기반 대기 (추가 클릭 없음)
    dl = _wait_download_event(downloads, deadline=min(total_deadline, click_ts + 180))
    if dl is not None:
        saved = _save_download(dl, download_dir, key)
        return saved

    # 2) 이벤트를 못 잡은 케이스: 디렉토리 폴링
    polled = _poll_new_file(download_dir, before_names, deadline=min(total_deadline, click_ts + 180))
    if polled is not None:
        # 파일명이 애매하면 유니크명으로 리네임(후처리 안정)
        renamed = _ensure_unique_file(polled, download_dir, key)
        return renamed

    raise RuntimeError("Excel 다운로드를 감지하지 못했습니다 (event + directory polling 모두 실패).")


def _find_excel_download_clickable(frame):
    """
    WebSquare/커스텀 UI 대응:
    - role=button일 수도 있고
    - <a> / <span> / <div role=button> 등 다양
    """
    candidates = []

    # 1) 접근성 role 기반
    try:
        candidates.append(frame.get_by_role("button", name=EXCEL_BTN_RE))
    except Exception:
        pass

    # 2) 텍스트 기반(가장 범용)
    candidates.append(frame.locator("text=/Excel\\s*다운로드/i"))

    # 3) 태그 기반 + 텍스트 필터
    candidates.append(frame.locator("button,a,[role='button'],span,div").filter(has_text=EXCEL_BTN_RE))

    # 실제로 클릭 가능한 첫 번째를 선정
    for loc in candidates:
        try:
            if loc.count() > 0:
                return loc.first
        except Exception:
            # 일부 locator는 count에서 튀는 경우가 있어 보호
            continue

    raise RuntimeError("iframe 내에서 'Excel 다운로드' 버튼(또는 클릭 요소)을 찾지 못했습니다.")


def _safe_click(locator):
    """
    클릭 안정화:
    - 화면 밖이면 스크롤
    - overlay/렌더 지연 고려해서 약간의 재시도
    """
    last_err = None
    for _ in range(3):
        try:
            locator.scroll_into_view_if_needed(timeout=10_000)
        except Exception:
            pass
        try:
            locator.click(timeout=20_000)
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"Excel 다운로드 클릭 실패: {last_err}")


def _wait_download_event(downloads: List[Download], deadline: float) -> Optional[Download]:
    while time.time() < deadline:
        if downloads:
            return downloads.pop(0)
        time.sleep(0.2)
    return None


def _save_download(dl: Download, download_dir: Path, key: str) -> Path:
    suggested = ""
    try:
        suggested = dl.suggested_filename
    except Exception:
        suggested = ""
    filename = _safe_unique_name(key, suggested)
    out = download_dir / filename
    dl.save_as(str(out))
    return out


def _poll_new_file(download_dir: Path, before_names: set, deadline: float) -> Optional[Path]:
    """
    이벤트를 못 잡았더라도 파일이 실제로 떨어졌을 가능성이 있으므로,
    디렉토리 변화로 탐지.
    """
    while time.time() < deadline:
        files = _list_files(download_dir)
        for p in reversed(files):
            if p.name not in before_names:
                return p
        time.sleep(0.5)
    return None


def _ensure_unique_file(path: Path, download_dir: Path, key: str) -> Path:
    """
    서버가 같은 파일명으로 내려줘서 덮어쓰는 케이스 방지.
    이미 유니크해 보이면 그대로 사용.
    """
    name = path.name
    if name.startswith(f"{key}_") and re.search(r"_[0-9a-f]{8}\.", name):
        return path
    new_name = _safe_unique_name(key, path.name)
    new_path = download_dir / new_name
    try:
        shutil.move(str(path), str(new_path))
        return new_path
    except Exception:
        # move 실패 시 원본 사용(최후 보루)
        return path
