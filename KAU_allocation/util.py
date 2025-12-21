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


def read_excel_auto(content: bytes) -> pd.DataFrame:
    """Read Excel content with automatic format detection"""
    from io import BytesIO
    
    logger = logging.getLogger(__name__)
    
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
