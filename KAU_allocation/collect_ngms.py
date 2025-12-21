#!/usr/bin/env python3
"""
NGMS (국가온실가스종합관리시스템) 데이터 수집 스크립트
Playwright를 사용하여 데이터 다운로드
GitHub Actions에서 실행됨
"""

import os
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# 스크립트 위치 기준 데이터 디렉토리
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"


def get_gspread_client():
    """Google Sheets 클라이언트 생성"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS 환경변수가 설정되지 않았습니다.")
    
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    creds_dict = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    
    return gspread.authorize(credentials)


def update_sheet(gc, spreadsheet_id, sheet_name, df):
    """시트 업데이트"""
    spreadsheet = gc.open_by_key(spreadsheet_id)
    
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        worksheet.clear()
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name, 
            rows=len(df) + 100, 
            cols=len(df.columns) + 5
        )
    
    df_clean = df.fillna("")
    data = [df_clean.columns.tolist()] + df_clean.values.tolist()
    
    worksheet.update(data, value_input_option='USER_ENTERED')
    print(f"✅ '{sheet_name}' 업데이트 완료: {len(df)}행")


class NGMSDownloader:
    """NGMS 데이터 다운로더"""
    
    BASE_URL = "https://ngms.gir.go.kr:8443"
    
    def __init__(self, download_dir):
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        self.playwright = None
        self.browser = None
        self.page = None
    
    def __enter__(self):
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        self.page = self.browser.new_page(
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        return self
    
    def __exit__(self, *args):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def _wait_and_click(self, frame, selector, timeout=10000):
        """요소 대기 후 클릭"""
        element = frame.locator(selector).first
        element.wait_for(state="visible", timeout=timeout)
        element.click()
    
    def _download_with_button(self, frame, button_selector, filename):
        """다운로드 버튼 클릭 및 파일 저장"""
        filepath = self.download_dir / filename
        
        with self.page.expect_download(timeout=120000) as download_info:
            self._wait_and_click(frame, button_selector)
        
        download = download_info.value
        download.save_as(filepath)
        print(f"💾 다운로드 완료: {filepath}")
        
        return filepath
    
    def download_allocation_companies(self, plan_period=4):
        """할당대상업체 데이터 다운로드"""
        url = f"{self.BASE_URL}/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
        
        print(f"📄 할당대상업체 페이지 로드 중...")
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        self.page.wait_for_timeout(5000)
        
        frame = self.page.frame_locator("iframe").first
        
        print(f"🔧 계획기간 {plan_period} 선택...")
        frame.locator("select").first.select_option(str(plan_period))
        self.page.wait_for_timeout(2000)
        
        self._wait_and_click(frame, "input[value='검색'], button:has-text('검색')")
        self.page.wait_for_timeout(5000)
        
        filename = f"ngms_allocation_{plan_period}차_{datetime.now().strftime('%Y%m%d')}.xlsx"
        filepath = self._download_with_button(
            frame, 
            "button:has-text('Excel'), a:has-text('Excel 다운로드')",
            filename
        )
        
        return pd.read_excel(filepath)
    
    def download_target_management(self, year=None):
        """목표관리대상업체 데이터 다운로드"""
        url = f"{self.BASE_URL}/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS022V.xml&menuNo=50900502"
        
        print(f"📄 목표관리대상업체 페이지 로드 중...")
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        self.page.wait_for_timeout(5000)
        
        frame = self.page.frame_locator("iframe").first
        
        self._wait_and_click(frame, "input[value='검색'], button:has-text('검색')")
        self.page.wait_for_timeout(5000)
        
        filename = f"ngms_target_{datetime.now().strftime('%Y%m%d')}.xlsx"
        filepath = self._download_with_button(
            frame,
            "button:has-text('Excel'), a:has-text('Excel 다운로드')",
            filename
        )
        
        return pd.read_excel(filepath)
    
    def download_emission_statistics(self, year=None):
        """명세서배출량통계 다운로드"""
        url = f"{self.BASE_URL}/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS023V.xml&menuNo=50900503"
        
        print(f"📄 명세서배출량통계 페이지 로드 중...")
        self.page.goto(url, wait_until="networkidle", timeout=60000)
        self.page.wait_for_timeout(5000)
        
        frame = self.page.frame_locator("iframe").first
        
        self._wait_and_click(frame, "input[value='조회'], button:has-text('조회')")
        self.page.wait_for_timeout(3000)
        
        results = {}
        categories = ["업체배출량", "지역별배출량", "인증별배출량"]
        
        download_buttons = frame.locator("button:has-text('다운'), a:has-text('다운')").all()
        
        for i, (btn, category) in enumerate(zip(download_buttons[:3], categories)):
            try:
                filename = f"ngms_emission_{category}_{datetime.now().strftime('%Y%m%d')}.xlsx"
                filepath = self.download_dir / filename
                
                with self.page.expect_download(timeout=120000) as download_info:
                    btn.click()
                
                download = download_info.value
                download.save_as(filepath)
                
                results[category] = pd.read_excel(filepath)
                print(f"💾 {category} 다운로드 완료: {len(results[category])}행")
                
            except Exception as e:
                print(f"⚠️ {category} 다운로드 실패: {e}")
        
        return results


def main():
    print("=" * 60)
    print("NGMS 데이터 수집 시작")
    print(f"시간: {datetime.now().isoformat()}")
    print("=" * 60)
    
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID 환경변수가 설정되지 않았습니다.")
    
    DATA_DIR.mkdir(exist_ok=True)
    
    gc = get_gspread_client()
    print("✅ Google Sheets 인증 완료")
    
    with NGMSDownloader(DATA_DIR) as downloader:
        
        # 1. 할당대상업체 (계획기간별)
        for plan_period in [3, 4]:
            try:
                print(f"\n--- 할당대상업체 {plan_period}차 ---")
                df = downloader.download_allocation_companies(plan_period)
                update_sheet(gc, spreadsheet_id, f"NGMS_할당대상업체_{plan_period}차", df)
            except Exception as e:
                print(f"❌ 할당대상업체 {plan_period}차 수집 실패: {e}")
        
        # 2. 목표관리대상업체
        try:
            print(f"\n--- 목표관리대상업체 ---")
            df = downloader.download_target_management()
            update_sheet(gc, spreadsheet_id, "NGMS_목표관리대상업체", df)
        except Exception as e:
            print(f"❌ 목표관리대상업체 수집 실패: {e}")
        
        # 3. 명세서배출량통계
        try:
            print(f"\n--- 명세서배출량통계 ---")
            results = downloader.download_emission_statistics()
            for category, df in results.items():
                update_sheet(gc, spreadsheet_id, f"NGMS_명세서_{category}", df)
        except Exception as e:
            print(f"❌ 명세서배출량통계 수집 실패: {e}")
    
    print("\n" + "=" * 60)
    print("NGMS 데이터 수집 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
