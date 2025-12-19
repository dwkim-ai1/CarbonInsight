"""
NGMS Data Scraper Utilities
유틸리티 함수 모음
"""

import logging
import os
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

from config import COLUMN_MAPPING

# Configure logging
def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
        ]
    )
    return logging.getLogger("NGMS_Scraper")


def standardize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼명을 표준화된 이름으로 변환
    
    Args:
        df: DataFrame with original column names
        
    Returns:
        DataFrame with standardized column names
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    # 원본 컬럼명 저장
    original_columns = list(df.columns)
    
    # 새로운 컬럼명 생성
    new_columns = []
    renamed_count = 0
    
    for col in df.columns:
        col_str = str(col).strip()
        
        # 매핑 테이블에서 찾기
        if col_str in COLUMN_MAPPING:
            new_col = COLUMN_MAPPING[col_str]
            new_columns.append(new_col)
            if col_str != new_col:
                renamed_count += 1
                logger.debug(f"컬럼명 변환: '{col_str}' → '{new_col}'")
        else:
            new_columns.append(col_str)
    
    # 컬럼명 적용
    df.columns = new_columns
    
    if renamed_count > 0:
        logger.info(f"컬럼명 표준화: {renamed_count}개 변환됨")
        logger.info(f"  변환 전: {original_columns[:5]}...")
        logger.info(f"  변환 후: {new_columns[:5]}...")
    
    return df


