"""
ETRS Data Scraper Utilities
유틸리티 함수 모음
"""

import logging
import os
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
import pandas as pd

from config import COLUMN_MAPPING


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


def standardize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """
    컬럼명을 표준화된 이름으로 변환
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
    original_columns = list(df.columns)
    new_columns = []
    renamed_count = 0
    
    for col in df.columns:
        col_str = str(col).strip()
        new_col = col_str
        
        # 정확한 매핑 먼저 시도
        if col_str in COLUMN_MAPPING:
            new_col = COLUMN_MAPPING[col_str]
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
    
    df_sorted = df.sort_values(by=key_columns).reset_index(drop=True)
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
    
    # Ensure key columns exist
    for col in key_columns:
        if col not in old_df.columns or col not in new_df.columns:
            raise ValueError(f"Key column '{col}' not found in dataframes")
    
    # Create composite key
    old_keys = old_df[key_columns].astype(str).agg('|'.join, axis=1)
    new_keys = new_df[key_columns].astype(str).agg('|'.join, axis=1)
    
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
                    col_changed = ~(
                        (merged[new_col] == merged[old_col]) | 
                        (merged[new_col].isna() & merged[old_col].isna())
                    )
                    changed_mask = changed_mask | col_changed
        
        if changed_mask.any():
            changed_keys = merged[changed_mask][key_columns]
            changed_key_str = changed_keys.astype(str).agg('|'.join, axis=1)
            new_key_str = new_df[key_columns].astype(str).agg('|'.join, axis=1)
            result["changed"] = new_df[new_key_str.isin(changed_key_str)].copy()
    
    return result


def clean_excel_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean Excel data by removing empty rows and standardizing format
    """
    logger = logging.getLogger(__name__)
    
    if df.empty:
        return df
    
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
    
    logger.debug(f"데이터 정제 완료: {len(df)}행")
    
    return df


def detect_excel_format(content: bytes) -> str:
    """Detect Excel file format from content"""
    if content[:2] == b'PK':
        return 'xlsx'
    elif content[:4] == b'\xd0\xcf\x11\xe0':
        return 'xls'
    else:
        return 'unknown'


def read_excel_auto(content: bytes) -> pd.DataFrame:
    """Read Excel content with automatic format detection"""
    from io import BytesIO
    
    file_format = detect_excel_format(content)
    
    if file_format == 'xlsx':
        return pd.read_excel(BytesIO(content), engine='openpyxl')
    elif file_format == 'xls':
        return pd.read_excel(BytesIO(content), engine='xlrd')
    else:
        try:
            return pd.read_excel(BytesIO(content), engine='openpyxl')
        except:
            return pd.read_excel(BytesIO(content), engine='xlrd')


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
