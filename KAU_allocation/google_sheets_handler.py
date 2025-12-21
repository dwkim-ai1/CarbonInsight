"""
ETRS Google Sheets Handler
Google Sheets 데이터 처리 모듈
"""

import json
import os
from typing import List, Dict, Any
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from .utils import setup_logging, get_current_timestamp

logger = setup_logging()


class GoogleSheetsHandler:
    """Google Sheets 데이터 처리 클래스"""
    
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        """
        Initialize Google Sheets handler
        
        Args:
            credentials_json: Service account credentials JSON string
            spreadsheet_id: Google Sheets spreadsheet ID
        """
        self.spreadsheet_id = spreadsheet_id
        self._setup_credentials(credentials_json)
        self._connect()
    
    def _setup_credentials(self, credentials_json: str) -> None:
        """Setup Google credentials from JSON string"""
        try:
            creds_dict = json.loads(credentials_json)
            self.credentials = Credentials.from_service_account_info(
                creds_dict, 
                scopes=self.SCOPES
            )
            logger.info("Google credentials 설정 완료")
        except json.JSONDecodeError as e:
            logger.error(f"Credentials JSON 파싱 실패: {e}")
            raise
    
    def _connect(self) -> None:
        """Connect to Google Sheets"""
        try:
            self.gc = gspread.authorize(self.credentials)
            self.spreadsheet = self.gc.open_by_key(self.spreadsheet_id)
            logger.info(f"Google Sheets 연결 완료: {self.spreadsheet.title}")
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def _get_or_create_worksheet(
        self, 
        sheet_name: str, 
        rows: int = 1000, 
        cols: int = 20
    ) -> gspread.Worksheet:
        """
        Get existing worksheet or create new one
        
        Args:
            sheet_name: Name of the worksheet
            rows: Number of rows for new worksheet
            cols: Number of columns for new worksheet
            
        Returns:
            Worksheet object
        """
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            logger.info(f"기존 시트 사용: {sheet_name}")
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name, 
                rows=rows, 
                cols=cols
            )
            logger.info(f"새 시트 생성: {sheet_name}")
        
        return worksheet
    
    def update_sheet(
        self, 
        sheet_name: str, 
        df: pd.DataFrame,
        clear_first: bool = True
    ) -> Dict[str, Any]:
        """
        Update worksheet with DataFrame
        
        Args:
            sheet_name: Name of the worksheet
            df: DataFrame to write
            clear_first: Whether to clear existing data first
            
        Returns:
            Update result dictionary
        """
        worksheet = self._get_or_create_worksheet(
            sheet_name, 
            rows=len(df) + 100, 
            cols=len(df.columns) + 5
        )
        
        if clear_first:
            worksheet.clear()
        
        # Prepare data
        df_clean = df.copy()
        
        # Convert all columns to string to avoid serialization issues
        for col in df_clean.columns:
            df_clean[col] = df_clean[col].astype(str).replace('nan', '').replace('None', '')
        
        # Add update timestamp
        update_time = get_current_timestamp()
        
        # Prepare data as list of lists
        headers = df_clean.columns.tolist()
        data_rows = df_clean.values.tolist()
        all_data = [headers] + data_rows
        
        # Write to sheet
        worksheet.update(all_data, value_input_option='USER_ENTERED')
        
        logger.info(f"✅ '{sheet_name}' 업데이트 완료: {len(df)}행")
        
        return {
            "sheet_name": sheet_name,
            "rows_updated": len(df),
            "update_time": update_time,
        }
    
    def get_sheet_data(self, sheet_name: str) -> pd.DataFrame:
        """
        Get all data from a worksheet as DataFrame
        
        Args:
            sheet_name: Name of the worksheet
            
        Returns:
            DataFrame with sheet data
        """
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            all_values = worksheet.get_all_values()
            
            if all_values and len(all_values) > 1:
                headers = all_values[0]
                data = all_values[1:]
                df = pd.DataFrame(data, columns=headers)
                logger.info(f"시트 데이터 로드: {sheet_name} ({len(df)}행)")
                return df
            else:
                logger.info(f"시트에 데이터 없음: {sheet_name}")
                return pd.DataFrame()
        except gspread.WorksheetNotFound:
            logger.info(f"시트 없음: {sheet_name}")
            return pd.DataFrame()


def create_handler_from_env() -> GoogleSheetsHandler:
    """
    Create GoogleSheetsHandler using environment variables
    
    Environment variables:
        GOOGLE_SHEETS_CREDS: Service account credentials JSON
        KAU_SHEET_ID: Google Sheets spreadsheet ID
        
    Returns:
        Configured GoogleSheetsHandler
    """
    credentials_json = os.environ.get('GOOGLE_SHEETS_CREDS')
    spreadsheet_id = os.environ.get('KAU_SHEET_ID')
    
    if not credentials_json:
        raise ValueError("GOOGLE_SHEETS_CREDS 환경변수가 설정되지 않았습니다.")
    if not spreadsheet_id:
        raise ValueError("KAU_SHEET_ID 환경변수가 설정되지 않았습니다.")
    
    return GoogleSheetsHandler(credentials_json, spreadsheet_id)
