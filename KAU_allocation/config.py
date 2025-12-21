"""
ETRS Data Scraper Configuration
배출권등록부시스템 데이터 스크래퍼 설정
"""

# Base URLs
ETRS_BASE_URL = "https://etrs.gir.go.kr"

# 수집할 데이터셋 정의
# menuId와 endpoint는 ETRS 웹사이트 구조에 따름
DATASETS = {
    "사전할당량": {
        "menu_id": 24,
        "endpoint": "infoOpenList10Excel",
        "sheet_prefix": "ETRS_사전할당량",
    },
    "인증배출량": {
        "menu_id": 20,
        "endpoint": "infoOpenList06Excel",
        "sheet_prefix": "ETRS_인증배출량",
    },
    "추가할당량": {
        "menu_id": 14,
        "endpoint": "infoOpenList02Excel",
        "sheet_prefix": "ETRS_추가할당량",
    },
    "할당취소량": {
        "menu_id": 16,
        "endpoint": "infoOpenList04Excel",
        "sheet_prefix": "ETRS_할당취소량",
    },
    "배출권이월량": {
        "menu_id": 18,
        "endpoint": "infoOpenList08Excel",
        "sheet_prefix": "ETRS_배출권이월량",
    },
    "배출권차입량": {
        "menu_id": 19,
        "endpoint": "infoOpenList09Excel",
        "sheet_prefix": "ETRS_배출권차입량",
    },
}

# 계획기간 정의
PLAN_PERIODS = {
    1: "1차 (2015-2017)",
    2: "2차 (2018-2020)",
    3: "3차 (2021-2025)",
}

# 부문 코드
SECTOR_CODES = {
    "전체": "",
    "전환": "B001",
    "산업": "B002",
    "건물": "A021",
    "수송": "B003",
    "폐기물": "A020",
    "공공기타": "B004",
}

# Google Sheets 시트명 매핑
SHEET_NAMES = {
    # 계획기간별로 생성됨
    # 예: ETRS_사전할당량_1차, ETRS_사전할당량_2차, ETRS_사전할당량_3차
}

# 컬럼 정의 (데이터 유형별)
COLUMNS = {
    "사전할당량": [
        "번호", "부문", "업종", "업체명", "유상여부",
        # 연도별 할당량 컬럼은 계획기간에 따라 다름
    ],
    "인증배출량": [
        "번호", "부문", "업종", "업체명",
        # 연도별 배출량 컬럼
    ],
    "추가할당량": [
        "번호", "부문", "업종", "업체명",
        # 연도별 추가할당량 컬럼
    ],
}

# 키 컬럼 (레코드 식별용)
KEY_COLUMNS = {
    "사전할당량": ["업체명", "부문"],
    "인증배출량": ["업체명", "부문"],
    "추가할당량": ["업체명", "부문"],
}

# HTTP 요청 설정
REQUEST_SETTINGS = {
    "timeout": 120,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/vnd.ms-excel, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
}

# 디버그 설정
DEBUG = {
    "save_files": True,
    "verbose_logging": True,
}
