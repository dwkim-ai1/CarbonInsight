# NGMS Data Scraper (KAU Participant Module)

국가온실가스종합관리시스템(NGMS) 데이터를 자동으로 수집하여 Google Sheets에 업데이트하는 모듈입니다.

## 📋 개요

이 모듈은 [NGMS 정보공개 페이지](https://ngms.gir.go.kr:8443/subMain.do?link=/hom/bbs/OGCMBBS021V.xml&menuNo=50900501)에서 다음 데이터를 수집합니다:

| 데이터 유형 | 설명 | 업데이트 시트 | 누적 시트 |
|------------|------|--------------|----------|
| 할당대상업체 | 배출권 할당 대상 업체 목록 | NGMS_할당대상업체 | 할당대상업체 |
| 목표관리대상업체 | 목표관리제 대상 업체 목록 | NGMS_목표관리대상업체 | 목표관리대상업체 |
| 명세서배출량통계 | 배출량 명세서 통계 | NGMS_명세서배출량통계 | 명세서배출량통계 |

## 🗂️ 파일 구조

```
├── .github/
│   └── workflows/
│       └── ngms_update.yml      # GitHub Actions 워크플로우
├── KAU_participant/
│   ├── __init__.py              # 패키지 초기화
│   ├── config.py                # 설정 파일
│   ├── utils.py                 # 유틸리티 함수
│   ├── google_sheets_handler.py # Google Sheets 처리
│   ├── ngms_scraper.py          # 웹 스크래퍼
│   ├── main.py                  # 메인 실행 파일
│   └── debug_scraper.py         # 디버깅 스크립트
└── requirements/
    └── requirements_NGMS.txt    # 의존성 패키지
```

## ⚙️ 설정

### GitHub Secrets 설정

다음 secrets를 GitHub repository에 설정해야 합니다:

| Secret 이름 | 설명 |
|------------|------|
| `GOOGLE_SHEETS_CREDS` | Google Service Account JSON 키 |
| `KAU_SHEET_ID` | Google Sheets 스프레드시트 ID |

### Google Service Account 설정

1. [Google Cloud Console](https://console.cloud.google.com)에서 프로젝트 생성
2. Google Sheets API 활성화
3. Service Account 생성 및 JSON 키 다운로드
4. 대상 스프레드시트에 Service Account 이메일 공유

## 🚀 실행 방법

### 자동 실행 (GitHub Actions)

매월 1일 오전 9시 (KST)에 자동으로 실행됩니다.

수동 실행도 가능합니다:
1. GitHub repository의 Actions 탭으로 이동
2. "NGMS Data Update" 워크플로우 선택
3. "Run workflow" 버튼 클릭

### 로컬 실행

```bash
# 의존성 설치
pip install -r requirements/requirements_NGMS.txt

# Playwright 브라우저 설치
playwright install chromium

# 환경 변수 설정
export GOOGLE_SHEETS_CREDS='{"type": "service_account", ...}'
export KAU_SHEET_ID='your-spreadsheet-id'

# 실행
cd KAU_participant
python main.py
```

### 디버깅 모드

```bash
# 디버깅 스크립트 실행 (브라우저 표시)
python debug_scraper.py --headed

# 또는 환경 변수로 디버그 모드 활성화
export DEBUG_MODE=true
python main.py
```

## 📊 데이터 흐름

```
┌─────────────────┐
│   NGMS 웹사이트  │
│   (Excel 다운로드) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Playwright     │
│  (웹 스크래핑)   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  데이터 파싱    │
│  (Pandas)       │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌───────┐
│ 최신   │ │ 누적   │
│ 시트   │ │ 시트   │
│(전체)  │ │(변경분)│
└───────┘ └───────┘
```

## 🔧 트러블슈팅

### iframe 관련 문제

NGMS 웹사이트는 iframe 구조를 사용합니다. `debug_scraper.py`를 실행하여 페이지 구조를 분석할 수 있습니다:

```bash
python debug_scraper.py --headed
```

이 스크립트는 다음을 수행합니다:
- 페이지 구조 분석
- 모든 iframe 목록 출력
- 다운로드 버튼 검색
- 스크린샷 및 HTML 저장

### 다운로드 실패 시

1. `debug_output/` 폴더의 스크린샷과 HTML 확인
2. 셀렉터가 변경되었는지 확인
3. `config.py`의 셀렉터 업데이트

### Google Sheets 연결 오류

1. Service Account JSON 키 확인
2. 스프레드시트 공유 권한 확인
3. API 할당량 확인

## 📝 데이터 컬럼

### 할당대상업체
- 순번, 계획기간, 지정연도, 업종, 업체명, 소재지, 적용기준

### 목표관리대상업체
- 순번, 지정연도, 관장기관, 관리업체명, 지정업종, 주소, 지정구분, 고시이력

### 명세서배출량통계
- 번호, 관장기관, 법인명, 대상년도, 지정구분, 지정업종, 가스 배출량(tCO2), 에너지 사용량(TJ), 검증수행기관, 비고

## 📅 업데이트 주기

- **자동 실행**: 매월 1일 00:00 UTC (09:00 KST)
- **NGMS_ 시트**: 최신 데이터로 전체 교체
- **누적 시트**: 변경된 레코드만 추가

## ⚠️ 주의사항

1. NGMS 웹사이트 구조 변경 시 스크래퍼 수정 필요
2. 대용량 데이터 처리 시 Google Sheets API 할당량 주의
3. 디버그 모드에서 민감한 정보 노출 주의

## 📄 라이선스

MIT License
