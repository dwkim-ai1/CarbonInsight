# KAU_allocation - ETRS/ORS 데이터 스크래퍼

배출권등록부(ETRS) 및 상쇄등록부(ORS) 시스템에서 데이터를 자동 수집하여 Google Sheets에 업데이트하는 모듈입니다.

## 📊 수집 대상 (총 16개 데이터셋, 32개 시트)

### ETRS (배출권등록부) - 8개 데이터셋
| 데이터셋 | 최신 시트 | 이력 시트 |
|---------|----------|----------|
| 배출권 총수량 | ETRS_배출권총수량 | 배출권총수량_이력 |
| 조기감축실적 | ETRS_조기감축실적 | 조기감축실적_이력 |
| 인증배출량 | ETRS_인증배출량 | 인증배출량_이력 |
| 사전할당량 | ETRS_사전할당량 | 사전할당량_이력 |
| 추가할당량 | ETRS_추가할당량 | 추가할당량_이력 |
| 할당취소량 | ETRS_할당취소량 | 할당취소량_이력 |
| 배출권이월량 | ETRS_배출권이월량 | 배출권이월량_이력 |
| 배출권차입량 | ETRS_배출권차입량 | 배출권차입량_이력 |

### ORS (상쇄등록부) - 8개 데이터셋
| 데이터셋 | 최신 시트 | 이력 시트 |
|---------|----------|----------|
| 상쇄배출권발행량 | ORS_상쇄배출권발행량 | 상쇄배출권발행량_이력 |
| 방법론현황 | ORS_방법론현황 | 방법론현황_이력 |
| 국내사업 | ORS_국내사업 | 국내사업_이력 |
| 국외사업 | ORS_국외사업 | 국외사업_이력 |
| 국내인증실적 | ORS_국내인증실적 | 국내인증실적_이력 |
| 국외인증실적 | ORS_국외인증실적 | 국외인증실적_이력 |
| 국내미활용실적 | ORS_국내미활용실적 | 국내미활용실적_이력 |
| 국외미활용실적 | ORS_국외미활용실적 | 국외미활용실적_이력 |

## 🗂️ 파일 구조

```
KAU_allocation/
├── __init__.py              # 패키지 초기화
├── config.py                # 설정 파일 (16개 데이터셋 정의)
├── utils.py                 # 유틸리티 함수
├── etrs_scraper.py          # Playwright 기반 스크래퍼
├── google_sheets_handler.py # Google Sheets 처리
├── main.py                  # 메인 실행 파일
└── README.md                # 이 파일
```

## ⚙️ 설정

### GitHub Secrets
| Secret 이름 | 설명 |
|------------|------|
| `GOOGLE_SHEETS_CREDS` | Google Service Account JSON 키 |
| `KAU_SHEET_ID` | Google Sheets 스프레드시트 ID |

### 환경변수
| 변수명 | 설명 | 기본값 |
|-------|------|--------|
| `ETRS_ONLY` | ETRS만 수집 | `false` |
| `ORS_ONLY` | ORS만 수집 | `false` |
| `ETRS_PERIODS` | 수집할 계획기간 | `1,2,3` |
| `DEBUG_MODE` | 디버그 모드 | `false` |

## 🚀 실행 방법

### GitHub Actions (자동)
- 매주 월요일 오전 9시(KST) 자동 실행
- Actions 탭에서 수동 실행 가능

### 로컬 실행
```bash
# 의존성 설치
pip install -r requirements/requirements_allocation.txt
playwright install chromium

# 환경변수 설정
export GOOGLE_SHEETS_CREDS='{"type": "service_account", ...}'
export KAU_SHEET_ID='your-spreadsheet-id'

# 실행
cd KAU_allocation
python main.py
```

## 📋 시트 구조

### 최신 데이터 시트 (ETRS_*, ORS_*)
- 매 실행 시 전체 데이터로 교체
- 가장 최신 데이터 상태 반영

### 이력 시트 (*_이력)
- 변경 사항만 누적 기록
- `_변경유형`: 신규/변경
- `_변경일시`: 변경 감지 시간
- `_계획기간`: ETRS 데이터의 계획기간 (1차/2차/3차)

## 📅 업데이트 주기

- **자동 실행**: 매주 월요일 00:00 UTC (09:00 KST)
- **계획기간**: 1차(2015-2017), 2차(2018-2020), 3차(2021-2025)
