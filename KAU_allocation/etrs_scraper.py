"""
ETRS Web Scraper
배출권등록부시스템 웹 스크래퍼

requests 기반으로 엑셀 파일 직접 다운로드
"""

import os
from typing import Dict, Optional, List
from pathlib import Path
from datetime import datetime
import requests
import pandas as pd

from .config import (
    ETRS_BASE_URL,
    DATASETS,
    PLAN_PERIODS,
    SECTOR_CODES,
    REQUEST_SETTINGS,
)
from .utils import (
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
            dataset_name: Name of dataset (사전할당량, 인증배출량, etc.)
            plan_period: Plan period (1, 2, or 3)
            sector: Sector name (전체, 전환, 산업, etc.)
            save_file: Whether to save downloaded file
            
        Returns:
            DataFrame with downloaded data
        """
        if dataset_name not in DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            logger.info(f"사용 가능한 데이터셋: {list(DATASETS.keys())}")
            return None
        
        dataset = DATASETS[dataset_name]
        
        url = f"{self.base_url}/home/infoOpen/{dataset['endpoint']}.do"
        
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
        
        try:
            response = self.session.get(
                url, 
                params=params, 
                timeout=REQUEST_SETTINGS["timeout"]
            )
            response.raise_for_status()
            
            # Read Excel
            df = read_excel_auto(response.content)
            
            # Clean data
            df = clean_excel_data(df)
            
            logger.info(f"✅ 다운로드 완료: {len(df)}행")
            
            # Save file
            if save_file:
                filename = f"etrs_{dataset_name}_{plan_period}차_{datetime.now().strftime('%Y%m%d')}.xlsx"
                filepath = self.download_dir / filename
                df.to_excel(filepath, index=False)
                logger.info(f"💾 파일 저장: {filepath}")
            
            return df
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ HTTP 오류: {e}")
            logger.error(f"   URL: {url}")
            return None
        except Exception as e:
            logger.error(f"❌ 다운로드 실패: {e}")
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
            periods: List of plan periods (default: [1, 2, 3])
            sector: Sector name
            
        Returns:
            Dictionary mapping period to DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        results = {}
        
        for period in periods:
            df = self.download_excel(dataset_name, period, sector)
            if df is not None and len(df) > 0:
                results[period] = df
        
        return results
    
    def download_all_datasets(
        self,
        plan_period: int = 3,
        sector: str = "전체"
    ) -> Dict[str, pd.DataFrame]:
        """
        Download all datasets for a specific plan period
        
        Args:
            plan_period: Plan period
            sector: Sector name
            
        Returns:
            Dictionary mapping dataset name to DataFrame
        """
        results = {}
        
        for dataset_name in DATASETS.keys():
            df = self.download_excel(dataset_name, plan_period, sector)
            if df is not None and len(df) > 0:
                results[dataset_name] = df
        
        return results


def run_scraper(
    datasets: List[str] = None,
    periods: List[int] = None,
    download_dir: str = None
) -> Dict[str, Dict[int, pd.DataFrame]]:
    """
    Run the scraper
    
    Args:
        datasets: List of dataset names to download (default: all)
        periods: List of plan periods (default: [1, 2, 3])
        download_dir: Download directory
        
    Returns:
        Nested dictionary: {dataset_name: {period: DataFrame}}
    """
    scraper = ETRSScraper(download_dir=download_dir)
    
    if datasets is None:
        datasets = list(DATASETS.keys())
    
    if periods is None:
        periods = list(PLAN_PERIODS.keys())
    
    results = {}
    
    for dataset_name in datasets:
        results[dataset_name] = scraper.download_all_periods(
            dataset_name, 
            periods=periods
        )
    
    return results
