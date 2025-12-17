"""
KAU_participant - NGMS Data Scraper Package
국가온실가스종합관리시스템 데이터 스크래퍼 패키지

This package provides tools to:
- Scrape data from NGMS website (https://ngms.gir.go.kr)
- Update Google Sheets with downloaded data
- Track changes over time

Data types handled:
- 할당대상업체 (ETS Allocation Targets)
- 목표관리대상업체 (Target Management Companies)
- 명세서배출량통계 (Emission Statistics)
"""

__version__ = "1.0.0"
__author__ = "KAU Data Team"

from .config import (
    NGMS_BASE_URL,
    NGMS_MAIN_URL,
    SHEET_NAMES,
    COLUMNS
)

from .utils import (
    setup_logging,
    compare_dataframes,
    clean_excel_data
)

from .google_sheets_handler import (
    GoogleSheetsHandler,
    create_handler_from_env
)

from .ngms_scraper import (
    NGMSScraper,
    run_scraper
)

from .main import main, run

__all__ = [
    # Config
    'NGMS_BASE_URL',
    'NGMS_MAIN_URL', 
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
    'NGMSScraper',
    'run_scraper',
    
    # Main
    'main',
    'run'
]
