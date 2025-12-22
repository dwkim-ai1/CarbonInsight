# KAU_allocation 코드 수정 가이드

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
