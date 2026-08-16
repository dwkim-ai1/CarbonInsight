# DART 최근 10년 가동률 수집기

OpenDART 정기공시에서 생산능력·생산실적·가동률 원자료를 수집하여 기업 20개씩 Google
Sheets 10개에 저장합니다. 결정론 파서가 명시 가동률, 가동시간 비율, 본문 서술 순으로
검사하며, 모두 실패한 표만 Ollama Cloud 폴백에 전달합니다. 파싱 실패 시 샘플 데이터는
절대 만들지 않습니다.

기본 적재 방식은 기업별 row framework JSON을 먼저 만든 뒤, 각 정기보고서의 분기 값을
해당 framework selector에 매칭되는 행에만 업데이트하는 방식입니다. framework JSON은 각
결과 문서의 `_framework` 워크시트에 회사별로 저장되며 다음 실행부터 재사용합니다.
값 업데이트 단계에서는 기본적으로 새 행을 만들지 않고, framework 밖의 파서 결과는
장부에 skip으로 남깁니다.

## 사전 준비

GitHub Secrets에 `opendart_api`, `GOOGLE_SHEETS_CREDS`, `gspread_list`,
`gspread_ids`, `CARBON_TOKEN`, `ESG_TESTER`를 등록합니다. `gspread_ids`는 결과
Google Sheets 10개의 ID를 순서대로 담은 JSON 배열 문자열입니다.

```json
["id1","id2","id3","id4","id5","id6","id7","id8","id9","id10"]
```

배열 순서는 shard 1부터 shard 10까지 대응합니다. 9번째 ID는 런타임 표준 스키마를
판정하는 기준 문서로도 사용됩니다.

기업 목록 문서(`gspread_list`)의 첫 워크시트는 `회사명`, `상장코드`, `Gspread_ID` 헤더를
지원합니다. `corp_code`가 있으면 그대로 사용하고, 없으면 `상장코드` 또는 회사명으로
OpenDartReader의 DART 고유번호 조회를 수행합니다. `Gspread_ID` 값이 `gspread_ids` 배열의
ID와 일치하면 해당 순번의 shard로 배정하고, 없으면 행 순서 기준으로 20개씩 배정합니다.

대상 문서는 개인 계정 `dwkim.digital@gmail.com`뿐 아니라
`GOOGLE_SHEETS_CREDS` JSON의 `client_email`에도 **편집자**로 공유해야 합니다. 빠뜨리면
`gspread.exceptions.SpreadsheetNotFound` 또는 Google API `403 PERMISSION_DENIED`가
발생합니다.

`OLLAMA_API_KEY`는 선택 사항입니다. 브라우저에서 **ollama.com → Settings → Keys →
Create key** 순서로 이동하고, 표시된 키를 복사하여 같은 이름의 GitHub Secret으로
등록합니다. 화면에서는 (1) 좌측 Settings, (2) Keys 탭, (3) Create key 버튼, (4) 한 번만
표시되는 키 복사 순서입니다. 키가 없으면 경고만 남기고 결정론 모드로 계속됩니다.

## Google Sheets quota

Google Sheets API의 서비스 계정은 사용자 단위 분당 quota를 공유하므로, 수집기는 기본적으로
읽기 `45/min`, 쓰기 `30/min`으로 API 호출 간격을 제한합니다. GitHub Actions Variables에
다음 값을 설정하면 조정할 수 있습니다.

```text
GOOGLE_SHEETS_READ_REQUESTS_PER_MINUTE=45
GOOGLE_SHEETS_WRITE_REQUESTS_PER_MINUTE=30
```

## 실행

Actions의 **DART utilization time-series collector**에서 Run workflow를 선택합니다.
초기 검증은 `target_shard=1`, `years_back=1`, `test_mode=true`를 권장합니다. 예약 실행은
4월 1일·5월 20일·9월 1일·11월 20일 09:00 KST입니다.

`force_reprocess=true`로 실행하면 저장된 framework JSON을 무시하고 첫 정기보고서에서
framework를 다시 생성합니다. 기본값에서는 기존 `_framework` JSON을 재사용하여 LLM 호출을
기업당 최초 1회 수준으로 제한합니다.

초기 백필처럼 정밀 매핑이 필요하면 `llm_per_report_mode=true`로 실행합니다. 이 모드는
정기보고서 1건마다 `gemma4:31b-cloud`를 호출해 기존 framework row에 맞는 값만 큐에
넣습니다. 새 row를 만들지 않으므로 먼저 framework가 필요하며, `_framework`가 없거나
기존 구조를 버리고 다시 잡으려면 `force_reprocess=true`를 함께 사용합니다. 기본 호출
상한은 전체 `10000`, 기업당 `60`이며 GitHub Actions Variables의
`LLM_PER_REPORT_MAX_CALLS`, `LLM_PER_REPORT_MAX_CALLS_PER_COMPANY`로 조정할 수 있습니다.
`years_back=10`은 현재 사업연도를 포함한 최근 10개 사업연도만 조회합니다.
보고서별 LLM 모드는 오래 걸릴 수 있으므로 `target_shard=1`, `company_offset=0`,
`company_limit=2`처럼 작은 기업 묶음부터 실행한 뒤 offset을 늘려 이어가는 방식을
권장합니다.

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
