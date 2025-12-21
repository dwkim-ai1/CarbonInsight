#!/usr/bin/env python3
"""
로컬 테스트용 스크립트

사용법:
    pip install -r ../requirements/requirements_allocation.txt
    python test_collector.py --test-etrs
    
    # NGMS 테스트 시
    playwright install chromium
    python test_collector.py --test-ngms
"""

import requests
import pandas as pd
from io import BytesIO
from pathlib import Path
from datetime import datetime
import argparse
import sys

SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"


class ETRSCollector:
    """ETRS 데이터 수집기"""
    
    BASE_URL = "https://etrs.gir.go.kr"
    
    MENU_IDS = {
        "사전할당량": 24,
        "인증배출량": 20,
        "추가할당량": 14,
    }
    
    SECTOR_CODES = {
        "전체": "",
        "전환": "B001",
        "산업": "B002",
        "건물": "A021",
        "수송": "B003",
        "폐기물": "A020",
        "공공기타": "B004",
    }
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
    
    def download_pre_allocation(self, plan_period=3, sector="전체", save_path=None):
        """사전할당량 데이터 다운로드"""
        url = f"{self.BASE_URL}/home/infoOpen/infoOpenList10Excel.do"
        
        params = {
            "pagerOffset": 0,
            "maxPageItems": 10,
            "maxIndexPages": 10,
            "menuId": self.MENU_IDS["사전할당량"],
            "condition.plPeriDgr": plan_period,
            "condition.sectCd": self.SECTOR_CODES.get(sector, ""),
            "condition.btCd": "",
        }
        
        print(f"[ETRS] 사전할당량 다운로드 중... (계획기간: {plan_period}차, 부문: {sector})")
        
        response = self.session.get(url, params=params, timeout=60)
        response.raise_for_status()
        
        if save_path:
            Path(save_path).write_bytes(response.content)
            print(f"[ETRS] 저장 완료: {save_path}")
        
        df = pd.read_excel(BytesIO(response.content))
        print(f"[ETRS] 데이터 로드 완료: {len(df)}행")
        
        return df


class NGMSCollector:
    """NGMS 데이터 수집기 (Playwright 필요)"""
    
    BASE_URL = "https://ngms.gir.go.kr:8443"
    
    def __init__(self):
        self.browser = None
        self.page = None
    
    def _init_browser(self):
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=True)
            self.page = self.browser.new_page()
            print("[NGMS] 브라우저 초기화 완료")
        except ImportError:
            print("❌ Playwright가 설치되지 않았습니다.")
            print("   pip install playwright")
            print("   playwright install chromium")
            sys.exit(1)
    
    def _close_browser(self):
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
    
    def download_allocation_companies(self, plan_period=4, save_path=None):
        """할당대상업체 데이터 다운로드"""
        self._init_browser()
        
        try:
            url = f"{self.BASE_URL}/websquare/ngms.do?w2xPath=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"
            print(f"[NGMS] 할당대상업체 페이지 로드 중...")
            
            self.page.goto(url, wait_until="networkidle", timeout=60000)
            self.page.wait_for_timeout(3000)
            
            frame = self.page.frame_locator("iframe").first
            
            print(f"[NGMS] 계획기간 {plan_period} 선택...")
            frame.locator("select").first.select_option(str(plan_period))
            self.page.wait_for_timeout(1000)
            
            frame.locator("button:has-text('검색'), input[value='검색']").first.click()
            self.page.wait_for_timeout(3000)
            
            download_path = save_path or DATA_DIR / f"ngms_allocation_{plan_period}차_{datetime.now().strftime('%Y%m%d')}.xlsx"
            
            with self.page.expect_download(timeout=60000) as download_info:
                frame.locator("button:has-text('Excel'), a:has-text('Excel')").first.click()
            
            download = download_info.value
            download.save_as(download_path)
            print(f"[NGMS] 저장 완료: {download_path}")
            
            df = pd.read_excel(download_path)
            print(f"[NGMS] 데이터 로드 완료: {len(df)}행")
            
            return df
            
        finally:
            self._close_browser()


def test_etrs():
    """ETRS 테스트"""
    print("\n" + "="*60)
    print("ETRS (배출권등록부시스템) 테스트")
    print("="*60 + "\n")
    
    DATA_DIR.mkdir(exist_ok=True)
    
    collector = ETRSCollector()
    save_path = DATA_DIR / f"etrs_test_{datetime.now().strftime('%Y%m%d')}.xlsx"
    df = collector.download_pre_allocation(plan_period=3, sector="전체", save_path=save_path)
    
    print("\n" + "-"*40)
    print("데이터 미리보기:")
    print("-"*40)
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(10))
    
    return df


def test_ngms():
    """NGMS 테스트"""
    print("\n" + "="*60)
    print("NGMS (국가온실가스종합관리시스템) 테스트")
    print("="*60 + "\n")
    
    DATA_DIR.mkdir(exist_ok=True)
    
    collector = NGMSCollector()
    df = collector.download_allocation_companies(plan_period=4)
    
    print("\n" + "-"*40)
    print("데이터 미리보기:")
    print("-"*40)
    print(f"Shape: {df.shape}")
    print(f"Columns: {list(df.columns)}")
    print(df.head(10))
    
    return df


def main():
    parser = argparse.ArgumentParser(description="GIR 데이터 수집기 테스트")
    parser.add_argument("--test-etrs", action="store_true", help="ETRS 다운로드 테스트")
    parser.add_argument("--test-ngms", action="store_true", help="NGMS 다운로드 테스트")
    parser.add_argument("--all", action="store_true", help="모든 테스트")
    
    args = parser.parse_args()
    
    if args.test_etrs:
        test_etrs()
    elif args.test_ngms:
        test_ngms()
    elif args.all:
        test_etrs()
        test_ngms()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
