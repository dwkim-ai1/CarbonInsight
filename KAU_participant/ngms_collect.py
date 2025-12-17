import re
from pathlib import Path
from typing import Tuple, List, Optional

import pandas as pd
from playwright.sync_api import sync_playwright, Page


NGMS_URL = (
    "https://ngms.gir.go.kr:8443/"
    "subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
)


def _page_update_hint(page: Page) -> str:
    try:
        body = page.locator("body").inner_text()
    except Exception:
        return ""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    for ln in lines:
        if (
            re.search(r"(업데이트|고시|기준|등록|작성|수정)", ln)
            and re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", ln)
        ):
            return ln
    return ""


def _click_tab(page: Page, tab_name: str) -> None:
    # ✅ strict mode 중복 매칭 방지: role=tab로 한정
    tab = page.get_by_role("tab", name=tab_name)
    tab.click()
    page.wait_for_timeout(800)


def _download_excel(page: Page, download_dir: Path) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    with page.expect_download() as dlinfo:
        # "Excel 다운로드" 버튼은 보통 1개지만, 혹시 모르면 role=button + 정규식
        page.get_by_role("button", name=re.compile(r"Excel\s*다운로드")).click()
    download = dlinfo.value
    out = download_dir / (download.suggested_filename or "download.xlsx")
    download.save_as(out)
    return out


def _safe_set_combo_by_label(page: Page, label_text: str, value_text: str) -> bool:
    """
    label_text(예: '지정연도') 옆 콤보박스를 찾아 value_text 선택.
    UI 구조가 다를 수 있어 best-effort.
    성공하면 True, 못하면 False.
    """
    if not value_text:
        return False

    # 1) label 텍스트 근처에 있는 select 시도
    try:
        sel = page.locator(
            f"xpath=//*[normalize-space()='{label_text}']/following::select[1]"
        )
        if sel.count() > 0:
            sel.first.select_option(label=value_text)
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass

    # 2) combobox(role) 시도 (근처 1개만 잡히면 클릭→옵션 클릭)
    try:
        # label 요소 다음의 combobox를 넓게 찾음
        combo = page.locator(
            f"xpath=//*[normalize-space()='{label_text}']/following::*[@role='combobox'][1]"
        )
        if combo.count() > 0:
            combo.first.click()
            page.wait_for_timeout(200)
            page.get_by_text(value_text, exact=True).click()
            page.wait_for_timeout(300)
            return True
    except Exception:
        pass

    # 3) 마지막 fallback: label_text와 value_text가 같은 행에 존재하면 value_text 클릭(옵션 리스트)
    try:
        page.get_by_text(label_text, exact=False).click(timeout=1000)
        page.wait_for_timeout(200)
        page.get_by_text(value_text, exact=True).click(timeout=1000)
        page.wait_for_timeout(300)
        return True
    except Exception:
        return False


def _safe_click_search(page: Page) -> None:
    """
    검색 버튼이 있으면 클릭. 없으면 무시.
    """
    try:
        btn = page.get_by_role("button", name=re.compile(r"검색|조회"))
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(800)
    except Exception:
        pass


def collect_allocated(download_dir: Path, plan_period: str = "", designation_year: str = "") -> Tuple[pd.DataFrame, str]:
    """
    할당대상업체
    - plan_period / designation_year가 주어지면 가능한 범위에서 필터 적용 후 다운로드
    - 필터 UI를 못 찾으면 전체 다운로드로 진행
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        _click_tab(page, "할당대상업체")

        # 필터 (best-effort)
        _safe_set_combo_by_label(page, "계획기간", plan_period)
        _safe_set_combo_by_label(page, "지정연도", designation_year)
        _safe_click_search(page)

        hint = _page_update_hint(page)
        excel = _download_excel(page, download_dir)
        df = pd.read_excel(excel)

        context.close()
        browser.close()
    return df, hint


def collect_target_mgmt(download_dir: Path, designation_year: str = "") -> Tuple[pd.DataFrame, str]:
    """
    목표관리대상업체
    - designation_year가 주어지면 필터 적용 시도
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        _click_tab(page, "목표관리대상업체")

        _safe_set_combo_by_label(page, "지정연도", designation_year)
        _safe_click_search(page)

        hint = _page_update_hint(page)
        excel = _download_excel(page, download_dir)
        df = pd.read_excel(excel)

        context.close()
        browser.close()
    return df, hint


def collect_statement_stats(download_dir: Path, emission_year: str = "") -> Tuple[pd.DataFrame, str]:
    """
    명세서배출량통계
    - emission_year가 비어 있으면: 최신연도(첫 행)
    - emission_year가 있으면: 해당 연도 행을 찾아 다운로드 시도 (없으면 첫 행 fallback)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        _click_tab(page, "명세서배출량통계")

        hint = _page_update_hint(page)

        # 테이블 렌더 대기
        page.locator("table tbody tr").first.wait_for(timeout=20000)

        # 대상 row 찾기
        row = page.locator("table tbody tr").first
        if emission_year:
            candidates = page.locator("table tbody tr")
            n = candidates.count()
            found = False
            for i in range(min(n, 2000)):  # 너무 길어질까봐 상한
                td0 = candidates.nth(i).locator("td").first
                try:
                    y = td0.inner_text().strip()
                except Exception:
                    continue
                if y == str(emission_year).strip():
                    row = candidates.nth(i)
                    found = True
                    break
            if not found:
                # 해당 연도가 없으면 첫 행으로 fallback
                row = page.locator("table tbody tr").first

        year_text = row.locator("td").first.inner_text().strip()

        down_buttons = row.get_by_role("button", name=re.compile("다운"))
        count = down_buttons.count()

        kinds = ["업체배출량", "지역별배출량", "업종별배출량", "목표달성여부"]
        frames: List[pd.DataFrame] = []

        for i in range(min(count, 4)):
            with page.expect_download() as dlinfo:
                down_buttons.nth(i).click()

            download = dlinfo.value
            out = download_dir / (download.suggested_filename or f"stats_{i}.xlsx")
            download.save_as(out)

            df = pd.read_excel(out)
            df.insert(0, "배출년도", year_text)
            df.insert(1, "구분", kinds[i] if i < len(kinds) else f"구분{i+1}")
            frames.append(df)

        context.close()
        browser.close()

    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return merged, hint
