"""
ETRS Data Scraper Utilities
유틸리티 함수 모음
"""

import logging
import os
from datetime import datetime
from typing import Optional
import pandas as pd


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


def get_current_timestamp() -> str:
    """Get current timestamp"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_current_date() -> str:
    """Get current date string"""
    return datetime.now().strftime("%Y-%m-%d")


def ensure_directory(path: str) -> None:
    """Ensure directory exists"""
    os.makedirs(path, exist_ok=True)


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
    
    # Remove completely empty rows
    df = df.dropna(how='all')
    
    # Remove rows where all values are whitespace
    df = df[~df.apply(lambda x: x.astype(str).str.strip().eq('').all(), axis=1)]
    
    # Strip whitespace from string columns
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace(['nan', 'None', '', 'NaN', 'NaT'], pd.NA)
    
    # Reset index
    df = df.reset_index(drop=True)
    
    logger.debug(f"데이터 정제 완료: {len(df)}행")
    
    return df


def detect_excel_format(content: bytes) -> str:
    """
    Detect Excel file format from content
    
    Args:
        content: File content bytes
        
    Returns:
        'xlsx' or 'xls'
    """
    # xlsx: PK (50 4B), xls: D0 CF 11 E0
    if content[:2] == b'PK':
        return 'xlsx'
    elif content[:4] == b'\xd0\xcf\x11\xe0':
        return 'xls'
    else:
        return 'unknown'


def read_excel_auto(content: bytes) -> pd.DataFrame:
    """
    Read Excel content with automatic format detection
    
    Args:
        content: File content bytes
        
    Returns:
        DataFrame
    """
    from io import BytesIO
    
    file_format = detect_excel_format(content)
    
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


def format_number(value) -> str:
    """Format number with comma separators"""
    try:
        if pd.isna(value):
            return ""
        return f"{int(float(value)):,}"
    except (ValueError, TypeError):
        return str(value)
