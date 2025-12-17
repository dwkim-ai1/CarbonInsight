# KAU_participant/ngms_collect.py
"""
NGMS(WebSquare) 수집 모듈 (Playwright, iframe 대응 완성본)

핵심:
- subMain.do 는 껍데기이고 실제 화면은 iframe(/websquare/ngms.do) 안에 렌더링됨
- 따라서 모든 조작(조회/필터/엑셀다운로드/테이블)은 반드시 frame 컨텍스트에서 수행해야 함

주의:
- NGMS 화면은 WebSquare 기반이라 "label로 콤보 찾기"가 항상 통하지 않습니다.
  그래서 '여러 전략'을 순차적으로 시도하는 방식으로 안정화했습니다.
"""

from __future__ import annotations

import re
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Page,
    Frame,
    Download,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)

# 프로젝트에서 이미 쓰고 있을 가능성이 높아서 기본 URL은 상수로 두되,
# 필요 시 외부(run_ngms.py)에서 import 해서 사용/수정할 수 있게 해둠.
DEFAULT_NGMS_ENTRY_URL = "https://ngms.gir.go.kr/hom/subMain.do?menuNo=50900501"


@dataclass(frozen=True)
class CollectResult:
    df: pd.DataFrame
    hint: str
    excel_path: Path


# -----------------------------
# Frame / Locator Utilities
# -----------------------------
def _get_ngms_frame(page: Page, *, title_keyword: Optional[str] = None) -> Frame:
    """
    subMain.do 안에서 실제 콘텐츠가 있는 NGMS iframe을 찾아 반환한다.
    - title_keyword가 있으면 title 우선
    - 없거나 실패하면 url에 'websquare/ngms.do' 포함 여부로 탐색
    """
    # 프레임 로딩을 약간 기다림 (networkidle만으로는 iframe 내부 렌더가 늦을 때가 있음)
    deadline = time.time() + 15
    last_err: Optional[Exception] = None

    while time.time() < deadline:
        try:
            frames = page.frames
            # 1) title keyword
            if title_keyword:
                for f in frames:
                    try:
                        if f.title() and title_keyword in f.title():
                            return f
                    except Exception:
                        pass

            # 2) url pattern
            for f in frames:
                try:
                    if f.url and "websquare/ngms.do" in f.url:
                        return f
                except Exception:
                    pass

        except Exception as e:
            last_err = e

        time.sleep(0.2)

    if last_err:
        raise RuntimeError(f"NGMS iframe 탐색 중 오류: {last_err}") from last_err
    raise RuntimeError("NGMS iframe을 찾지 못했습니다. (title/url 기준 모두 실패)")


def _click_first(frame: Frame, candidates: list[Tuple[str, Dict[str, Any]]], timeout_ms: int = 30_000) -> None:
    """
    여러 locator 전략을 순서대로 시도해 첫 번째로 클릭 성공하는 것을 사용.
    candidates: [("role", {"role":"button","name":...}), ("text", {"text":"Excel 다운로드"}) ...]
    """
    last_exc: Optional[Exception] = None
    for kind, kw in candidates:
        try:
            if kind == "role":
                loc = frame.get_by_role(kw["role"], name=kw.get("name"))
            elif kind == "text":
                loc = frame.get_by_text(kw["text"], exact=kw.get("exact", False))
            elif kind == "css":
                loc = frame.locator(kw["selector"])
            else:
                raise ValueError(f"Unknown locator kind: {kind}")

            loc.first.wait_for(state="visible", timeout=timeout_ms)
            loc.first.click(timeout=timeout_ms)
            return
        except Exception as e:
            last_exc = e

    raise RuntimeError(f"클릭 실패: 모든 locator 후보가 실패했습니다. last={last_exc}") from last_exc


