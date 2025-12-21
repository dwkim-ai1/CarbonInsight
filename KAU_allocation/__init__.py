"""
KAU_allocation - ETRS Data Scraper Package
배출권등록부시스템 데이터 스크래퍼 패키지

This package provides tools to:
- Scrape data from ETRS website (https://etrs.gir.go.kr)
- Update Google Sheets with downloaded data
- Track changes over time

Data types handled:
- 사전할당량 (Pre-allocation)
- 인증배출량 (Certified Emissions)
- 추가할당량 (Additional Allocation)
- 할당취소량 (Cancelled Allocation)
- 배출권이월량 (Carried-over Allowances)
- 배출권차입량 (Borrowed Allowances)
"""

__version__ = "1.0.0"
__author__ = "KAU Data Team"

from .config import (
    ETRS_BASE_URL,
    DATASETS,
    PLAN_PERIODS,
    SHEET_NAMES,
    COLUMNS,
)

from .utils import (
    setup_logging,
    compare_dataframes,
    clean_excel_data,
)

from .google_sheets_handler import (
    GoogleSheetsHandler,
    create_handler_from_env,
)

from .etrs_scraper import (
    ETRSScraper,
    run_scraper,
)

from .main import main

__all__ = [
    # Config
    'ETRS_BASE_URL',
    'DATASETS',
    'PLAN_PERIODS',
    'SHEET_NAMES',
    'COLUMNS',
    
    # Utils
    'setup_logging',
    'compare_dataframes',
    'clean_excel_data',
    
    # Google Sheets
    'GoogleSheetsHandler',
    'create_handler_from_env',
    
    # Scraper
    'ETRSScraper',
    'run_scraper',
    
    # Main
    'main',
]
