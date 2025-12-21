"""
ETRS Web Scraper
배출권등록부시스템 웹 스크래퍼

requests 기반 Excel 다운로드 방식
"""

import os
import sys
from typing import Dict, Optional, List
from pathlib import Path
from datetime import datetime
import requests
import pandas as pd

# Absolute imports (not relative)
from config import (
    ETRS_BASE_URL,
    DATASETS,
    PLAN_PERIODS,
    SECTOR_CODES,
    REQUEST_SETTINGS,
    RETRY_SETTINGS,
)
from utils import (
    setup_logging,
    clean_excel_data,
    read_excel_auto,
    ensure_directory,
)

logger = setup_logging()


class ETRSScraper:
    """ETRS 웹사이트 스크래퍼 클래스"""
    
    def __init__(self, download_dir: Optional[str] = None):
        """
        Initialize scraper
        
        Args:
            download_dir: Directory for downloaded files
        """
        self.base_url = ETRS_BASE_URL
        self.download_dir = Path(download_dir) if download_dir else Path(__file__).parent / "data"
        ensure_directory(str(self.download_dir))
        
        self.session = requests.Session()
        self.session.headers.update(REQUEST_SETTINGS["headers"])
        
        logger.info(f"ETRS Scraper 초기화 - 저장 경로: {self.download_dir}")
    
    def download_excel(
        self,
        dataset_name: str,
        plan_period: int,
        sector: str = "전체",
        save_file: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Download Excel data from ETRS
        
        Args:
            dataset_name: Name of dataset (사전할당량, etc.)
            plan_period: Plan period (1, 2, or 3)
            sector: Sector filter
            save_file: Whether to save the downloaded file
            
        Returns:
            DataFrame with downloaded data or None
        """
        if dataset_name not in DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            return None
        
        dataset = DATASETS[dataset_name]
        
        # Build URL
        url = f"{self.base_url}/home/infoOpen/{dataset['endpoint']}.do"
        
        # Build params
        params = {
            "pagerOffset": 0,
            "maxPageItems": 10,
            "maxIndexPages": 10,
            "menuId": dataset["menu_id"],
            "condition.plPeriDgr": plan_period,
            "condition.sectCd": SECTOR_CODES.get(sector, ""),
            "condition.btCd": "",
        }
        
        logger.info(f"📥 다운로드 중: {dataset_name} ({plan_period}차, {sector})")
        logger.debug(f"URL: {url}")
        logger.debug(f"Params: {params}")
        
        # Retry logic
        max_retries = RETRY_SETTINGS.get("max_retries", 3)
        retry_delay = RETRY_SETTINGS.get("retry_delay", 5)
        
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    url, 
                    params=params, 
                    timeout=REQUEST_SETTINGS["timeout"]
                )
                response.raise_for_status()
                
                # Check content type
                content_type = response.headers.get('Content-Type', '')
                logger.debug(f"Content-Type: {content_type}")
                
                # Read Excel
                df = read_excel_auto(response.content)
                
                # Clean data
                df = clean_excel_data(df)
                
                logger.info(f"✅ 다운로드 완료: {len(df)}행")
                
                # Save file if requested
                if save_file and len(df) > 0:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    filename = f"etrs_{dataset_name}_{plan_period}차_{timestamp}.xlsx"
                    filepath = self.download_dir / filename
                    df.to_excel(filepath, index=False)
                    logger.info(f"💾 파일 저장: {filepath}")
                
                return df
                
            except requests.exceptions.HTTPError as e:
                logger.error(f"❌ HTTP 오류 (시도 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
            except requests.exceptions.RequestException as e:
                logger.error(f"❌ 요청 오류 (시도 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    import time
                    time.sleep(retry_delay)
            except Exception as e:
                logger.error(f"❌ 다운로드 실패: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                return None
        
        logger.error(f"❌ 최대 재시도 횟수 초과: {dataset_name} {plan_period}차")
        return None
    
    def download_all_periods(
        self,
        dataset_name: str,
        periods: List[int] = None,
        sector: str = "전체"
    ) -> Dict[int, pd.DataFrame]:
        """
        Download data for multiple plan periods
        
        Args:
            dataset_name: Name of dataset
            periods: List of periods to download (default: all)
            sector: Sector filter
            
        Returns:
            Dictionary mapping period to DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        results = {}
        
        for period in periods:
            logger.info(f"\n--- {dataset_name} {period}차 ---")
            df = self.download_excel(dataset_name, period, sector)
            if df is not None and len(df) > 0:
                results[period] = df
                logger.info(f"✓ {period}차: {len(df)}행")
            else:
                logger.warning(f"✗ {period}차: 데이터 없음")
        
        return results
    
    def download_all_datasets(
        self,
        periods: List[int] = None,
        sector: str = "전체"
    ) -> Dict[str, Dict[int, pd.DataFrame]]:
        """
        Download all datasets for all periods
        
        Args:
            periods: List of periods to download
            sector: Sector filter
            
        Returns:
            Nested dictionary: dataset_name -> period -> DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        all_results = {}
        
        for dataset_name in DATASETS.keys():
            logger.info(f"\n{'='*50}")
            logger.info(f"데이터셋: {dataset_name}")
            logger.info(f"{'='*50}")
            
            results = self.download_all_periods(dataset_name, periods, sector)
            if results:
                all_results[dataset_name] = results
        
        return all_results


def run_scraper() -> Dict[str, Dict[int, pd.DataFrame]]:
    """
    Run the scraper and download all data
    
    Returns:
        Dictionary with all downloaded data
    """
    scraper = ETRSScraper()
    return scraper.download_all_datasets()


if __name__ == "__main__":
    # Test run
    results = run_scraper()
    
    for dataset_name, period_data in results.items():
        print(f"\n=== {dataset_name} ===")
        for period, df in period_data.items():
            print(f"  {period}차: {len(df)}행")
            print(f"  컬럼: {list(df.columns)}")