def _try_set_combobox_by_label(frame: Frame, label_text: str, value: str, timeout_ms: int = 10_000) -> bool:
    """
    WebSquare 화면에서 '콤보/셀렉트'를 세팅하려고 여러 방식을 시도.
    성공하면 True, 실패하면 False.
    """
    if value is None or str(value).strip() == "":
        return True  # 빈 값이면 세팅 스킵을 성공으로 간주

    value = str(value).strip()

    # 전략 A) label 기반으로 input/combobox 잡기 (통하면 가장 깔끔)
    try:
        # label이 실제 <label>이 아닐 수 있어서, "라벨 텍스트 근처"를 잡는 방식도 병행
        # 1) get_by_label
        loc = frame.get_by_label(re.compile(re.escape(label_text)))
        loc.wait_for(state="attached", timeout=timeout_ms)
        # select/input 둘 다 대응
        try:
            loc.select_option(label=value, timeout=timeout_ms)  # type: ignore
            return True
        except Exception:
            pass
        try:
            loc.fill(value, timeout=timeout_ms)  # type: ignore
            loc.press("Enter", timeout=timeout_ms)  # type: ignore
            return True
        except Exception:
            pass
    except Exception:
        pass

    # 전략 B) '라벨 텍스트'가 들어간 영역에서 가까운 select 찾기
    try:
        # label_text가 들어간 요소 주변에서 select를 탐색
        label_like = frame.get_by_text(label_text, exact=False).first
        label_like.wait_for(state="visible", timeout=timeout_ms)

        # DOM 구조가 다양하므로, ancestor 범위 넓게 잡고 select/input 후보를 찾는다.
        container = label_like.locator("xpath=ancestor-or-self::*[1]")
        # 주변으로 몇 단계 올라가며 찾기
        for up in range(1, 6):
            scope = label_like.locator(f"xpath=ancestor::*[{up}]")
            selects = scope.locator("select")
            if selects.count() > 0:
                selects.first.select_option(label=value)
                return True

            # WebSquare가 input + popup(listbox) 형태일 수도 있음
            inputs = scope.locator("input")
            if inputs.count() > 0:
                inp = inputs.first
                try:
                    inp.click()
                    inp.fill(value)
                    inp.press("Enter")
                    return True
                except Exception:
                    pass
    except Exception:
        pass

    # 전략 C) combo/option 목록이 열리는 UI 가정: value 텍스트를 직접 클릭
    try:
        # 콤보가 이미 열려있거나, Enter로 열렸을 때 목록에서 value를 선택
        option = frame.get_by_role("option", name=re.compile(re.escape(value)))
        option.first.click(timeout=timeout_ms)
        return True
    except Exception:
        pass

    try:
        option2 = frame.get_by_text(value, exact=True)
        option2.first.click(timeout=timeout_ms)
        return True
    except Exception:
        pass

    return False


def _safe_click_search(frame: Frame, timeout_ms: int = 30_000) -> None:
    """
    조회 버튼 클릭: 화면별로 '조회', '검색', 'Search' 등 다양할 수 있어 다중 후보로 처리.
    """
    _click_first(
        frame,
        candidates=[
            ("role", {"role": "button", "name": re.compile(r"^\s*조회\s*$")}),
            ("role", {"role": "button", "name": re.compile(r"^\s*검색\s*$")}),
            ("role", {"role": "button", "name": re.compile(r"^\s*Search\s*$", re.I)}),
            ("text", {"text": "조회", "exact": True}),
            ("text", {"text": "검색", "exact": True}),
        ],
        timeout_ms=timeout_ms,
    )


def _wait_table_rendered(frame: Frame, timeout_ms: int = 30_000) -> None:
    """
    조회 후 테이블이 렌더링될 때까지 대기.
    화면마다 테이블이 다르므로 느슨하게 기다린다.
    """
    # 흔히 grid/table이 렌더되면 'tbody tr' 또는 'w2grid' 류가 생김
    candidates = [
        ("css", {"selector": "table tbody tr"}),
        ("css", {"selector": "[role='row']"}),
        ("css", {"selector": ".w2grid"}),
        ("css", {"selector": ".grid"}),
    ]
    last_exc: Optional[Exception] = None
    for kind, kw in candidates:
        try:
            loc = frame.locator(kw["selector"])
            loc.first.wait_for(state="attached", timeout=timeout_ms)
            return
        except Exception as e:
            last_exc = e
    # 테이블이 없어도 다운로드는 될 수 있으니 hard fail은 피하고 로그만 남김
    logger.warning("테이블 렌더링 확인 실패(계속 진행): %s", last_exc)


