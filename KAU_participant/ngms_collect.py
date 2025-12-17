import re
from pathlib import Path
from typing import Tuple, List

import pandas as pd
from playwright.sync_api import sync_playwright, Page


NGMS_URL = (
    "https://ngms.gir.go.kr:8443/"
    "subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
)


def _page_update_hint(page: Page) -> str:
    """
    화면 내에서 '업데이트/고시/기준' + 날짜가 포함된 문구를 최대 1개 추출
    """
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


def _download_excel(page: Page, download_dir: Path) -> Path:
    """
    'Excel 다운로드' 버튼 클릭 → xlsx 저장
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    with page.expect_download() as dlinfo:
        page.get_by_role(
            "button", name=re.compile(r"Excel\s*다운로드")
        ).click()

    download = dlinfo.value
    out = download_dir / (download.suggested_filename or "download.xlsx")
    download.save_as(out)
    return out


def collect_allocated(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    할당대상업체 전체 다운로드
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        page.get_by_text("할당대상업체", exact=False).click()
        page.wait_for_timeout(800)

        hint = _page_update_hint(page)
        excel = _download_excel(page, download_dir)
        df = pd.read_excel(excel)

        context.close()
        browser.close()

    return df, hint


def collect_target_mgmt(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    목표관리대상업체 전체 다운로드
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        page.get_by_text("목표관리대상업체", exact=False).click()
        page.wait_for_timeout(800)

        hint = _page_update_hint(page)
        excel = _download_excel(page, download_dir)
        df = pd.read_excel(excel)

        context.close()
        browser.close()

    return df, hint


def collect_statement_stats_latest_year(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    명세서배출량통계
    - 최신 연도(목록 첫 행)
    - 업체 / 지역 / 업종 / 목표달성 (최대 4종)
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        page.goto(NGMS_URL, wait_until="networkidle")
        page.get_by_text("명세서배출량통계", exact=False).click()
        page.wait_for_timeout(1000)

        hint = _page_update_hint(page)

        # 첫 번째 데이터 행
        first_row = page.locator("table tbody tr").first
        year_text = first_row.locator("td").first.inner_text().strip()

        down_buttons = first_row.get_by_role("button", name=re.compile("다운"))
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
