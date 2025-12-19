"""
NGMS Data Scraper Configuration
국가온실가스종합관리시스템 데이터 스크래퍼 설정
"""

# Base URLs
NGMS_BASE_URL = "https://ngms.gir.go.kr:8443"
NGMS_MAIN_URL = f"{NGMS_BASE_URL}/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501"

# Page URLs - subMain.do로 접속 후 iframe 내에서 작업
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

# Column definitions for each data type (표준 컬럼명)
COLUMNS = {
    "할당대상업체": ["순번", "계획기간", "지정연도", "업종", "업체명", "소재지", "적용기준"],
    "목표관리대상업체": ["순번", "지정연도", "관장기관", "관리업체명", "지정업종", "주소", "지정구분", "고시이력"],
    "명세서배출량통계": ["순번", "업체명", "대상년도", "지정구분", "부문", "지정업종", 
                        "온실가스배출량(tCO2)", "에너지사용량(TJ)", "검증수행기관", "중소기업여부", "비고"],
}

# ★★★ 컬럼명 매핑 테이블 (원본 → 표준) ★★★
# 시점별로 달라지는 헤더를 표준 컬럼명으로 통일
COLUMN_MAPPING = {
    # === 공통: 순번 ===
    "NO": "순번",
    "번호": "순번",
    "No": "순번",
    "no": "순번",
    "No.": "순번",
    
    # === 명세서배출량통계: 업체명 ===
    "법인명": "업체명",
    "사업장명": "업체명",
    "회사명": "업체명",
    "기업명": "업체명",
    "사업장": "업체명",
    
    # === 연도 관련 ===
    "대상연도": "대상년도",
    "배출년도": "대상년도",
    "배출연도": "대상년도",
    "년도": "대상년도",
    "연도": "대상년도",
    
    # === 배출량 관련 ===
    "가스 배출량(tCO2)": "온실가스배출량(tCO2)",
    "가스배출량(tCO2)": "온실가스배출량(tCO2)",
    "온실가스 배출량(tCO2)": "온실가스배출량(tCO2)",
    "온실가스배출량": "온실가스배출량(tCO2)",
    "배출량(tCO2)": "온실가스배출량(tCO2)",
    "GHG배출량": "온실가스배출량(tCO2)",
    "온실가스배출량(tCO2eq)": "온실가스배출량(tCO2)",
    
    # === 에너지 사용량 ===
    "에너지 사용량(TJ)": "에너지사용량(TJ)",
    "에너지사용량": "에너지사용량(TJ)",
    "사용량(TJ)": "에너지사용량(TJ)",
    
    # === 업종 관련 ===
    "계획업종": "지정업종",
    "업종": "지정업종",
    "산업분류": "지정업종",
    
    # === 기타 ===
    "중소기업 여부": "중소기업여부",
    "중소기업": "중소기업여부",
    "SME": "중소기업여부",
    "검증기관": "검증수행기관",
    "인증기관": "검증수행기관",
    
    # === 할당대상업체 ===
    "할당대상업체명": "업체명",
    
    # === 목표관리대상업체 ===
    "목표관리업체명": "관리업체명",
    "업체명칭": "관리업체명",
    
    # === 기관 관련 ===
    "관장기관": "관장기관",  # 유지
}

# Key columns for identifying unique records and detecting changes
KEY_COLUMNS = {
    "할당대상업체": ["업체명", "지정연도"],
    "목표관리대상업체": ["관리업체명", "지정연도"],
    "명세서배출량통계": ["업체명", "대상년도"],
}

# Value columns for detecting changes (excluding key columns)
VALUE_COLUMNS = {
    "할당대상업체": ["계획기간", "업종", "소재지", "적용기준"],
    "목표관리대상업체": ["관장기관", "지정업종", "주소", "지정구분", "고시이력"],
    "명세서배출량통계": ["지정구분", "부문", "지정업종", "온실가스배출량(tCO2)", 
                         "에너지사용량(TJ)", "검증수행기관", "중소기업여부", "비고"],
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
