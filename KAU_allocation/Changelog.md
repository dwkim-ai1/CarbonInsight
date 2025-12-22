# KAU_allocation 코드 수정 가이드

## 2025-12-22 (Session 5) - Excel 빈 컬럼 HTML 보완 로직 추가

### 문제
- 국외미활용실적_이력 시트에서 C열(모니터링기간) 데이터가 모두 비어있음
- **원인**: 웹사이트에서 제공하는 **Excel 파일 자체에 모니터링기간 컬럼이 비어있음**
- HTML 테이블에는 데이터가 존재하지만, Excel 다운로드 성공 시 바로 반환하여 HTML 데이터 활용 안 함

### 해결: Excel 데이터 품질 검증 + HTML 보완 로직

#### 기존 로직
```
1. Excel 다운로드 시도
2. 성공하면 바로 반환 ← 빈 컬럼이 있어도 그대로 사용
3. 실패 시 HTML 스크래핑
```

#### 개선된 로직
```
1. Excel 다운로드 시도
2. 성공하면 데이터 품질 검증 (빈 컬럼 체크)
3. 빈 컬럼이 있으면 HTML 스크래핑 시도
4. HTML 데이터로 빈 컬럼 보완
5. 보완된 데이터 반환
```

### 수정 내용

#### etrs_scraper.py

1. **`scrape_ors_table()` 함수 개선**
   - Excel 다운로드 후 `_find_empty_columns()` 호출하여 빈 컬럼 탐지
   - 빈 컬럼 발견 시 HTML 스크래핑하여 `_merge_with_html_data()`로 보완

2. **`_find_empty_columns()` 신규 함수**
   - DataFrame에서 모든 값이 비어있는 컬럼 탐지
   - NaN, 빈 문자열, 공백만 있는 경우 빈 컬럼으로 판단

3. **`_merge_with_html_data()` 신규 함수**
   - Excel과 HTML 데이터 병합
   - 컬럼명 매칭 로직 (정확 매칭 → 부분 매칭 → 키워드 매칭)

4. **`_find_matching_column()` 신규 함수**
   - Excel 컬럼명과 매칭되는 HTML 컬럼명 탐색
   - 매칭 우선순위: 정확 매칭 > 대소문자 무시 > 부분 문자열 > 키워드 기반

5. **`_scrape_ors_html_table()` 함수 개선** (이전 수정)
   - 각 셀을 `inner_text()`로 개별 처리하여 멀티라인 텍스트 지원
   - 줄바꿈 정규화

#### config.py
- 국내/외미활용실적의 EXPECTED_COLUMNS, KEY_COLUMNS, VALUE_COLUMNS를 실제 테이블 구조에 맞게 수정

### 로그 출력 예시
```
📥 ORS 스크래핑 중: 국외미활용실적
✅ ORS Excel 다운로드 성공: 340행
⚠️ Excel에 빈 컬럼 발견: ['모니터링기간']
📋 HTML 스크래핑으로 빈 컬럼 보완 시도...
  ✅ '모니터링기간' ← HTML '모니터링기간' (직접 대체)
✅ 데이터 보완 완료: 340행
```

## 📋 변경 사항 요약

### 문제 1: 이행연도 드롭다운 selector 실패 해결

**원인:**
- 기존 코드: `select[id*="implYear"]`, `select[id*="year"]` 등 사용
- 실제 ETRS HTML: `<select id="pfYy" name="condition.pfYy">`

**해결:**
`etrs_scraper.py`의 `_select_implementation_year` 메서드에서 올바른 selector 사용:

```python
# ★★★ 수정된 selector (실제 ETRS HTML 기반) ★★★
selectors = [
    '#pfYy',                              # 직접 ID 선택
    'select#pfYy',                        # select 태그와 ID 조합
    'select[name="condition.pfYy"]',      # name 속성으로 선택
    'select[name*="pfYy"]',               # name에 pfYy 포함
    'select[id*="pfYy"]',                 # id에 pfYy 포함 (안전장치)
]
```

### 문제 2: 비즈니스 로직 수정

**기존 로직:**
- 매번 모든 연도(2021~2025) 데이터 전체 수집
- 최신 시트에 전체 데이터 replace
- 이력 시트에 변경분 append

**수정된 로직 (자동 수집 모드):**
- 최신 연도(예: 2024년) 데이터만 수집
- 누적 시트의 기존 데이터에서 이미 있는 키 확인
- 기존 데이터에 없는 새 데이터만 append
- 이력 시트에는 새로 추가된 데이터만 append

**핵심 포인트:**
- 기존 데이터(2021~2023년)는 절대 건드리지 않음
- 새로 수집한 데이터 중 기존에 없는 것만 필터링하여 추가

## 📁 수정된 파일 목록

### 1. `etrs_scraper.py`
- `_select_implementation_year()`: 올바른 이행연도 selector 사용
- `download_etrs_dataset()`: `current_year_only` 파라미터 추가
- `download_all_etrs()`: `current_year_only` 파라미터 추가
- `download_all()`: `current_year_only` 파라미터 추가
- `run_scraper()`: `current_year_only` 파라미터 추가

### 2. `main.py`
- `CURRENT_YEAR_ONLY` 환경변수 지원
- 자동 수집 모드에서 `process_etrs_update_incremental()` 사용

### 3. `google_sheets_handler.py`
- `process_etrs_update_incremental()`: 증분 업데이트 메서드 추가
- `_merge_implementation_year_data()`: 이행연도 기반 merge 로직
- `_merge_by_key()`: 키 기반 merge 로직

### 4. `.github/workflows/update_allocation_data.yml`
- `current_year_only` 입력 옵션 추가
- `CURRENT_YEAR_ONLY` 환경변수 설정
- 자동 실행(schedule)에서는 기본적으로 `CURRENT_YEAR_ONLY=true`

## 🚀 적용 방법

### 방법 1: 파일 직접 교체
1. 첨부된 파일들을 다운로드
2. 기존 `KAU_allocation/` 디렉토리의 파일들을 백업
3. 새 파일들로 교체

### 방법 2: 부분 수정
기존 코드에서 아래 부분만 수정:

#### etrs_scraper.py - `_select_implementation_year` 메서드
```python
async def _select_implementation_year(self, page, year: int) -> None:
    """
    Select implementation year (이행연도) from dropdown
    
    ★★★ 수정됨: 실제 ETRS HTML에 맞는 selector 사용 ★★★
    """
    try:
        # ★★★ 올바른 이행연도 selector (실제 ETRS HTML 기반) ★★★
        selectors = [
            '#pfYy',                              # 직접 ID 선택
            'select#pfYy',                        # select 태그와 ID 조합
            'select[name="condition.pfYy"]',      # name 속성으로 선택
            'select[name*="pfYy"]',               # name에 pfYy 포함
            'select[id*="pfYy"]',                 # id에 pfYy 포함 (안전장치)
        ]
        
        for selector in selectors:
            try:
                select = page.locator(selector).first
                count = await select.count()
                
                if count > 0:
                    # 옵션 확인
                    options = await select.locator('option').all_text_contents()
                    logger.debug(f"이행연도 옵션 ({selector}): {options}")
                    
                    # 연도로 선택 시도 (value 속성)
                    try:
                        await select.select_option(value=str(year))
                        logger.info(f"✓ 이행연도 선택 성공: {year}년 (selector: {selector})")
                        await asyncio.sleep(1)
                        return
                    except:
                        pass
                    
                    # label로 시도
                    try:
                        await select.select_option(label=str(year))
                        logger.info(f"✓ 이행연도 선택 성공: {year}년 (label)")
                        await asyncio.sleep(1)
                        return
                    except:
                        pass
                        
            except Exception as e:
                logger.debug(f"selector '{selector}' 시도 실패: {e}")
                continue
        
        logger.warning(f"⚠️ 이행연도 선택 실패: {year}년")
        
    except Exception as e:
        logger.error(f"❌ 이행연도 선택 오류: {e}")
```

## 🔧 환경변수 설정

### GitHub Actions Secrets (기존과 동일)
- `GOOGLE_SHEETS_CREDS`: Google 서비스 계정 JSON
- `KAU_SHEET_ID`: 스프레드시트 ID

### 새로운 환경변수
- `CURRENT_YEAR_ONLY`: `true`로 설정하면 현재 연도만 수집

## 📊 동작 방식

### 자동 수집 모드 (schedule 실행)
1. `CURRENT_YEAR_ONLY=true` 자동 설정
2. 최신 연도(2024년) 데이터만 수집
3. 기존 ETRS_인증배출량 시트에서 이미 있는 데이터 키 확인
4. 새로 수집한 데이터 중 기존에 없는 것만 필터링
5. 기존 데이터 유지 + 새 데이터 append
6. 결과: 기존 2021~2023년 데이터 유지 + 2024년 신규 데이터 추가

**예시:**
```
기존 시트: 업체A-2021, 업체A-2022, 업체A-2023, 업체B-2021...
새로 수집: 업체A-2024, 업체B-2024, 업체C-2024...
→ 기존에 없는 2024년 데이터만 append
```

### 수동 실행 모드
- `current_year_only: true` 선택 시: 자동 수집 모드와 동일
- `current_year_only: false` 선택 시: 전체 데이터 수집 및 replace

## ⚠️ 주의사항

1. **첫 실행 시**: 기존 데이터가 없으면 새 데이터만 저장됩니다.
2. **이행연도 데이터셋**: 인증배출량, 배출권이월량, 배출권차입량
   - 키: 업체명 + 부문 + 이행연도
   - 기존에 없는 (업체명, 부문, 이행연도) 조합만 추가
3. **일반 데이터셋**: 사전할당량, 추가할당량 등은 기존 로직 유지 (전체 replace)
4. **ORS 데이터**: 항상 전체 교체 (계획기간/이행연도 없음)
5. **중복 방지**: 이미 시트에 있는 데이터는 다시 추가되지 않음

## 🧪 테스트 권장 사항

1. 수동 실행으로 `debug_mode: true`, `current_year_only: true` 테스트
2. 디버그 스크린샷에서 이행연도 선택 확인
3. Google Sheets에서 데이터 merge 결과 확인
