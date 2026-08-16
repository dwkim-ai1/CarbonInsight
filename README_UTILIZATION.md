# DART 최근 10년 가동률 수집기

OpenDART 정기공시에서 생산능력·생산실적·가동률 원자료를 수집하여 기업 20개씩 Google
Sheets 10개에 저장합니다. 결정론 파서가 명시 가동률, 가동시간 비율, 본문 서술 순으로
검사하며, 모두 실패한 표만 Ollama Cloud 폴백에 전달합니다. 파싱 실패 시 샘플 데이터는
절대 만들지 않습니다.

## 사전 준비

GitHub Secrets에 `opendart_api`, `GOOGLE_SHEETS_CREDS`, `gspread_list`,
`gspread_id_1` … `gspread_id_10`, `CARBON_TOKEN`, `ESG_TESTER`를 등록합니다.
대상 문서는 개인 계정 `dwkim.digital@gmail.com`뿐 아니라
`GOOGLE_SHEETS_CREDS` JSON의 `client_email`에도 **편집자**로 공유해야 합니다. 빠뜨리면
`gspread.exceptions.SpreadsheetNotFound` 또는 Google API `403 PERMISSION_DENIED`가
발생합니다.

`OLLAMA_API_KEY`는 선택 사항입니다. 브라우저에서 **ollama.com → Settings → Keys →
Create key** 순서로 이동하고, 표시된 키를 복사하여 같은 이름의 GitHub Secret으로
등록합니다. 화면에서는 (1) 좌측 Settings, (2) Keys 탭, (3) Create key 버튼, (4) 한 번만
표시되는 키 복사 순서입니다. 키가 없으면 경고만 남기고 결정론 모드로 계속됩니다.

## 실행

Actions의 **DART utilization time-series collector**에서 Run workflow를 선택합니다.
초기 검증은 `target_shard=1`, `years_back=1`, `test_mode=true`를 권장합니다. 예약 실행은
4월 1일·5월 20일·9월 1일·11월 20일 09:00 KST입니다.

로컬 검증(실제 자격 증명 필요):

```bash
python scripts/check_sheet_schema.py
TARGET_SHARD=1 YEARS_BACK=1 TEST_MODE=true python utilization_collector.py
```

9번 통합 문서의 첫 워크시트 헤더가 런타임 표준입니다. 워크시트가 하나이고 헤더에
`기업명`이 있으면 통합 방식 B, 그 외에는 기업별 워크시트 방식 A입니다. 최소 필수 컬럼은
감지한 헤더 뒤에 보충됩니다. 목록의 `그룹`/`묶음`/`bucket`이 있으면 우선하고, 없으면
행 순서로 20개씩 배정합니다.

## LLM 감사 및 안전장치

폴백은 `gemma4:31b-cloud`, temperature 0, 8192 context를 사용합니다. 응답의 숫자는
원문 부분 문자열과 대조하고, 없는 값이나 0~200% 밖의 가동률을 `null`로 강등합니다.
JSON 오류, 불확실 결과, 호출 오류는 상태 코드로 반환됩니다. 실제 API를 호출하지 않는
모킹 테스트는 `pytest tests/test_llm_fallback.py -v`로 실행합니다.

운영 전에는 서비스 계정 접근, 스키마 출력, shard 1 dry-run, 실제 폴백 1건과 DART 원문
수기 대조를 차례로 수행하십시오. 실제 API/시트 검증은 저장소에 자격 증명이 없으면
로컬 또는 GitHub Actions에서만 가능합니다.
