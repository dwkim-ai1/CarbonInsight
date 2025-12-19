"""
NGMS Google Sheets Handler
Google Sheets 데이터 처리 모듈
"""

import json
import os
from typing import List, Dict, Optional, Any
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from config import SHEET_NAMES, KEY_COLUMNS, VALUE_COLUMNS, COLUMNS
from utils import (
    setup_logging, 
    compare_dataframes, 
    get_current_timestamp,
    get_current_date,
    create_update_summary
)

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
    
    def _get_or_create_worksheet(self, sheet_name: str, columns: List[str]) -> gspread.Worksheet:
        """
        Get existing worksheet or create new one
        
        Args:
            sheet_name: Name of the worksheet
            columns: Column headers for new worksheet
            
        Returns:
            Worksheet object
        """
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            logger.info(f"기존 시트 사용: {sheet_name}")
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name, 
                rows=1000, 
                cols=len(columns) + 2  # Extra columns for metadata
            )
            # Add headers
            worksheet.update('A1', [columns])
            logger.info(f"새 시트 생성: {sheet_name}")
        
        return worksheet
    
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
            
            # ★★★ get_all_values 사용 (중복 헤더 문제 방지) ★★★
            all_values = worksheet.get_all_values()
            
            if all_values and len(all_values) > 1:
                # 첫 행을 헤더로, 나머지를 데이터로
                headers = all_values[0]
                data = all_values[1:]
                df = pd.DataFrame(data, columns=headers)
                logger.info(f"시트 데이터 로드: {sheet_name} ({len(df)}행)")
                return df
            elif all_values and len(all_values) == 1:
                # 헤더만 있는 경우
                logger.info(f"시트에 데이터 없음 (헤더만 존재): {sheet_name}")
                return pd.DataFrame()
            else:
                logger.info(f"시트에 데이터 없음: {sheet_name}")
                return pd.DataFrame()
        except gspread.WorksheetNotFound:
            logger.info(f"시트 없음: {sheet_name}")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"시트 데이터 로드 실패: {sheet_name} - {e}")
            return pd.DataFrame()
    
    def update_latest_sheet(
        self, 
        data_type: str, 
        new_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Update the 'latest' sheet with new data (full replacement)
        
        Args:
            data_type: Type of data (할당대상업체, 목표관리대상업체, 명세서배출량통계)
            new_data: New data to write
            
        Returns:
            Update result dictionary
        """
        sheet_name = SHEET_NAMES[f"{data_type}_latest"]
        columns = COLUMNS[data_type]
        
        worksheet = self._get_or_create_worksheet(sheet_name, columns)
        
        # Get existing data for comparison
        old_data = self.get_sheet_data(sheet_name)
        
        # Clear and write new data
        worksheet.clear()
        
        # Prepare data with metadata
        update_time = get_current_timestamp()
        
        # Add update timestamp column
        new_data_with_meta = new_data.copy()
        new_data_with_meta['_업데이트일시'] = update_time
        
        # Write headers and data
        headers = list(new_data_with_meta.columns)
        data_rows = new_data_with_meta.fillna('').values.tolist()
        
        all_data = [headers] + data_rows
        worksheet.update('A1', all_data)
        
        logger.info(f"최신 시트 업데이트 완료: {sheet_name} ({len(new_data)}행)")
        
        return {
            "sheet_name": sheet_name,
            "rows_updated": len(new_data),
            "update_time": update_time,
            "old_data": old_data
        }
    
    def update_stack_sheet(
        self, 
        data_type: str, 
        changes: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        Update the 'stack' sheet with changed records
        
        Args:
            data_type: Type of data
            changes: Dictionary with 'added', 'removed', 'changed' DataFrames
            
        Returns:
            Update result dictionary
        """
        sheet_name = SHEET_NAMES[f"{data_type}_stack"]
        columns = COLUMNS[data_type]
        
        # Columns for stack sheet include metadata
        stack_columns = columns + ['_변경유형', '_변경일시']
        
        worksheet = self._get_or_create_worksheet(sheet_name, stack_columns)
        
        update_time = get_current_timestamp()
        rows_added = 0
        
        # Prepare records to add
        records_to_add = []
        
        for change_type, df in changes.items():
            if not df.empty:
                df_copy = df.copy()
                
                # Map change type to Korean
                type_map = {
                    'added': '신규',
                    'removed': '삭제',
                    'changed': '변경'
                }
                
                df_copy['_변경유형'] = type_map.get(change_type, change_type)
                df_copy['_변경일시'] = update_time
                
                # Ensure columns are in correct order
                for col in stack_columns:
                    if col not in df_copy.columns:
                        df_copy[col] = ''
                
                df_copy = df_copy[stack_columns]
                records_to_add.extend(df_copy.fillna('').values.tolist())
        
        if records_to_add:
            # Get current row count
            existing_data = worksheet.get_all_values()
            next_row = len(existing_data) + 1
            required_rows = next_row + len(records_to_add)
            
            # ★★★ 행 수가 부족하면 자동 확장 ★★★
            current_row_count = worksheet.row_count
            if required_rows > current_row_count:
                # 여유있게 1.5배 또는 최소 1000행 추가
                new_row_count = max(required_rows + 1000, int(current_row_count * 1.5))
                worksheet.add_rows(new_row_count - current_row_count)
                logger.info(f"시트 행 확장: {current_row_count} → {new_row_count}")
            
            # Append new records
            worksheet.update(f'A{next_row}', records_to_add)
            rows_added = len(records_to_add)
            
            logger.info(f"누적 시트 업데이트 완료: {sheet_name} (+{rows_added}행)")
        else:
            logger.info(f"누적 시트 변경 없음: {sheet_name}")
        
        return {
            "sheet_name": sheet_name,
            "rows_added": rows_added,
            "update_time": update_time,
            "added_count": len(changes.get('added', pd.DataFrame())),
            "removed_count": len(changes.get('removed', pd.DataFrame())),
            "changed_count": len(changes.get('changed', pd.DataFrame()))
        }
    
    def append_historical_data(
        self, 
        data_type: str, 
        data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Append historical data directly to stack sheet (without updating NGMS_ sheet)
        
        Args:
            data_type: Type of data
            data: Historical data to append
            
        Returns:
            Result dictionary with rows_added count
        """
        logger.info(f"=== {data_type} 과거 데이터 추가 ===")
        
        sheet_name = SHEET_NAMES[f"{data_type}_stack"]
        columns = COLUMNS[data_type]
        
        # Columns for stack sheet include metadata
        stack_columns = columns + ['_변경유형', '_변경일시']
        
        worksheet = self._get_or_create_worksheet(sheet_name, stack_columns)
        
        update_time = get_current_timestamp()
        
        # Prepare data
        df_copy = data.copy()
        df_copy['_변경유형'] = '과거'  # Mark as historical data
        df_copy['_변경일시'] = update_time
        
        # Ensure columns are in correct order
        for col in stack_columns:
            if col not in df_copy.columns:
                df_copy[col] = ''
        
        # Only keep stack columns
        available_cols = [c for c in stack_columns if c in df_copy.columns]
        df_copy = df_copy[available_cols]
        
        records_to_add = df_copy.fillna('').values.tolist()
        
        if records_to_add:
            # Get current row count
            existing_data = worksheet.get_all_values()
            next_row = len(existing_data) + 1
            required_rows = next_row + len(records_to_add)
            
            # ★★★ 행 수가 부족하면 자동 확장 ★★★
            current_row_count = worksheet.row_count
            if required_rows > current_row_count:
                new_row_count = max(required_rows + 1000, int(current_row_count * 1.5))
                worksheet.add_rows(new_row_count - current_row_count)
                logger.info(f"시트 행 확장: {current_row_count} → {new_row_count}")
            
            # Append new records
            worksheet.update(f'A{next_row}', records_to_add)
            rows_added = len(records_to_add)
            
            logger.info(f"과거 데이터 추가 완료: {sheet_name} (+{rows_added}행)")
        else:
            rows_added = 0
            logger.info(f"추가할 데이터 없음: {sheet_name}")
        
        return {
            "sheet_name": sheet_name,
            "rows_added": rows_added,
            "update_time": update_time
        }
    
    def process_update(
        self, 
        data_type: str, 
        new_data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        Process full update: update latest sheet and stack sheet if changes detected
        
        Args:
            data_type: Type of data
            new_data: New data from NGMS
            
        Returns:
            Complete update result
        """
        logger.info(f"=== {data_type} 업데이트 시작 ===")
        
        # 1. Update latest sheet and get old data
        latest_result = self.update_latest_sheet(data_type, new_data)
        old_data = latest_result['old_data']
        
        # 2. Compare and detect changes
        key_cols = KEY_COLUMNS[data_type]
        value_cols = VALUE_COLUMNS[data_type]
        
        # Filter to only existing columns
        available_key_cols = [c for c in key_cols if c in new_data.columns]
        available_value_cols = [c for c in value_cols if c in new_data.columns]
        
        # ★★★ 디버그 로그 ★★★
        logger.info(f"키 컬럼 (설정): {key_cols}")
        logger.info(f"키 컬럼 (실제 존재): {available_key_cols}")
        logger.info(f"새 데이터 컬럼: {list(new_data.columns)[:8]}...")
        
        # 키 컬럼이 없으면 경고
        if not available_key_cols:
            logger.warning(f"⚠️ 키 컬럼을 찾을 수 없음! 전체 데이터가 '신규'로 처리됩니다.")
        
        if old_data.empty:
            # First time - all data is new
            changes = {
                'added': new_data.copy(),
                'removed': pd.DataFrame(),
                'changed': pd.DataFrame()
            }
        else:
            # Filter old_data to only have same columns
            old_data = old_data[[c for c in old_data.columns if c in new_data.columns or c.startswith('_')]]
            old_data = old_data[[c for c in old_data.columns if not c.startswith('_')]]
            
            changes = compare_dataframes(
                old_data, 
                new_data, 
                available_key_cols,
                available_value_cols
            )
        
        # 3. Update stack sheet with changes
        stack_result = self.update_stack_sheet(data_type, changes)
        
        # 4. Create summary
        summary = create_update_summary(
            data_type,
            len(new_data),
            stack_result['added_count'],
            stack_result['removed_count'],
            stack_result['changed_count']
        )
        logger.info(summary)
        
        return {
            'data_type': data_type,
            'latest_sheet': latest_result,
            'stack_sheet': stack_result,
            'summary': summary,
            'has_changes': stack_result['rows_added'] > 0
        }


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
        raise ValueError("GOOGLE_SHEETS_CREDS environment variable not set")
    if not spreadsheet_id:
        raise ValueError("KAU_SHEET_ID environment variable not set")
    
    return GoogleSheetsHandler(credentials_json, spreadsheet_id)
