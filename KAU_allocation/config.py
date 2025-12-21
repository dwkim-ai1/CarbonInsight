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
    # 아래는 엔드포인트 확인 후 활성화
    # "인증배출량": {
    #     "menu_id": 20,
    #     "endpoint": "infoOpenList06Excel",
    #     "sheet_prefix": "ETRS_인증배출량",
    # },
    # "추가할당량": {
    #     "menu_id": 14,
    #     "endpoint": "infoOpenList02Excel",
    #     "sheet_prefix": "ETRS_추가할당량",
    # },
    # "할당취소량": {
    #     "menu_id": 16,
    #     "endpoint": "infoOpenList04Excel",
    #     "sheet_prefix": "ETRS_할당취소량",
    # },
    # "배출권이월량": {
    #     "menu_id": 18,
    #     "endpoint": "infoOpenList08Excel",
    #     "sheet_prefix": "ETRS_배출권이월량",
    # },
    # "배출권차입량": {
    #     "menu_id": 19,
    #     "endpoint": "infoOpenList09Excel",
    #     "sheet_prefix": "ETRS_배출권차입량",
    # },
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
    # 최신 데이터 시트 (전체 교체)
    "사전할당량_latest": "ETRS_사전할당량",
    "인증배출량_latest": "ETRS_인증배출량",
    "추가할당량_latest": "ETRS_추가할당량",
    # 누적 데이터 시트 (변경 시 추가)
    "사전할당량_stack": "사전할당량_이력",
    "인증배출량_stack": "인증배출량_이력",
    "추가할당량_stack": "추가할당량_이력",
}

# 컬럼 정의 (데이터 유형별)
COLUMNS = {
    "사전할당량": [
        "번호", "부문", "업종", "업체명", "유상여부",
        "2021년", "2022년", "2023년", "2024년", "2025년",
    ],
    "인증배출량": [
        "번호", "부문", "업종", "업체명",
        "2021년", "2022년", "2023년", "2024년", "2025년",
    ],
    "추가할당량": [
        "번호", "부문", "업종", "업체명",
        "2021년", "2022년", "2023년", "2024년", "2025년",
    ],
}

# ★★★ 컬럼명 매핑 테이블 (원본 → 표준) ★★★
COLUMN_MAPPING = {
    # 번호/순번
    "NO": "번호",
    "No": "번호",
    "no": "번호",
    "No.": "번호",
    "순번": "번호",
    
    # 업체명
    "업체명": "업체명",
    "회사명": "업체명",
    "사업장명": "업체명",
    "법인명": "업체명",
    
    # 부문/업종
    "부문": "부문",
    "업종": "업종",
    "산업분류": "업종",
    
    # 유상여부
    "유상여부": "유상여부",
    "유상/무상": "유상여부",
    
    # 할당량 관련 (연도별)
    "사전할당량(tCO2eq)": "할당량",
}

# 키 컬럼 (레코드 식별용)
KEY_COLUMNS = {
    "사전할당량": ["업체명", "부문"],
    "인증배출량": ["업체명", "부문"],
    "추가할당량": ["업체명", "부문"],
}

# 값 컬럼 (변경 감지용)
VALUE_COLUMNS = {
    "사전할당량": ["업종", "유상여부", "2021년", "2022년", "2023년", "2024년", "2025년"],
    "인증배출량": ["업종", "2021년", "2022년", "2023년", "2024년", "2025년"],
    "추가할당량": ["업종", "2021년", "2022년", "2023년", "2024년", "2025년"],
}

# HTTP 요청 설정
REQUEST_SETTINGS = {
    "timeout": 120,
    "headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/vnd.ms-excel, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
}

# 재시도 설정
RETRY_SETTINGS = {
    "max_retries": 3,
    "retry_delay": 5,  # seconds
}

# 디버그 설정
DEBUG = {
    "save_files": True,
    "verbose_logging": True,
}
