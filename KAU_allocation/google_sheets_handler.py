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

# Absolute imports
from config import SHEET_NAMES, KEY_COLUMNS, VALUE_COLUMNS, COLUMNS
from utils import (
    setup_logging, 
    compare_dataframes, 
    get_current_timestamp,
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
    
    def _get_or_create_worksheet(
        self, 
        sheet_name: str, 
        rows: int = 1000,
        cols: int = 20
    ) -> gspread.Worksheet:
        """Get existing worksheet or create new one"""
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
    
    def get_sheet_data(self, sheet_name: str) -> pd.DataFrame:
        """
        Get all data from a worksheet as DataFrame
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
            elif all_values and len(all_values) == 1:
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
        new_data: pd.DataFrame,
        plan_period: int = None
    ) -> Dict[str, Any]:
        """
        Update the 'latest' sheet with new data (full replacement)
        
        Args:
            data_type: Type of data (사전할당량, etc.)
            new_data: New data to write
            plan_period: Plan period (1, 2, or 3)
            
        Returns:
            Update result dictionary
        """
        # Build sheet name
        if plan_period:
            sheet_name = f"ETRS_{data_type}_{plan_period}차"
        else:
            sheet_name = SHEET_NAMES.get(f"{data_type}_latest", f"ETRS_{data_type}")
        
        worksheet = self._get_or_create_worksheet(
            sheet_name, 
            rows=len(new_data) + 100,
            cols=len(new_data.columns) + 5
        )
        
        # Get existing data for comparison
        old_data = self.get_sheet_data(sheet_name)
        
        # Clear and write new data
        worksheet.clear()
        
        # Prepare data with metadata
        update_time = get_current_timestamp()
        
        new_data_with_meta = new_data.copy()
        new_data_with_meta['_업데이트일시'] = update_time
        
        # Convert to string for gspread
        for col in new_data_with_meta.columns:
            new_data_with_meta[col] = new_data_with_meta[col].astype(str).replace('nan', '').replace('None', '')
        
        # Write headers and data
        headers = list(new_data_with_meta.columns)
        data_rows = new_data_with_meta.values.tolist()
        
        all_data = [headers] + data_rows
        worksheet.update(all_data, value_input_option='USER_ENTERED')
        
        logger.info(f"✅ 최신 시트 업데이트 완료: {sheet_name} ({len(new_data)}행)")
        
        return {
            "sheet_name": sheet_name,
            "rows_updated": len(new_data),
            "update_time": update_time,
            "old_data": old_data
        }
    
    def update_stack_sheet(
        self, 
        data_type: str, 
        changes: Dict[str, pd.DataFrame],
        plan_period: int = None
    ) -> Dict[str, Any]:
        """
        Update the 'stack' sheet with changes (append new/changed records)
        
        Args:
            data_type: Type of data
            changes: Dictionary with 'added', 'removed', 'changed' DataFrames
            plan_period: Plan period
            
        Returns:
            Update result dictionary
        """
        # Build sheet name
        if plan_period:
            sheet_name = f"{data_type}_이력_{plan_period}차"
        else:
            sheet_name = SHEET_NAMES.get(f"{data_type}_stack", f"{data_type}_이력")
        
        columns = COLUMNS.get(data_type, [])
        
        stack_columns = columns + ['_변경유형', '_변경일시', '_계획기간']
        
        worksheet = self._get_or_create_worksheet(sheet_name, rows=5000, cols=len(stack_columns) + 5)
        
        update_time = get_current_timestamp()
        rows_added = 0
        
        # Get existing data
        try:
            all_values = worksheet.get_all_values()
            if all_values and len(all_values) > 1:
                headers = all_values[0]
                data = all_values[1:]
                existing_df = pd.DataFrame(data, columns=headers)
            else:
                existing_df = pd.DataFrame(columns=stack_columns)
                # Write headers if new sheet
                if not all_values:
                    worksheet.update('A1', [stack_columns])
        except Exception as e:
            logger.warning(f"기존 데이터 읽기 실패: {e}")
            existing_df = pd.DataFrame(columns=stack_columns)
        
        records_to_append = []
        
        # Process added records
        added_df = changes.get('added', pd.DataFrame())
        if not added_df.empty:
            for _, row in added_df.iterrows():
                record = {col: row.get(col, '') for col in columns if col in row.index}
                record['_변경유형'] = '신규'
                record['_변경일시'] = update_time
                record['_계획기간'] = f"{plan_period}차" if plan_period else ""
                records_to_append.append(record)
            logger.info(f"신규 데이터 {len(added_df)}건 준비")
        
        # Process changed records
        changed_df = changes.get('changed', pd.DataFrame())
        if not changed_df.empty:
            for _, row in changed_df.iterrows():
                record = {col: row.get(col, '') for col in columns if col in row.index}
                record['_변경유형'] = '변경'
                record['_변경일시'] = update_time
                record['_계획기간'] = f"{plan_period}차" if plan_period else ""
                records_to_append.append(record)
            logger.info(f"변경 데이터 {len(changed_df)}건 준비")
        
        # Log removed records
        removed_df = changes.get('removed', pd.DataFrame())
        if not removed_df.empty:
            logger.info(f"삭제 감지: {len(removed_df)}건 (기록만 유지)")
        
        # Append to sheet
        if records_to_append:
            # Convert to list of lists
            new_rows = []
            for record in records_to_append:
                row = [str(record.get(col, '')) for col in stack_columns]
                new_rows.append(row)
            
            # Get next row position
            next_row = len(existing_df) + 2  # +1 for header, +1 for 1-based index
            
            # Expand rows if needed
            required_rows = next_row + len(new_rows) + 100
            if required_rows > worksheet.row_count:
                worksheet.add_rows(required_rows - worksheet.row_count)
            
            # Append
            worksheet.update(f'A{next_row}', new_rows, value_input_option='USER_ENTERED')
            rows_added = len(new_rows)
            
            logger.info(f"✅ 누적 시트 업데이트 완료: {sheet_name} (+{rows_added}건)")
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
    
    def process_update(
        self, 
        data_type: str, 
        new_data: pd.DataFrame,
        plan_period: int = None
    ) -> Dict[str, Any]:
        """
        Process full update: update latest sheet and stack changes
        
        Args:
            data_type: Type of data
            new_data: New data from ETRS
            plan_period: Plan period
            
        Returns:
            Complete update result
        """
        period_str = f" {plan_period}차" if plan_period else ""
        logger.info(f"=== {data_type}{period_str} 업데이트 시작 ===")
        
        # 1. Update latest sheet
        latest_result = self.update_latest_sheet(data_type, new_data, plan_period)
        old_data = latest_result['old_data']
        
        # 2. Compare and detect changes
        key_cols = KEY_COLUMNS.get(data_type, [])
        value_cols = VALUE_COLUMNS.get(data_type, [])
        
        # Filter to existing columns
        available_key_cols = [c for c in key_cols if c in new_data.columns]
        available_value_cols = [c for c in value_cols if c in new_data.columns]
        
        if old_data.empty:
            changes = {
                'added': new_data.copy(),
                'removed': pd.DataFrame(),
                'changed': pd.DataFrame()
            }
        elif not available_key_cols:
            logger.warning("키 컬럼을 찾을 수 없음, 전체 데이터를 신규로 처리")
            changes = {
                'added': new_data.copy(),
                'removed': pd.DataFrame(),
                'changed': pd.DataFrame()
            }
        else:
            # Filter old_data columns (exclude metadata)
            old_data_filtered = old_data[[c for c in old_data.columns if not c.startswith('_')]]
            
            changes = compare_dataframes(
                old_data_filtered, 
                new_data, 
                available_key_cols,
                available_value_cols
            )
        
        # 3. Update stack sheet
        stack_result = self.update_stack_sheet(data_type, changes, plan_period)
        
        # 4. Create summary
        summary = create_update_summary(
            f"{data_type}{period_str}",
            len(new_data),
            stack_result['added_count'],
            stack_result['removed_count'],
            stack_result['changed_count']
        )
        logger.info(summary)
        
        return {
            'data_type': data_type,
            'plan_period': plan_period,
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
    """
    credentials_json = os.environ.get('GOOGLE_SHEETS_CREDS')
    spreadsheet_id = os.environ.get('KAU_SHEET_ID')
    
    if not credentials_json:
        raise ValueError("GOOGLE_SHEETS_CREDS 환경변수가 설정되지 않았습니다.")
    if not spreadsheet_id:
        raise ValueError("KAU_SHEET_ID 환경변수가 설정되지 않았습니다.")
    
    return GoogleSheetsHandler(credentials_json, spreadsheet_id)
