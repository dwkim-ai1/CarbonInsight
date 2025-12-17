import os
import re
import time
from pathlib import Path
from typing import Optional, Tuple, List

import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


NGMS_URL = "https://ngms.gir.go.kr:8443/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"


def _make_driver(download_dir: Path) -> webdriver.Chrome:
    download_dir.mkdir(parents=True, exist_ok=True)

    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    prefs = {
        "download.default_directory": str(download_dir.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    chrome_options.add_experimental_option("prefs", prefs)

    return webdriver.Chrome(options=chrome_options)


def _wait_download(download_dir: Path, timeout: int = 120) -> Path:
    """
    다운로드 완료된 최신 xlsx 파일 반환
    """
    end = time.time() + timeout
    while time.time() < end:
        files = sorted(download_dir.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            # 크롬 다운로드 중인 임시파일(.crdownload)이 있으면 대기
            if any(download_dir.glob("*.crdownload")):
                time.sleep(1)
                continue
            return files[0]
        time.sleep(1)
    raise TimeoutError("xlsx 다운로드를 timeout 내에 찾지 못했습니다.")


def _page_update_hint(driver) -> str:
    """
    화면에 표시된 '업데이트/고시/기준/등록/작성' 류의 텍스트를 최대한 찾아서 반환.
    못 찾으면 빈 문자열.
    """
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        return ""
    # 너무 공격적으로 파싱하지 말고, 날짜 패턴이 포함된 라인만 후보로
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    candidates: List[str] = []
    for ln in lines:
        if re.search(r"(업데이트|고시|기준|등록|작성|수정)", ln) and re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", ln):
            candidates.append(ln)
    return candidates[0] if candidates else ""


def _click_tab(driver, tab_text: str):
    # 상단 탭 텍스트로 클릭 (화면 구조 변경 가능성 대비: 부분일치)
    tab = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, f"//*[contains(normalize-space(.), '{tab_text}')]"))
    )
    tab.click()
    time.sleep(1)


def _click_excel_download(driver):
    btn = WebDriverWait(driver, 20).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Excel') and contains(., '다운로드')]"))
    )
    btn.click()


def collect_allocated(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    할당대상업체: 전체조건(기본)에서 Excel 다운로드
    """
    driver = _make_driver(download_dir)
    try:
        driver.get(NGMS_URL)
        _click_tab(driver, "할당대상업체")
        hint = _page_update_hint(driver)
        # 검색 없이 바로 다운로드(전체)
        _click_excel_download(driver)
        f = _wait_download(download_dir)
        df = pd.read_excel(f)
        return df, hint
    finally:
        driver.quit()


def collect_target_mgmt(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    목표관리대상업체: Excel 다운로드
    """
    driver = _make_driver(download_dir)
    try:
        driver.get(NGMS_URL)
        _click_tab(driver, "목표관리대상업체")
        hint = _page_update_hint(driver)
        _click_excel_download(driver)
        f = _wait_download(download_dir)
        df = pd.read_excel(f)
        return df, hint
    finally:
        driver.quit()


def collect_statement_stats_latest_year(download_dir: Path) -> Tuple[pd.DataFrame, str]:
    """
    명세서배출량통계: 최신 연도(목록 첫 행의 배출년도)만 4종 다운로드 후 병합
    - 업체배출량 / 지역별배출량 / 업종별배출량 / 목표달성여부(있으면)
    """
    driver = _make_driver(download_dir)
    try:
        driver.get(NGMS_URL)
        _click_tab(driver, "명세서배출량통계")
        hint = _page_update_hint(driver)

        # 테이블 첫 행의 배출년도 추출(화면 구조 바뀔 수 있어 다소 방어적으로)
        year_cell = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//table//tr[1]/td[1]"))
        )
        year = str(year_cell.text).strip()

        # 첫 행의 '다운' 버튼들을 왼쪽부터 순서대로 클릭
        # (업체/지역/업종/목표달성) 4개를 가정
        down_buttons = driver.find_elements(By.XPATH, "//table//tr[1]//button[contains(., '다운')]")
        if not down_buttons:
            raise RuntimeError("명세서배출량통계 첫 행에서 '다운' 버튼을 찾지 못했습니다.")

        kind_names = ["업체배출량", "지역별배출량", "업종별배출량", "목표달성여부"]
        dfs = []
        for i, btn in enumerate(down_buttons[:4]):
            # 다운로드 디렉토리 비우고 시작(파일명 중복 대비)
            for old in download_dir.glob("*.xlsx"):
                old.unlink(missing_ok=True)

            btn.click()
            f = _wait_download(download_dir)

            df = pd.read_excel(f)
            df.insert(0, "배출년도", year)
            df.insert(1, "구분", kind_names[i] if i < len(kind_names) else f"구분{i+1}")
            dfs.append(df)

        merged = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        return merged, hint
    finally:
        driver.quit()
