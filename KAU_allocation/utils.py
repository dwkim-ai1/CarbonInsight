"""
ETRS/ORS Data Scraper Utilities
유틸리티 함수 모음
"""

import logging
import os
import sys
import hashlib

# Absolute imports support
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

# Note: COLUMN_MAPPING is imported here but may cause circular import
# So we handle it carefully


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
        ]
    )
    return logging.getLogger("ETRS_Scraper")


def get_column_mapping() -> Dict[str, str]:
    """Get column mapping (lazy load to avoid circular import)"""
    try:
        from config import COLUMN_MAPPING
        return COLUMN_MAPPING
    except ImportError:
        return {}


def standardize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼명을 표준화된 이름으로 변환
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    column_mapping = get_column_mapping()
    
    original_columns = list(df.columns)
    new_columns = []
    renamed_count = 0
    
    for col in df.columns:
        col_str = str(col).strip()
        new_col = col_str
        
        # 정확한 매핑 먼저 시도
        if col_str in column_mapping:
            new_col = column_mapping[col_str]
            if col_str != new_col:
                renamed_count += 1
                logger.debug(f"컬럼명 변환: '{col_str}' → '{new_col}'")
        
        new_columns.append(new_col)
    
    df.columns = new_columns
    
    if renamed_count > 0:
        logger.info(f"컬럼명 표준화: {renamed_count}개 변환됨")
    
    return df


