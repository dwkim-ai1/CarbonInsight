"""
ETRS/ORS Google Sheets Handler
Google Sheets 데이터 처리 모듈

32개 시트 관리:
- 16개 최신 데이터 시트 (ETRS_*, ORS_*)
- 16개 이력 시트 (*_이력)
"""

import os
import sys
import json
from typing import List, Dict, Optional, Any
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# Absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ETRS_DATASETS,
    ORS_DATASETS,
    ALL_DATASETS,
    KEY_COLUMNS,
    VALUE_COLUMNS,
    COLUMNS,
    PLAN_PERIODS,
)
from utils import (
    setup_logging,
    compare_dataframes,
    get_current_timestamp,
    create_update_summary,
)

logger = setup_logging()


class AllocationSheetsHandler:
    """ETRS/ORS Google Sheets 데이터 처리 클래스"""
    
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        """
        Initialize handler
        
        Args:
            credentials_json: Service account credentials JSON string
            spreadsheet_id: Google Sheets spreadsheet ID
        """
        self.spreadsheet_id = spreadsheet_id
        self._setup_credentials(credentials_json)
        self._connect()
    
    def _setup_credentials(self, credentials_json: str) -> None:
        """Setup Google credentials"""
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
        columns: List[str] = None
    ) -> gspread.Worksheet:
        """Get or create worksheet"""
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            logger.debug(f"기존 시트 사용: {sheet_name}")
        except gspread.WorksheetNotFound:
            # Create new worksheet
            num_cols = len(columns) + 5 if columns else 20
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=1000,
                cols=num_cols
            )
            
            # Add headers if provided
            if columns:
                headers = columns + ['_변경유형', '_변경일시', '_계획기간']
                worksheet.update('A1', [headers])
            
            logger.info(f"새 시트 생성: {sheet_name}")
        
        return worksheet
    
    def get_sheet_data(self, sheet_name: str) -> pd.DataFrame:
        """Get all data from worksheet"""
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            all_values = worksheet.get_all_values()
            
            if all_values and len(all_values) > 1:
                headers = all_values[0]
                data = all_values[1:]
                df = pd.DataFrame(data, columns=headers)
                logger.debug(f"시트 데이터 로드: {sheet_name} ({len(df)}행)")
                return df
            elif all_values and len(all_values) == 1:
                logger.debug(f"시트에 헤더만 존재: {sheet_name}")
                return pd.DataFrame(columns=all_values[0])
            else:
                return pd.DataFrame()
        except gspread.WorksheetNotFound:
            logger.debug(f"시트 없음: {sheet_name}")
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"시트 데이터 로드 실패: {sheet_name} - {e}")
            return pd.DataFrame()
    
    # =========================================================
    # ETRS 데이터 처리
    # =========================================================
    
    def update_etrs_latest_sheet(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        Update ETRS latest sheet (combine all periods)
        
        Args:
            dataset_name: Dataset name
            period_data: Dictionary of period -> DataFrame
            
        Returns:
            Update result
        """
        if dataset_name not in ETRS_DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            return {"success": False}
        
        dataset = ETRS_DATASETS[dataset_name]
        sheet_name = dataset['sheet_latest']
        columns = COLUMNS.get(dataset_name, [])
        
        # Combine all periods
        all_data = []
        for period, df in period_data.items():
            df_copy = df.copy()
            if '_계획기간' not in df_copy.columns:
                df_copy['_계획기간'] = f"{period}차"
            all_data.append(df_copy)
        
        if not all_data:
            logger.warning(f"업데이트할 데이터 없음: {dataset_name}")
            return {"success": False}
        
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # Get existing data for comparison
        old_data = self.get_sheet_data(sheet_name)
        
        # Update sheet
        worksheet = self._get_or_create_worksheet(sheet_name, columns)
        worksheet.clear()
        
        update_time = get_current_timestamp()
        combined_df['_업데이트일시'] = update_time
        
        # Write data
        headers = list(combined_df.columns)
        data_rows = combined_df.fillna('').astype(str).values.tolist()
        all_rows = [headers] + data_rows
        
        worksheet.update('A1', all_rows)
        
        logger.info(f"✅ 최신 시트 업데이트: {sheet_name} ({len(combined_df)}행)")
        
        return {
            "success": True,
            "sheet_name": sheet_name,
            "rows": len(combined_df),
            "update_time": update_time,
            "old_data": old_data
        }
    
    def update_etrs_history_sheet(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame],
        old_data: pd.DataFrame = None
    ) -> Dict[str, Any]:
        """
        Update ETRS history sheet (append changes only)
        
        Args:
            dataset_name: Dataset name
            period_data: Dictionary of period -> DataFrame
            old_data: Previous data for comparison
            
        Returns:
            Update result
        """
        if dataset_name not in ETRS_DATASETS:
            return {"success": False}
        
        dataset = ETRS_DATASETS[dataset_name]
        sheet_name = dataset['sheet_history']
        key_cols = KEY_COLUMNS.get(dataset_name, [])
        value_cols = VALUE_COLUMNS.get(dataset_name, [])
        
        # Combine all periods
        all_data = []
        for period, df in period_data.items():
            df_copy = df.copy()
            if '_계획기간' not in df_copy.columns:
                df_copy['_계획기간'] = f"{period}차"
            all_data.append(df_copy)
        
        if not all_data:
            return {"success": False}
        
        new_data = pd.concat(all_data, ignore_index=True)
        
        # ★★★ 실제 데이터의 컬럼을 사용 (config 대신) ★★★
        actual_columns = [c for c in new_data.columns if not c.startswith('_')]
        
        # Detect changes
        if old_data is None or old_data.empty:
            changes = {
                'added': new_data.copy(),
                'removed': pd.DataFrame(),
                'changed': pd.DataFrame()
            }
        else:
            # Filter columns - 실제 데이터에 있는 컬럼만 사용
            available_key_cols = [c for c in key_cols if c in new_data.columns and c in old_data.columns]
            available_value_cols = [c for c in value_cols if c in new_data.columns]
            
            # 키 컬럼이 없으면 실제 데이터의 공통 컬럼에서 찾기
            if not available_key_cols:
                common_cols = [c for c in new_data.columns if c in old_data.columns and not c.startswith('_')]
                # 업체명, 부문 등 기본 키 컬럼 시도
                for key_candidate in ['업체명', '부문', '업종', '구분']:
                    if key_candidate in common_cols:
                        available_key_cols.append(key_candidate)
                if len(available_key_cols) == 0 and common_cols:
                    available_key_cols = common_cols[:2]  # 처음 2개 컬럼 사용
            
            # Filter old_data columns
            old_data_filtered = old_data[[c for c in old_data.columns if not c.startswith('_')]]
            
            if available_key_cols:
                changes = compare_dataframes(
                    old_data_filtered,
                    new_data,
                    available_key_cols,
                    available_value_cols
                )
            else:
                changes = {
                    'added': new_data.copy(),
                    'removed': pd.DataFrame(),
                    'changed': pd.DataFrame()
                }
        
        # Prepare rows to append
        update_time = get_current_timestamp()
        rows_to_add = []
        
        # Added records
        if not changes['added'].empty:
            for _, row in changes['added'].iterrows():
                row_dict = row.to_dict()
                row_dict['_변경유형'] = '신규'
                row_dict['_변경일시'] = update_time
                rows_to_add.append(row_dict)
        
        # Changed records
        if not changes['changed'].empty:
            for _, row in changes['changed'].iterrows():
                row_dict = row.to_dict()
                row_dict['_변경유형'] = '변경'
                row_dict['_변경일시'] = update_time
                rows_to_add.append(row_dict)
        
        # Append to history sheet
        if rows_to_add:
            # ★★★ 시트 가져오기 (헤더 없이 생성) ★★★
            worksheet = self._get_or_create_worksheet(sheet_name)
            
            # Get current data
            existing = worksheet.get_all_values()
            
            # ★★★ 헤더가 없으면 실제 데이터의 컬럼으로 생성 ★★★
            if not existing or len(existing) == 0:
                # 실제 데이터 컬럼 + 메타데이터 컬럼
                headers = actual_columns + ['_변경유형', '_변경일시', '_계획기간']
                worksheet.update('A1', [headers])
                existing = [headers]
                logger.info(f"이력 시트 헤더 생성: {headers[:5]}...")
            
            headers = existing[0]
            next_row = len(existing) + 1
            
            # ★★★ 헤더 컬럼 부족 시 확장 ★★★
            all_needed_cols = set()
            for row_dict in rows_to_add:
                all_needed_cols.update(row_dict.keys())
            
            missing_cols = [c for c in all_needed_cols if c not in headers]
            if missing_cols:
                # 기존 헤더에 새 컬럼 추가
                headers = headers + missing_cols
                worksheet.update('A1', [headers])
                logger.info(f"이력 시트 헤더 확장: +{missing_cols}")
            
            # Prepare rows
            new_rows = []
            for row_dict in rows_to_add:
                row_values = [str(row_dict.get(h, '')) for h in headers]
                new_rows.append(row_values)
            
            # Expand sheet if needed
            required_rows = next_row + len(new_rows)
            required_cols = len(headers)
            
            if required_rows > worksheet.row_count:
                worksheet.add_rows(required_rows - worksheet.row_count + 100)
            if required_cols > worksheet.col_count:
                worksheet.add_cols(required_cols - worksheet.col_count + 10)
            
            # Write rows
            worksheet.update(f'A{next_row}', new_rows)
            
            logger.info(f"✅ 이력 시트 업데이트: {sheet_name} (+{len(new_rows)}행)")
        else:
            logger.info(f"ℹ️ 이력 시트 변경 없음: {sheet_name}")
        
        return {
            "success": True,
            "sheet_name": sheet_name,
            "added": len(changes['added']),
            "changed": len(changes['changed']),
            "removed": len(changes['removed']),
            "update_time": update_time
        }
    
    def process_etrs_update(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        Process full ETRS update (latest + history)
        
        Args:
            dataset_name: Dataset name
            period_data: Dictionary of period -> DataFrame
            
        Returns:
            Update result
        """
        logger.info(f"\n{'='*40}")
        logger.info(f"📊 {dataset_name} 업데이트")
        logger.info(f"{'='*40}")
        
        # Update latest sheet
        latest_result = self.update_etrs_latest_sheet(dataset_name, period_data)
        
        if not latest_result.get("success"):
            return {"success": False, "error": "Latest sheet update failed"}
        
        # Update history sheet
        history_result = self.update_etrs_history_sheet(
            dataset_name,
            period_data,
            latest_result.get('old_data')
        )
        
        # Summary
        total_rows = latest_result.get('rows', 0)
        added = history_result.get('added', 0)
        changed = history_result.get('changed', 0)
        
        summary = f"""
{dataset_name} 업데이트 완료:
  - 최신 시트: {total_rows}행
  - 신규: {added}건
  - 변경: {changed}건
"""
        logger.info(summary)
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "latest_sheet": latest_result,
            "history_sheet": history_result
        }
    
    # =========================================================
    # ORS 데이터 처리
    # =========================================================
    
    def update_ors_latest_sheet(
        self,
        dataset_name: str,
        df: pd.DataFrame
    ) -> Dict[str, Any]:
        """Update ORS latest sheet"""
        if dataset_name not in ORS_DATASETS:
            logger.error(f"알 수 없는 ORS 데이터셋: {dataset_name}")
            return {"success": False}
        
        dataset = ORS_DATASETS[dataset_name]
        sheet_name = dataset['sheet_latest']
        columns = COLUMNS.get(dataset_name, [])
        
        # Get existing data
        old_data = self.get_sheet_data(sheet_name)
        
        # Update sheet
        worksheet = self._get_or_create_worksheet(sheet_name, columns)
        worksheet.clear()
        
        update_time = get_current_timestamp()
        df_copy = df.copy()
        df_copy['_업데이트일시'] = update_time
        
        headers = list(df_copy.columns)
        data_rows = df_copy.fillna('').astype(str).values.tolist()
        all_rows = [headers] + data_rows
        
        worksheet.update('A1', all_rows)
        
        logger.info(f"✅ ORS 최신 시트 업데이트: {sheet_name} ({len(df)}행)")
        
        return {
            "success": True,
            "sheet_name": sheet_name,
            "rows": len(df),
            "update_time": update_time,
            "old_data": old_data
        }
    
    def update_ors_history_sheet(
        self,
        dataset_name: str,
        df: pd.DataFrame,
        old_data: pd.DataFrame = None
    ) -> Dict[str, Any]:
        """Update ORS history sheet"""
        if dataset_name not in ORS_DATASETS:
            return {"success": False}
        
        dataset = ORS_DATASETS[dataset_name]
        sheet_name = dataset['sheet_history']
        key_cols = KEY_COLUMNS.get(dataset_name, [])
        value_cols = VALUE_COLUMNS.get(dataset_name, [])
        
        # ★★★ 실제 데이터의 컬럼을 사용 ★★★
        actual_columns = [c for c in df.columns if not c.startswith('_')]
        
        # Detect changes
        if old_data is None or old_data.empty:
            changes = {
                'added': df.copy(),
                'removed': pd.DataFrame(),
                'changed': pd.DataFrame()
            }
        else:
            available_key_cols = [c for c in key_cols if c in df.columns and c in old_data.columns]
            available_value_cols = [c for c in value_cols if c in df.columns]
            
            # 키 컬럼이 없으면 실제 데이터의 공통 컬럼에서 찾기
            if not available_key_cols:
                common_cols = [c for c in df.columns if c in old_data.columns and not c.startswith('_')]
                for key_candidate in ['사업명', '방법론명', '구분', '번호']:
                    if key_candidate in common_cols:
                        available_key_cols.append(key_candidate)
                if len(available_key_cols) == 0 and common_cols:
                    available_key_cols = common_cols[:2]
            
            old_data_filtered = old_data[[c for c in old_data.columns if not c.startswith('_')]]
            
            if available_key_cols:
                changes = compare_dataframes(old_data_filtered, df, available_key_cols, available_value_cols)
            else:
                changes = {'added': df.copy(), 'removed': pd.DataFrame(), 'changed': pd.DataFrame()}
        
        # Prepare rows
        update_time = get_current_timestamp()
        rows_to_add = []
        
        if not changes['added'].empty:
            for _, row in changes['added'].iterrows():
                row_dict = row.to_dict()
                row_dict['_변경유형'] = '신규'
                row_dict['_변경일시'] = update_time
                rows_to_add.append(row_dict)
        
        if not changes['changed'].empty:
            for _, row in changes['changed'].iterrows():
                row_dict = row.to_dict()
                row_dict['_변경유형'] = '변경'
                row_dict['_변경일시'] = update_time
                rows_to_add.append(row_dict)
        
        # Append
        if rows_to_add:
            # ★★★ 시트 가져오기 (헤더 없이 생성) ★★★
            worksheet = self._get_or_create_worksheet(sheet_name)
            existing = worksheet.get_all_values()
            
            # ★★★ 헤더가 없으면 실제 데이터의 컬럼으로 생성 ★★★
            if not existing or len(existing) == 0:
                headers = actual_columns + ['_변경유형', '_변경일시']
                worksheet.update('A1', [headers])
                existing = [headers]
                logger.info(f"ORS 이력 시트 헤더 생성: {headers[:5]}...")
            
            headers = existing[0]
            next_row = len(existing) + 1
            
            # ★★★ 헤더 컬럼 부족 시 확장 ★★★
            all_needed_cols = set()
            for row_dict in rows_to_add:
                all_needed_cols.update(row_dict.keys())
            
            missing_cols = [c for c in all_needed_cols if c not in headers]
            if missing_cols:
                headers = headers + missing_cols
                worksheet.update('A1', [headers])
                logger.info(f"ORS 이력 시트 헤더 확장: +{missing_cols}")
            
            new_rows = []
            for row_dict in rows_to_add:
                row_values = [str(row_dict.get(h, '')) for h in headers]
                new_rows.append(row_values)
            
            required_rows = next_row + len(new_rows)
            required_cols = len(headers)
            
            if required_rows > worksheet.row_count:
                worksheet.add_rows(required_rows - worksheet.row_count + 100)
            if required_cols > worksheet.col_count:
                worksheet.add_cols(required_cols - worksheet.col_count + 10)
            
            worksheet.update(f'A{next_row}', new_rows)
            logger.info(f"✅ ORS 이력 시트 업데이트: {sheet_name} (+{len(new_rows)}행)")
        
        return {
            "success": True,
            "sheet_name": sheet_name,
            "added": len(changes['added']),
            "changed": len(changes['changed']),
            "update_time": update_time
        }
    
    def process_ors_update(
        self,
        dataset_name: str,
        df: pd.DataFrame
    ) -> Dict[str, Any]:
        """Process full ORS update"""
        logger.info(f"\n{'='*40}")
        logger.info(f"🌿 ORS {dataset_name} 업데이트")
        logger.info(f"{'='*40}")
        
        latest_result = self.update_ors_latest_sheet(dataset_name, df)
        
        if not latest_result.get("success"):
            return {"success": False}
        
        history_result = self.update_ors_history_sheet(
            dataset_name, df, latest_result.get('old_data')
        )
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "latest_sheet": latest_result,
            "history_sheet": history_result
        }
    
    # =========================================================
    # 통합 업데이트
    # =========================================================
    
    def process_all_updates(
        self,
        etrs_data: Dict[str, Dict[int, pd.DataFrame]],
        ors_data: Dict[str, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        Process all updates (ETRS + ORS)
        
        Args:
            etrs_data: ETRS data (dataset -> period -> DataFrame)
            ors_data: ORS data (dataset -> DataFrame)
            
        Returns:
            Complete update result
        """
        results = {
            'etrs': {},
            'ors': {},
            'success': True
        }
        
        # Process ETRS
        for dataset_name, period_data in etrs_data.items():
            try:
                result = self.process_etrs_update(dataset_name, period_data)
                results['etrs'][dataset_name] = result
            except Exception as e:
                logger.error(f"ETRS {dataset_name} 업데이트 실패: {e}")
                results['etrs'][dataset_name] = {"success": False, "error": str(e)}
        
        # Process ORS
        for dataset_name, df in ors_data.items():
            try:
                result = self.process_ors_update(dataset_name, df)
                results['ors'][dataset_name] = result
            except Exception as e:
                logger.error(f"ORS {dataset_name} 업데이트 실패: {e}")
                results['ors'][dataset_name] = {"success": False, "error": str(e)}
        
        return results


def create_handler_from_env() -> AllocationSheetsHandler:
    """Create handler from environment variables"""
    credentials_json = os.environ.get('GOOGLE_SHEETS_CREDS')
    spreadsheet_id = os.environ.get('KAU_SHEET_ID')
    
    if not credentials_json:
        raise ValueError("GOOGLE_SHEETS_CREDS 환경변수가 설정되지 않음")
    if not spreadsheet_id:
        raise ValueError("KAU_SHEET_ID 환경변수가 설정되지 않음")
    
    return AllocationSheetsHandler(credentials_json, spreadsheet_id)