def get_current_timestamp() -> str:
    """Get current timestamp in Korean timezone"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_date() -> str:
    """Get current date string"""
    return datetime.now().strftime("%Y-%m-%d")


def get_current_month() -> str:
    """Get current year-month string"""
    return datetime.now().strftime("%Y-%m")


def calculate_data_hash(df: pd.DataFrame, key_columns: List[str]) -> str:
    """
    Calculate hash of dataframe based on key columns for change detection
    
    Args:
        df: DataFrame to hash
        key_columns: Columns to use for hashing
        
    Returns:
        Hash string
    """
    if df.empty:
        return ""
    
    # Sort by key columns for consistent hashing
    df_sorted = df.sort_values(by=key_columns).reset_index(drop=True)
    
    # Create hash from string representation
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
    
    # Ensure key columns exist in both dataframes
    for col in key_columns:
        if col not in old_df.columns or col not in new_df.columns:
            raise ValueError(f"Key column '{col}' not found in dataframes")
    
    # Create composite key for comparison
    old_keys = old_df[key_columns].astype(str).agg('|'.join, axis=1)
    new_keys = new_df[key_columns].astype(str).agg('|'.join, axis=1)
    
    # Find added records
    added_mask = ~new_keys.isin(old_keys)
    result["added"] = new_df[added_mask].copy()
    
    # Find removed records
    removed_mask = ~old_keys.isin(new_keys)
    result["removed"] = old_df[removed_mask].copy()
    
    # Find changed records (same keys but different values)
    common_new = new_df[~added_mask].copy()
    common_old = old_df[~removed_mask].copy()
    
    if not common_new.empty and not common_old.empty:
        # Merge on key columns to compare
        merged = common_new.merge(
            common_old, 
            on=key_columns, 
            suffixes=('_new', '_old'),
            how='inner'
        )
        
        changed_mask = pd.Series([False] * len(merged))
        for col in value_columns:
            if col in common_new.columns and col in common_old.columns:
                new_col = f"{col}_new" if f"{col}_new" in merged.columns else col
                old_col = f"{col}_old" if f"{col}_old" in merged.columns else col
                if new_col in merged.columns and old_col in merged.columns:
                    # Compare values, treating NaN as equal
                    col_changed = ~(
                        (merged[new_col] == merged[old_col]) | 
                        (merged[new_col].isna() & merged[old_col].isna())
                    )
                    changed_mask = changed_mask | col_changed
        
        if changed_mask.any():
            # Get the new versions of changed records
            changed_keys = merged[changed_mask][key_columns]
            changed_key_str = changed_keys.astype(str).agg('|'.join, axis=1)
            new_key_str = new_df[key_columns].astype(str).agg('|'.join, axis=1)
            result["changed"] = new_df[new_key_str.isin(changed_key_str)].copy()
    
    return result


def clean_excel_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean Excel data by removing empty rows and standardizing format
    
    Args:
        df: Raw DataFrame from Excel
        
    Returns:
        Cleaned DataFrame
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    # ★★★ 헤더 키워드 목록 (이 키워드가 있으면 헤더 행으로 판단) ★★★
    HEADER_KEYWORDS = ['순번', 'NO', '번호', '업체명', '법인명', '관리업체명', '지정연도', '대상년도', '대상연도']
    
    def is_valid_header_row(row):
        """행이 유효한 헤더인지 확인"""
        row_values = [str(v).strip().upper() for v in row if pd.notna(v) and str(v).strip()]
        for keyword in HEADER_KEYWORDS:
            if keyword.upper() in row_values or any(keyword.upper() in v for v in row_values):
                return True
        return False
    
    def count_valid_cells(row):
        """유효한 셀 개수"""
        return sum(1 for v in row if pd.notna(v) and str(v).strip() and str(v).lower() not in ['nan', 'none', 'nat', ''])
    
    # ★★★ 컬럼명이 문제인지 확인 ★★★
    unnamed_count = sum(1 for col in df.columns if 'Unnamed' in str(col))
    numeric_col_count = sum(1 for col in df.columns if isinstance(col, (int, float)) or str(col).isdigit())
    nan_col_count = sum(1 for col in df.columns if str(col).lower() == 'nan')
    
    needs_header_fix = (unnamed_count > len(df.columns) // 2 or 
                        numeric_col_count > len(df.columns) // 2 or 
                        nan_col_count > len(df.columns) // 2)
    
    if needs_header_fix:
        logger.info(f"헤더 자동 수정 필요: Unnamed={unnamed_count}, 숫자컬럼={numeric_col_count}, nan컬럼={nan_col_count}")
        
        # ★★★ 키워드 기반으로 유효한 헤더 행 찾기 ★★★
        header_row_idx = None
        max_search = min(15, len(df))  # 최대 15행까지 검색
        
        for i in range(max_search):
            row = df.iloc[i]
            
            # 키워드가 포함된 행 찾기
            if is_valid_header_row(row):
                header_row_idx = i
                logger.info(f"키워드 기반 헤더 발견: {i}행")
                break
        
        # 키워드로 못 찾으면 유효 셀 개수 기반
        if header_row_idx is None:
            for i in range(max_search):
                row = df.iloc[i]
                valid_count = count_valid_cells(row)
                
                # 절반 이상이 유효한 값이면 헤더로 사용
                if valid_count > len(row) // 2:
                    header_row_idx = i
                    logger.info(f"유효 셀 기반 헤더 발견: {i}행 (유효 셀: {valid_count}/{len(row)})")
                    break
        
        # 헤더 행 적용
        if header_row_idx is not None and header_row_idx < len(df):
            new_header = df.iloc[header_row_idx].astype(str).tolist()
            df = df.iloc[header_row_idx + 1:].reset_index(drop=True)
            df.columns = new_header
            logger.info(f"새 헤더 적용: {new_header[:5]}...")
        else:
            logger.warning("유효한 헤더를 찾지 못함")
    
    # ★★★ 컬럼명이 여전히 nan이면 한 번 더 시도 ★★★
    nan_col_count = sum(1 for col in df.columns if str(col).lower() == 'nan')
    if nan_col_count > len(df.columns) // 2 and len(df) > 0:
        logger.warning(f"여전히 nan 컬럼 많음 ({nan_col_count}개), 키워드 기반 재시도")
        
        for i in range(min(10, len(df))):
            row = df.iloc[i]
            if is_valid_header_row(row):
                new_header = row.astype(str).tolist()
                df = df.iloc[i + 1:].reset_index(drop=True)
                df.columns = new_header
                logger.info(f"재시도 성공, 새 헤더: {new_header[:5]}...")
                break
    
    # ★★★ 타이틀 행 제거 ★★★
    if len(df.columns) > 0:
        first_col = str(df.columns[0])
        if '현황' in first_col or '건)' in first_col or '통계' in first_col or '기준' in first_col:
            logger.info(f"타이틀 행 감지: '{first_col}'")
            
            for i in range(min(5, len(df))):
                row = df.iloc[i]
                if is_valid_header_row(row):
                    new_header = row.astype(str).tolist()
                    df = df.iloc[i + 1:].reset_index(drop=True)
                    df.columns = new_header
                    logger.info(f"새 헤더: {new_header[:5]}...")
                    break
    
    # Remove completely empty rows
    df = df.dropna(how='all')
    
    # Remove rows where all values are whitespace
    df = df[~df.apply(lambda x: x.astype(str).str.strip().eq('').all(), axis=1)]
    
    # ★★★ Strip whitespace from string columns ★★★
    for col in df.columns:
        try:
            if df[col].dtype == object or str(df[col].dtype) == 'object':
                df[col] = df[col].astype(str).str.strip()
                df[col] = df[col].replace(['nan', 'None', '', 'NaN', 'NaT'], pd.NA)
        except Exception as e:
            logger.debug(f"컬럼 '{col}' 처리 중 오류: {e}")
            continue
    
    # ★★★ 컬럼명 표준화 ★★★
    df = standardize_column_names(df)
    
    # Reset index
    df = df.reset_index(drop=True)
    
    return df


def ensure_directory(path: str) -> None:
    """Ensure directory exists"""
    os.makedirs(path, exist_ok=True)


def save_debug_info(
    content: str, 
    filename: str, 
    debug_dir: str = "debug"
) -> str:
    """
    Save debug information to file
    
    Args:
        content: Content to save
        filename: Filename to use
        debug_dir: Directory for debug files
        
    Returns:
        Full path to saved file
    """
    ensure_directory(debug_dir)
    filepath = os.path.join(debug_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return filepath


def format_log_message(
    action: str, 
    data_type: str, 
    details: Optional[Dict[str, Any]] = None
) -> str:
    """
    Format a log message with consistent structure
    
    Args:
        action: Action being performed
        data_type: Type of data (할당대상업체, etc.)
        details: Additional details
        
    Returns:
        Formatted log message
    """
    message = f"[{data_type}] {action}"
    if details:
        detail_str = ", ".join(f"{k}={v}" for k, v in details.items())
        message += f" - {detail_str}"
    return message


def validate_excel_columns(
    df: pd.DataFrame, 
    expected_columns: List[str],
    data_type: str
) -> bool:
    """
    Validate that Excel data has expected columns
    
    Args:
        df: DataFrame to validate
        expected_columns: Expected column names
        data_type: Type of data for error message
        
    Returns:
        True if valid, raises exception if not
    """
    missing_cols = set(expected_columns) - set(df.columns)
    if missing_cols:
        raise ValueError(
            f"{data_type} Excel 데이터에 필수 컬럼이 없습니다: {missing_cols}"
        )
    return True


def create_update_summary(
    data_type: str,
    total_records: int,
    added: int,
    removed: int,
    changed: int
) -> str:
    """
    Create a summary message for update results
    
    Args:
        data_type: Type of data
        total_records: Total records in new data
        added: Number of added records
        removed: Number of removed records
        changed: Number of changed records
        
    Returns:
        Summary message
    """
    return (
        f"[{data_type}] 업데이트 완료:\n"
        f"  - 전체 레코드: {total_records}건\n"
        f"  - 신규 추가: {added}건\n"
        f"  - 삭제: {removed}건\n"
        f"  - 변경: {changed}건"
    )