def get_current_timestamp() -> str:
    """Get current timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_date() -> str:
    """Get current date string"""
    return datetime.now().strftime("%Y-%m-%d")


def get_current_month() -> str:
    """Get current year-month string"""
    return datetime.now().strftime("%Y-%m")


def ensure_directory(path: str) -> None:
    """Ensure directory exists"""
    os.makedirs(path, exist_ok=True)


def calculate_data_hash(df: pd.DataFrame, key_columns: List[str]) -> str:
    """Calculate hash of dataframe for change detection"""
    if df.empty:
        return ""
    
    # Filter to existing key columns
    available_keys = [c for c in key_columns if c in df.columns]
    if not available_keys:
        available_keys = df.columns.tolist()[:2]
    
    df_sorted = df.sort_values(by=available_keys).reset_index(drop=True)
    data_str = df_sorted.to_string()
    return hashlib.md5(data_str.encode()).hexdigest()


def compare_dataframes(
    old_df: pd.DataFrame, 
    new_df: pd.DataFrame, 
    key_columns: List[str],
    value_columns: List[str]
) -> Dict[str, pd.DataFrame]:
    """
    Compare two dataframes and find changes
    
    Args:
        old_df: Previous data
        new_df: New data
        key_columns: Columns to identify unique records
        value_columns: Columns to check for changes
        
    Returns:
        Dictionary with 'added', 'removed', 'changed' DataFrames
    """
    logger = logging.getLogger(__name__)
    
    result = {
        "added": pd.DataFrame(),
        "removed": pd.DataFrame(),
        "changed": pd.DataFrame(),
    }
    
    if old_df.empty:
        result["added"] = new_df.copy()
        return result
    
    if new_df.empty:
        result["removed"] = old_df.copy()
        return result
    
    # Filter to existing columns
    available_key_cols = [c for c in key_columns if c in old_df.columns and c in new_df.columns]
    
    if not available_key_cols:
        logger.warning("키 컬럼을 찾을 수 없음, 전체 데이터를 신규로 처리")
        result["added"] = new_df.copy()
        return result
    
    # Create composite key
    old_keys = old_df[available_key_cols].astype(str).agg('|'.join, axis=1)
    new_keys = new_df[available_key_cols].astype(str).agg('|'.join, axis=1)
    
    # Find added records
    added_mask = ~new_keys.isin(old_keys)
    result["added"] = new_df[added_mask].copy()
    
    # Find removed records
    removed_mask = ~old_keys.isin(new_keys)
    result["removed"] = old_df[removed_mask].copy()
    
    # Find changed records
    common_new = new_df[~added_mask].copy()
    common_old = old_df[~removed_mask].copy()
    
    if not common_new.empty and not common_old.empty:
        # Filter value columns
        available_value_cols = [c for c in value_columns if c in common_new.columns and c in common_old.columns]
        
        if available_value_cols:
            merged = common_new.merge(
                common_old, 
                on=available_key_cols, 
                suffixes=('_new', '_old'),
                how='inner'
            )
            
            changed_mask = pd.Series([False] * len(merged))
            for col in available_value_cols:
                new_col = f"{col}_new" if f"{col}_new" in merged.columns else col
                old_col = f"{col}_old" if f"{col}_old" in merged.columns else col
                if new_col in merged.columns and old_col in merged.columns:
                    col_changed = ~(
                        (merged[new_col].astype(str) == merged[old_col].astype(str)) | 
                        (merged[new_col].isna() & merged[old_col].isna())
                    )
                    changed_mask = changed_mask | col_changed
            
            if changed_mask.any():
                changed_keys = merged[changed_mask][available_key_cols]
                changed_key_str = changed_keys.astype(str).agg('|'.join, axis=1)
                new_key_str = new_df[available_key_cols].astype(str).agg('|'.join, axis=1)
                result["changed"] = new_df[new_key_str.isin(changed_key_str)].copy()
    
    logger.info(f"비교 결과: 신규 {len(result['added'])}건, 삭제 {len(result['removed'])}건, 변경 {len(result['changed'])}건")
    
    return result


def clean_excel_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean Excel data by removing empty rows and standardizing format
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    original_len = len(df)
    
    # Remove completely empty rows
    df = df.dropna(how='all')
    
    # Remove rows where all values are whitespace
    df = df[~df.apply(lambda x: x.astype(str).str.strip().eq('').all(), axis=1)]
    
    # Strip whitespace from string columns
    for col in df.columns:
        try:
            if df[col].dtype == object or str(df[col].dtype) == 'object':
                df[col] = df[col].astype(str).str.strip()
                df[col] = df[col].replace(['nan', 'None', '', 'NaN', 'NaT'], pd.NA)
        except Exception as e:
            logger.debug(f"컬럼 '{col}' 처리 중 오류: {e}")
            continue
    
    # Standardize column names
    df = standardize_column_names(df)
    
    # Reset index
    df = df.reset_index(drop=True)
    
    cleaned_len = len(df)
    if original_len != cleaned_len:
        logger.debug(f"데이터 정제: {original_len}행 → {cleaned_len}행")
    
    return df


def detect_excel_format(content: bytes) -> str:
    """Detect Excel file format from content bytes"""
    if len(content) < 4:
        return 'unknown'
    
    # XLSX files start with PK (zip signature)
    if content[:2] == b'PK':
        return 'xlsx'
    # XLS files start with compound document signature
    elif content[:4] == b'\xd0\xcf\x11\xe0':
        return 'xls'
    else:
        return 'unknown'


def read_excel_auto(content) -> pd.DataFrame:
    """
    Read Excel content with automatic format detection
    
    Args:
        content: bytes 데이터 또는 파일 경로(str)
        
    Returns:
        DataFrame
    """
    from io import BytesIO
    
    logger = logging.getLogger(__name__)
    
    # ★★★ 파일 경로인 경우 파일을 읽어서 bytes로 변환 ★★★
    if isinstance(content, str):
        try:
            with open(content, 'rb') as f:
                content = f.read()
            logger.debug(f"파일에서 Excel 데이터 로드: {len(content)} bytes")
        except Exception as e:
            logger.error(f"파일 읽기 실패: {e}")
            raise
    
    file_format = detect_excel_format(content)
    logger.debug(f"감지된 Excel 형식: {file_format}")
    
    try:
        if file_format == 'xlsx':
            return pd.read_excel(BytesIO(content), engine='openpyxl')
        elif file_format == 'xls':
            return pd.read_excel(BytesIO(content), engine='xlrd')
        else:
            # Try both
            try:
                return pd.read_excel(BytesIO(content), engine='openpyxl')
            except:
                return pd.read_excel(BytesIO(content), engine='xlrd')
    except Exception as e:
        logger.error(f"Excel 읽기 실패: {e}")
        raise


def create_update_summary(
    data_type: str,
    total_records: int,
    added: int,
    removed: int,
    changed: int
) -> str:
    """Create a summary message for update results"""
    return (
        f"[{data_type}] 업데이트 완료:\n"
        f"  - 전체 레코드: {total_records}건\n"
        f"  - 신규 추가: {added}건\n"
        f"  - 삭제: {removed}건\n"
        f"  - 변경: {changed}건"
    )


def convert_period_year_columns(df: pd.DataFrame, plan_period: int) -> pd.DataFrame:
    """
    "N차년도" 형태의 컬럼명을 실제 연도("YYYY년")로 변환
    
    예: 3차 계획기간의 "1차년도 조기감축" → "2021년 조기감축"
    
    Args:
        df: 변환할 DataFrame
        plan_period: 계획기간 (1, 2, 3, 4, 5)
        
    Returns:
        컬럼명이 변환된 DataFrame
    """
    import re
    
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    # 계획기간별 연도 매핑
    period_years = {
        1: [2015, 2016, 2017],
        2: [2018, 2019, 2020],
        3: [2021, 2022, 2023, 2024, 2025],
        4: [2026, 2027, 2028, 2029, 2030],
        5: [2031, 2032, 2033, 2034, 2035],
    }
    
    if plan_period not in period_years:
        logger.warning(f"알 수 없는 계획기간: {plan_period}")
        return df
    
    year_list = period_years[plan_period]
    
    # 컬럼명 변환
    new_columns = []
    converted_count = 0
    
    for col in df.columns:
        col_str = str(col)
        new_col = col_str
        
        # 패턴 1: "N차년도" 또는 "N차 년도" (예: "1차년도", "1차 년도")
        match = re.search(r'(\d)차\s*년도', col_str)
        if match:
            nth_year = int(match.group(1))
            if 1 <= nth_year <= len(year_list):
                actual_year = year_list[nth_year - 1]
                # "1차년도" → "2021년", "1차 년도 조기감축" → "2021년 조기감축"
                new_col = re.sub(r'\d차\s*년도', f'{actual_year}년', col_str)
                converted_count += 1
                logger.debug(f"컬럼 변환: '{col_str}' → '{new_col}'")
        
        # 패턴 2: "N년차" (예: "1년차", "2년차")
        elif re.search(r'(\d)년차', col_str):
            match = re.search(r'(\d)년차', col_str)
            nth_year = int(match.group(1))
            if 1 <= nth_year <= len(year_list):
                actual_year = year_list[nth_year - 1]
                new_col = re.sub(r'\d년차', f'{actual_year}년', col_str)
                converted_count += 1
                logger.debug(f"컬럼 변환: '{col_str}' → '{new_col}'")
        
        new_columns.append(new_col)
    
    if converted_count > 0:
        df.columns = new_columns
        logger.info(f"N차년도 → 실제연도 변환: {converted_count}개 컬럼 ({plan_period}차 계획기간)")
        logger.debug(f"변환된 컬럼: {[c for c in new_columns if '년' in c][:5]}...")
    
    return df


def normalize_year_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    다양한 연도 컬럼 형식을 표준 형식("YYYY년")으로 통일
    
    예: "2021", "2021년도", "'21" → "2021년"
    
    Args:
        df: 변환할 DataFrame
        
    Returns:
        컬럼명이 정규화된 DataFrame
    """
    import re
    
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    new_columns = []
    converted_count = 0
    
    for col in df.columns:
        col_str = str(col).strip()
        new_col = col_str
        
        # 이미 "YYYY년" 형식이면 통과
        if re.match(r'^\d{4}년$', col_str):
            new_columns.append(new_col)
            continue
        
        # "YYYY년도" → "YYYY년"
        match = re.match(r'^(\d{4})년도$', col_str)
        if match:
            new_col = f"{match.group(1)}년"
            converted_count += 1
        
        # "YYYY" (숫자만) → "YYYY년"
        elif re.match(r'^(20\d{2})$', col_str):
            new_col = f"{col_str}년"
            converted_count += 1
        
        new_columns.append(new_col)
    
    if converted_count > 0:
        df.columns = new_columns
        logger.debug(f"연도 컬럼 정규화: {converted_count}개")
    
    return df


def reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼 순서를 표준화된 순서로 정렬
    
    순서:
    1. 번호/순번
    2. 업체명/회사명/법인명
    3. 부문
    4. 업종
    5. 계획기간/배출권거래제 정보
    6. 연도별 데이터 (오름차순: 2015년, 2016년, ...)
    7. 기타 데이터 컬럼
    8. 메타데이터 (_변경유형, _변경일시 등)
    
    Args:
        df: 정렬할 DataFrame
        
    Returns:
        컬럼이 정렬된 DataFrame
    """
    import re
    
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    columns = list(df.columns)
    
    # 그룹별로 컬럼 분류
    priority_cols = []      # 1순위: 번호, 업체명
    entity_cols = []        # 2순위: 부문, 업종
    period_cols = []        # 3순위: 계획기간
    year_cols = []          # 4순위: 연도별 데이터
    other_cols = []         # 5순위: 기타
    meta_cols = []          # 6순위: 메타데이터 (_로 시작)
    
    # 1순위 컬럼 (순서 중요)
    priority_order = ['번호', '순번', 'NO', 'No', 'no', '업체명', '회사명', '법인명', '관리업체명', '사업명', '방법론명']
    
    # 2순위 컬럼
    entity_order = ['부문', '업종', '지정업종', '구분', '지정구분']
    
    # 3순위 컬럼
    period_patterns = ['계획기간', '기간', '배출권거래제', '차', '유상여부']
    
    for col in columns:
        col_str = str(col)
        
        # 메타데이터 컬럼 (맨 뒤로)
        if col_str.startswith('_'):
            meta_cols.append(col)
        # 1순위: 번호, 업체명 계열
        elif col_str in priority_order or any(p in col_str for p in ['번호', '순번', '업체명', '회사명', '법인명']):
            priority_cols.append(col)
        # 2순위: 부문, 업종 계열
        elif col_str in entity_order or col_str in ['부문', '업종']:
            entity_cols.append(col)
        # 3순위: 계획기간 계열
        elif any(p in col_str for p in period_patterns):
            period_cols.append(col)
        # 4순위: 연도 컬럼 (YYYY년, YYYY년 조기감축 등)
        elif re.search(r'\d{4}년', col_str):
            year_cols.append(col)
        # 5순위: 나머지
        else:
            other_cols.append(col)
    
    # 1순위 컬럼을 지정된 순서대로 정렬
    def priority_sort_key(col):
        col_str = str(col)
        for i, p in enumerate(priority_order):
            if p == col_str or p in col_str:
                return i
        return len(priority_order)
    
    priority_cols.sort(key=priority_sort_key)
    
    # 2순위 컬럼을 지정된 순서대로 정렬
    def entity_sort_key(col):
        col_str = str(col)
        for i, e in enumerate(entity_order):
            if e == col_str or e in col_str:
                return i
        return len(entity_order)
    
    entity_cols.sort(key=entity_sort_key)
    
    # 연도 컬럼 오름차순 정렬
    def year_sort_key(col):
        col_str = str(col)
        match = re.search(r'(\d{4})년', col_str)
        if match:
            return int(match.group(1))
        return 9999
    
    year_cols.sort(key=year_sort_key)
    
    # 메타데이터 컬럼 정렬 (_계획기간 → _변경유형 → _변경일시 → _업데이트일시)
    meta_order = ['_계획기간', '_변경유형', '_변경일시', '_업데이트일시']
    def meta_sort_key(col):
        col_str = str(col)
        for i, m in enumerate(meta_order):
            if m == col_str:
                return i
        return len(meta_order)
    
    meta_cols.sort(key=meta_sort_key)
    
    # 최종 순서 조합
    final_order = priority_cols + entity_cols + period_cols + year_cols + other_cols + meta_cols
    
    # 누락된 컬럼 확인 (혹시 분류되지 않은 컬럼)
    missing = [c for c in columns if c not in final_order]
    if missing:
        logger.debug(f"분류되지 않은 컬럼: {missing}")
        final_order = final_order[:len(final_order)-len(meta_cols)] + missing + meta_cols
    
    # 컬럼 재정렬
    final_order = [c for c in final_order if c in df.columns]
    
    if final_order != columns:
        logger.debug(f"컬럼 재정렬: {final_order[:5]}...{final_order[-2:]}")
        return df[final_order]
    
    return df


def save_debug_info(
    content: str, 
    filename: str, 
    debug_dir: str = "debug"
) -> str:
    """Save debug information to file"""
    ensure_directory(debug_dir)
    filepath = os.path.join(debug_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath
