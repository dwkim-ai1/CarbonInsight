"""
NGMS Data Scraper Configuration
국가온실가스종합관리시스템 데이터 스크래퍼 설정
"""

# Base URLs
NGMS_BASE_URL = "https://ngms.gir.go.kr:8443"
NGMS_MAIN_URL = f"{NGMS_BASE_URL}/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"

# Page URLs for each data type (올바른 URL: subMain.do)
IFRAME_URLS = {
    "할당대상업체": f"{NGMS_BASE_URL}/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501",
    "목표관리대상업체": f"{NGMS_BASE_URL}/subMain.do?link=/hom/bbs/OGCMBBS022V.xml&menuNo=50900502",
    "명세서배출량통계": f"{NGMS_BASE_URL}/subMain.do?link=/hom/bbs/OGCMBBS023V.xml&menuNo=50900503",
}

# Google Sheets configuration
SHEET_NAMES = {
    # 최신 데이터 시트 (매월 업데이트)
    "할당대상업체_latest": "NGMS_할당대상업체",
    "목표관리대상업체_latest": "NGMS_목표관리대상업체",
    "명세서배출량통계_latest": "NGMS_명세서배출량통계",
    # 누적 데이터 시트 (변경 시 추가)
    "할당대상업체_stack": "할당대상업체",
    "목표관리대상업체_stack": "목표관리대상업체",
    "명세서배출량통계_stack": "명세서배출량통계",
}

# Column definitions for each data type
COLUMNS = {
    "할당대상업체": ["순번", "계획기간", "지정연도", "업종", "업체명", "소재지", "적용기준"],
    "목표관리대상업체": ["순번", "지정연도", "관장기관", "관리업체명", "지정업종", "주소", "지정구분", "고시이력"],
    "명세서배출량통계": ["번호", "관장기관", "법인명", "대상년도", "지정구분", "지정업종", 
                        "가스 배출량(tCO2)", "에너지 사용량(TJ)", "검증수행기관", "비고"],
}

# Key columns for identifying unique records and detecting changes
KEY_COLUMNS = {
    "할당대상업체": ["업체명", "지정연도"],
    "목표관리대상업체": ["관리업체명", "지정연도"],
    "명세서배출량통계": ["법인명", "대상년도"],
}

# Value columns for detecting changes (excluding key columns)
VALUE_COLUMNS = {
    "할당대상업체": ["계획기간", "업종", "소재지", "적용기준"],
    "목표관리대상업체": ["관장기관", "지정업종", "주소", "지정구분", "고시이력"],
    "명세서배출량통계": ["관장기관", "지정구분", "지정업종", "가스 배출량(tCO2)", 
                         "에너지 사용량(TJ)", "검증수행기관", "비고"],
}

# Tab selectors for navigation (inside iframe)
TAB_SELECTORS = {
    "할당대상업체": 'li[id*="50900501"] a, a[menuno="50900501"]',
    "목표관리대상업체": 'li[id*="50900502"] a, a[menuno="50900502"]',
    "명세서배출량통계": 'li[id*="50900503"] a, a[menuno="50900503"]',
}

# Download button selectors
DOWNLOAD_SELECTORS = {
    "할당대상업체": 'a:has-text("Excel 다운로드"), button:has-text("Excel 다운로드")',
    "목표관리대상업체": 'a:has-text("Excel 다운로드"), button:has-text("Excel 다운로드")',
    "명세서배출량통계": 'a:has-text("다운"), button:has-text("다운")',  # 업체배출량 다운로드
}

# Browser settings
BROWSER_SETTINGS = {
    "headless": True,
    "slow_mo": 100,  # milliseconds between actions
    "timeout": 60000,  # 60 seconds timeout
    "download_timeout": 60000,  # 60 seconds for download (reduced from 120)
}

# Retry settings
RETRY_SETTINGS = {
    "max_retries": 3,
    "retry_delay": 5,  # seconds
}

# Debug settings
DEBUG = {
    "save_screenshots": True,
    "save_html": True,
    "verbose_logging": True,
}

# API endpoints (for direct data access if available)
API_ENDPOINTS = {
    # These may need to be discovered through network monitoring
    "할당대상업체": "/websquare/ngms.do",
    "목표관리대상업체": "/websquare/ngms.do", 
    "명세서배출량통계": "/websquare/ngms.do",
}