def _download_excel_from_frame(frame: Frame, download_dir: Path, timeout_ms: int = 120_000) -> Path:
    """
    iframe 내부에서 'Excel 다운로드'를 눌러 파일을 저장하고 경로를 반환.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    # 버튼명이 살짝 바뀌거나 공백이 들어갈 수 있어서 텍스트/role 모두 시도
    download_btn_candidates = [
        ("role", {"role": "button", "name": re.compile(r"Excel\s*다운로드")}),
        ("text", {"text": "Excel 다운로드", "exact": False}),
        ("text", {"text": "엑셀 다운로드", "exact": False}),
        ("text", {"text": "엑셀", "exact": False}),
    ]

    with frame.expect_download(timeout=timeout_ms) as dl_info:
        _click_first(frame, download_btn_candidates, timeout_ms=30_000)

    download: Download = dl_info.value
    suggested = download.suggested_filename or "ngms.xlsx"
    out_path = download_dir / suggested
    download.save_as(out_path)
    return out_path


# -----------------------------
# Public Collectors
# -----------------------------
def collect_allocated(
    download_dir: Path,
    *,
    plan_period: str = "",
    designation_year: str = "",
    entry_url: str = DEFAULT_NGMS_ENTRY_URL,
    frame_title_keyword: str = "할당대상업체",
) -> Tuple[pd.DataFrame, str]:
    """
    할당대상업체 화면에서 엑셀 다운로드 후 DataFrame으로 반환.
    run_ngms.py가 (df, hint)를 기대하므로 Tuple로 유지.

    파라미터:
    - plan_period: 계획기간 콤보 값(문자열)
    - designation_year: 지정연도 콤보 값(문자열)
    """
    res = _collect_generic_excel(
        download_dir=download_dir,
        entry_url=entry_url,
        frame_title_keyword=frame_title_keyword,
        filters={
            "계획기간": plan_period,
            "지정연도": designation_year,
        },
    )
    return res.df, res.hint


def _collect_generic_excel(
    *,
    download_dir: Path,
    entry_url: str,
    frame_title_keyword: Optional[str],
    filters: Dict[str, str],
) -> CollectResult:
    """
    공통 로직:
    - entry_url 진입
    - NGMS iframe 진입
    - 필터 세팅(가능한 범위)
    - 조회
    - Excel 다운로드
    - pandas read_excel
    """
    hint_parts: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        # 1) entry 로딩
        page.goto(entry_url, wait_until="networkidle", timeout=60_000)

        # 2) iframe 획득
        frame = _get_ngms_frame(page, title_keyword=frame_title_keyword)

        # 3) 필터 적용(실패해도 계속 진행)
        for label, value in (filters or {}).items():
            ok = _try_set_combobox_by_label(frame, label, value)
            if not ok:
                hint_parts.append(f"[경고] '{label}' 필터를 '{value}'로 세팅 실패(화면 구조 상 미지원).")

        # 4) 조회 클릭 + 렌더 대기
        try:
            _safe_click_search(frame, timeout_ms=30_000)
            _wait_table_rendered(frame, timeout_ms=30_000)
        except Exception as e:
            hint_parts.append(f"[경고] 조회/렌더 대기 중 예외(다운로드는 시도): {e}")

        # 5) Excel 다운로드
        excel_path = _download_excel_from_frame(frame, download_dir, timeout_ms=120_000)

        # 6) 읽기
        try:
            df = pd.read_excel(excel_path)
        except Exception as e:
            # NGMS 엑셀이 xls/xlsx 혼재하거나, 파일이 HTML로 떨어지는 경우 방어
            hint_parts.append(f"[오류] 엑셀 파싱 실패: {e}")
            raise

        context.close()
        browser.close()

    hint = "\n".join(hint_parts).strip()
    return CollectResult(df=df, hint=hint, excel_path=excel_path)


# -----------------------------
# Optional: 다른 화면도 같은 방식으로 확장 가능
# (run_ngms.py에서 필요하면 import해서 사용)
# -----------------------------
def collect_excel_any_screen(
    download_dir: Path,
    *,
    entry_url: str,
    frame_title_keyword: Optional[str] = None,
    filters: Optional[Dict[str, str]] = None,
) -> CollectResult:
    """
    특정 화면(iframe title keyword 또는 url 기준)에서
    필터 적용 후 엑셀 다운로드/파싱까지 공통 처리하는 유틸.

    사용 예:
    res = collect_excel_any_screen(
        Path("downloads"),
        entry_url="https://ngms.gir.go.kr/hom/subMain.do?menuNo=....",
        frame_title_keyword="목표관리",
        filters={"계획기간":"3차", "지정연도":"2024"}
    )
    """
    return _collect_generic_excel(
        download_dir=download_dir,
        entry_url=entry_url,
        frame_title_keyword=frame_title_keyword,
        filters=filters or {},
    )
