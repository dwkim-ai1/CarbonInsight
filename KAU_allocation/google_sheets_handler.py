"""
ETRS/ORS Google Sheets Handler
Google Sheets 데이터 처리 모듈 (큐 시스템)

32개 시트 관리:
- 16개 최신 데이터 시트 (ETRS_*, ORS_*)
- 16개 이력 시트 (*_이력)

★★★ API 호출 최적화 ★★★
- 모든 업데이트를 메모리에 큐잉
- flush_all_updates()로 한 번에 처리
- 시트당 최대 2회 API 호출 (읽기 1회, 쓰기 1회)
"""

import os
import sys
import json
import time
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
    reorder_columns,
)

logger = setup_logging()

# API 호출 딜레이 (초) - 분당 60회 쓰기 제한 대응
# 시트당 2회 호출 × 32개 시트 = 64회, 안전하게 3초 간격
API_WRITE_DELAY = 3


class AllocationSheetsHandler:
    """
    ETRS/ORS Google Sheets 데이터 처리 클래스
    
    ★★★ 큐 시스템 ★★★
    1. queue_latest_update() - 최신 시트 업데이트 큐잉
    2. queue_history_update() - 이력 시트 업데이트 큐잉
    3. flush_all_updates() - 모든 큐를 한 번에 처리
    """
    
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    def __init__(self, credentials_json: str, spreadsheet_id: str):
        """Initialize handler"""
        self.spreadsheet_id = spreadsheet_id
        self._setup_credentials(credentials_json)
        self._connect()
        
        # ★★★ 업데이트 큐 ★★★
        self.update_queue: Dict[str, Dict] = {}
        # 구조: {sheet_name: {'mode': 'replace'|'append', 'data': DataFrame}}
        
        # 시트 데이터 캐시 (API 호출 최소화)
        self.sheet_cache: Dict[str, pd.DataFrame] = {}
    
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
        rows: int = 1000,
        cols: int = 30
    ) -> gspread.Worksheet:
        """Get or create worksheet (최소 API 호출)"""
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = self.spreadsheet.add_worksheet(
                title=sheet_name,
                rows=rows,
                cols=cols
            )
            logger.info(f"새 시트 생성: {sheet_name}")
            time.sleep(2)  # 생성 후 대기
        
        return worksheet
    
    def get_sheet_data(self, sheet_name: str, use_cache: bool = True) -> pd.DataFrame:
        """
        시트 데이터 읽기 (캐시 사용)
        """
        # 캐시 확인
        if use_cache and sheet_name in self.sheet_cache:
            logger.debug(f"캐시 사용: {sheet_name}")
            return self.sheet_cache[sheet_name]
        
        try:
            worksheet = self.spreadsheet.worksheet(sheet_name)
            all_values = worksheet.get_all_values()
            time.sleep(1)  # 읽기 후 짧은 대기
            
            if all_values and len(all_values) > 1:
                headers = all_values[0]
                data = all_values[1:]
                df = pd.DataFrame(data, columns=headers)
                self.sheet_cache[sheet_name] = df
                logger.debug(f"시트 데이터 로드: {sheet_name} ({len(df)}행)")
                return df
            elif all_values and len(all_values) == 1:
                df = pd.DataFrame(columns=all_values[0])
                self.sheet_cache[sheet_name] = df
                return df
            else:
                return pd.DataFrame()
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"시트 데이터 로드 실패: {sheet_name} - {e}")
            return pd.DataFrame()
    
    def clear_cache(self, sheet_name: str = None):
        """캐시 클리어"""
        if sheet_name:
            self.sheet_cache.pop(sheet_name, None)
        else:
            self.sheet_cache.clear()
    
    # =========================================================
    # 큐 시스템
    # =========================================================
    
    def queue_latest_update(
        self,
        sheet_name: str,
        data: pd.DataFrame
    ) -> None:
        """
        최신 시트 업데이트를 큐에 추가 (전체 교체 모드)
        """
        # 컬럼 정렬
        data = reorder_columns(data.copy())
        
        # 업데이트 시간 추가
        data['_업데이트일시'] = get_current_timestamp()
        
        self.update_queue[sheet_name] = {
            'mode': 'replace',
            'data': data
        }
        
        logger.debug(f"큐 추가 (replace): {sheet_name} ({len(data)}행)")
    
    def queue_history_update(
        self,
        sheet_name: str,
        new_rows: pd.DataFrame,
        change_type: str = '신규'
    ) -> None:
        """
        이력 시트 업데이트를 큐에 추가 (추가 모드)
        """
        if new_rows.empty:
            return
        
        # 메타데이터 추가
        new_rows = new_rows.copy()
        new_rows['_변경유형'] = change_type
        new_rows['_변경일시'] = get_current_timestamp()
        
        # 컬럼 정렬
        new_rows = reorder_columns(new_rows)
        
        # 기존 큐에 추가
        if sheet_name in self.update_queue:
            existing = self.update_queue[sheet_name]['data']
            combined = pd.concat([existing, new_rows], ignore_index=True)
            self.update_queue[sheet_name]['data'] = combined
        else:
            self.update_queue[sheet_name] = {
                'mode': 'append',
                'data': new_rows
            }
        
        logger.debug(f"큐 추가 (append): {sheet_name} (+{len(new_rows)}행)")
    
    def flush_all_updates(self) -> Dict[str, Any]:
        """
        ★★★ 모든 큐를 처리하여 실제 Google Sheets 업데이트 ★★★
        
        Returns:
            처리 결과 딕셔너리
        """
        if not self.update_queue:
            logger.info("업데이트 큐가 비어있습니다")
            return {"success": True, "updated": 0}
        
        logger.info(f"\n{'='*50}")
        logger.info(f"📝 큐 플러시 시작: {len(self.update_queue)}개 시트")
        logger.info(f"{'='*50}")
        
        results = {
            "success": True,
            "updated": 0,
            "failed": 0,
            "details": {}
        }
        
        # 시트별로 순차 처리 (API 쿼터 보호)
        for sheet_name, update_info in self.update_queue.items():
            try:
                mode = update_info['mode']
                data = update_info['data']
                
                if mode == 'replace':
                    self._flush_replace(sheet_name, data)
                else:  # append
                    self._flush_append(sheet_name, data)
                
                results["updated"] += 1
                results["details"][sheet_name] = {
                    "success": True,
                    "mode": mode,
                    "rows": len(data)
                }
                
                logger.info(f"✅ {sheet_name}: {len(data)}행 ({mode})")
                
                # ★★★ API 쿼터 보호: 시트 간 대기 ★★★
                time.sleep(API_WRITE_DELAY)
                
            except Exception as e:
                logger.error(f"❌ {sheet_name} 업데이트 실패: {e}")
                results["failed"] += 1
                results["details"][sheet_name] = {
                    "success": False,
                    "error": str(e)
                }
                
                # 에러 발생해도 다음 시트 처리 (더 긴 대기)
                time.sleep(API_WRITE_DELAY * 2)
        
        # 큐 클리어
        self.update_queue.clear()
        self.sheet_cache.clear()
        
        logger.info(f"\n✅ 큐 플러시 완료: 성공 {results['updated']}, 실패 {results['failed']}")
        
        return results
    
    def _flush_replace(self, sheet_name: str, data: pd.DataFrame) -> None:
        """
        전체 교체 모드로 시트 업데이트 (최소 API 호출)
        """
        worksheet = self._get_or_create_worksheet(sheet_name)
        
        # 데이터 준비: 헤더 + 모든 데이터를 하나의 리스트로
        headers = list(data.columns)
        rows = data.fillna('').astype(str).values.tolist()
        all_data = [headers] + rows
        
        # 시트 크기 확인 및 조정 (필요 시 1회 API 호출)
        required_rows = len(all_data) + 10
        required_cols = len(headers) + 2
        
        if worksheet.row_count < required_rows or worksheet.col_count < required_cols:
            worksheet.resize(
                rows=max(worksheet.row_count, required_rows),
                cols=max(worksheet.col_count, required_cols)
            )
            time.sleep(1)
        
        # ★★★ clear + update (2회 API 호출) ★★★
        worksheet.clear()
        time.sleep(0.5)
        worksheet.update('A1', all_data, value_input_option='RAW')
    
    def _flush_append(self, sheet_name: str, data: pd.DataFrame) -> None:
        """
        추가 모드로 시트 업데이트 (최소 API 호출)
        """
        worksheet = self._get_or_create_worksheet(sheet_name)
        
        # 기존 데이터 확인 (1 API 호출)
        existing = worksheet.get_all_values()
        time.sleep(0.5)
        
        # 헤더 결정
        if not existing or len(existing) == 0:
            headers = list(data.columns)
            next_row = 2
            need_header = True
        else:
            headers = existing[0]
            next_row = len(existing) + 1
            need_header = False
            
            # 새 컬럼이 있으면 헤더 확장
            new_cols = [c for c in data.columns if c not in headers]
            if new_cols:
                # 메타데이터 컬럼은 맨 뒤로
                meta_cols = [h for h in headers if h.startswith('_')]
                non_meta = [h for h in headers if not h.startswith('_')]
                headers = non_meta + new_cols + meta_cols
                need_header = True
        
        # 데이터 준비 (헤더 순서에 맞춰서)
        rows = []
        for _, row in data.iterrows():
            row_values = [str(row.get(h, '')) if h in row.index else '' for h in headers]
            rows.append(row_values)
        
        # 시트 크기 확장 (필요 시 1회 API 호출)
        required_rows = next_row + len(rows) + 10
        required_cols = len(headers) + 2
        
        if worksheet.row_count < required_rows or worksheet.col_count < required_cols:
            worksheet.resize(
                rows=max(worksheet.row_count, required_rows),
                cols=max(worksheet.col_count, required_cols)
            )
            time.sleep(1)
        
        # ★★★ batch_update로 한 번에 쓰기 (1 API 호출) ★★★
        batch_data = []
        if need_header:
            batch_data.append({'range': 'A1', 'values': [headers]})
        if rows:
            batch_data.append({'range': f'A{next_row}', 'values': rows})
        
        if batch_data:
            worksheet.batch_update(batch_data, value_input_option='RAW')
    
    # =========================================================
    # ETRS 데이터 처리 (큐 시스템 사용)
    # =========================================================
    
    def process_etrs_update(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        ETRS 데이터셋 업데이트 처리 (큐에 추가만, 실제 쓰기는 flush에서)
        """
        logger.info(f"\n{'='*40}")
        logger.info(f"📊 {dataset_name} 처리 중...")
        logger.info(f"{'='*40}")
        
        if dataset_name not in ETRS_DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            return {"success": False}
        
        dataset = ETRS_DATASETS[dataset_name]
        sheet_latest = dataset['sheet_latest']
        sheet_history = dataset['sheet_history']
        key_cols = KEY_COLUMNS.get(dataset_name, [])
        value_cols = VALUE_COLUMNS.get(dataset_name, [])
        
        # 모든 계획기간 데이터 합치기
        all_data = []
        for period, df in period_data.items():
            df_copy = df.copy()
            if '_계획기간' not in df_copy.columns:
                df_copy['_계획기간'] = f"{period}차"
            all_data.append(df_copy)
        
        if not all_data:
            logger.warning(f"데이터 없음: {dataset_name}")
            return {"success": False}
        
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # 기존 데이터 가져오기 (비교용)
        old_data = self.get_sheet_data(sheet_latest)
        
        # ★★★ 최신 시트 업데이트 큐잉 ★★★
        self.queue_latest_update(sheet_latest, combined_df)
        
        # ★★★ 변경 감지 후 이력 시트 큐잉 ★★★
        if old_data.empty:
            # 첫 실행 - 모든 데이터가 신규
            self.queue_history_update(sheet_history, combined_df, '신규')
            changes = {'added': len(combined_df), 'changed': 0}
        else:
            # 변경 감지
            available_key_cols = [c for c in key_cols if c in combined_df.columns and c in old_data.columns]
            available_value_cols = [c for c in value_cols if c in combined_df.columns]
            
            if not available_key_cols:
                # 키 컬럼이 없으면 첫 2개 공통 컬럼 사용
                common = [c for c in combined_df.columns if c in old_data.columns and not c.startswith('_')]
                available_key_cols = common[:2] if common else []
            
            if available_key_cols:
                old_filtered = old_data[[c for c in old_data.columns if not c.startswith('_')]]
                change_result = compare_dataframes(
                    old_filtered, combined_df,
                    available_key_cols, available_value_cols
                )
                
                if not change_result['added'].empty:
                    self.queue_history_update(sheet_history, change_result['added'], '신규')
                if not change_result['changed'].empty:
                    self.queue_history_update(sheet_history, change_result['changed'], '변경')
                
                changes = {
                    'added': len(change_result['added']),
                    'changed': len(change_result['changed'])
                }
            else:
                # 비교 불가 - 전체를 신규로 처리
                self.queue_history_update(sheet_history, combined_df, '신규')
                changes = {'added': len(combined_df), 'changed': 0}
        
        logger.info(f"  → 최신: {len(combined_df)}행, 신규: {changes['added']}, 변경: {changes['changed']}")
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "rows": len(combined_df),
            "changes": changes
        }
    
    # =========================================================
    # ORS 데이터 처리 (큐 시스템 사용)
    # =========================================================
    
    def process_ors_update(
        self,
        dataset_name: str,
        data: pd.DataFrame
    ) -> Dict[str, Any]:
        """
        ORS 데이터셋 업데이트 처리 (큐에 추가만)
        """
        logger.info(f"\n{'='*40}")
        logger.info(f"📊 {dataset_name} 처리 중...")
        logger.info(f"{'='*40}")
        
        if dataset_name not in ORS_DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            return {"success": False}
        
        dataset = ORS_DATASETS[dataset_name]
        sheet_latest = dataset['sheet_latest']
        sheet_history = dataset['sheet_history']
        key_cols = KEY_COLUMNS.get(dataset_name, [])
        value_cols = VALUE_COLUMNS.get(dataset_name, [])
        
        if data.empty:
            logger.warning(f"데이터 없음: {dataset_name}")
            return {"success": False}
        
        # 기존 데이터 가져오기
        old_data = self.get_sheet_data(sheet_latest)
        
        # ★★★ 최신 시트 업데이트 큐잉 ★★★
        self.queue_latest_update(sheet_latest, data)
        
        # ★★★ 변경 감지 후 이력 시트 큐잉 ★★★
        if old_data.empty:
            self.queue_history_update(sheet_history, data, '신규')
            changes = {'added': len(data), 'changed': 0}
        else:
            available_key_cols = [c for c in key_cols if c in data.columns and c in old_data.columns]
            available_value_cols = [c for c in value_cols if c in data.columns]
            
            if not available_key_cols:
                common = [c for c in data.columns if c in old_data.columns and not c.startswith('_')]
                available_key_cols = common[:2] if common else []
            
            if available_key_cols:
                old_filtered = old_data[[c for c in old_data.columns if not c.startswith('_')]]
                change_result = compare_dataframes(
                    old_filtered, data,
                    available_key_cols, available_value_cols
                )
                
                if not change_result['added'].empty:
                    self.queue_history_update(sheet_history, change_result['added'], '신규')
                if not change_result['changed'].empty:
                    self.queue_history_update(sheet_history, change_result['changed'], '변경')
                
                changes = {
                    'added': len(change_result['added']),
                    'changed': len(change_result['changed'])
                }
            else:
                self.queue_history_update(sheet_history, data, '신규')
                changes = {'added': len(data), 'changed': 0}
        
        logger.info(f"  → 최신: {len(data)}행, 신규: {changes['added']}, 변경: {changes['changed']}")
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "rows": len(data),
            "changes": changes
        }
    
    # =========================================================
    # 레거시 호환 함수들 (기존 main.py 호환)
    # =========================================================
    
    def update_etrs_latest_sheet(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame]
    ) -> Dict[str, Any]:
        """레거시 호환 - process_etrs_update 사용 권장"""
        return self.process_etrs_update(dataset_name, period_data)
    
    def update_etrs_history_sheet(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame],
        old_data: pd.DataFrame = None
    ) -> Dict[str, Any]:
        """레거시 호환 - process_etrs_update가 이력도 처리함"""
        return {"success": True}
    
    def update_ors_latest_sheet(
        self,
        dataset_name: str,
        data: pd.DataFrame
    ) -> Dict[str, Any]:
        """레거시 호환 - process_ors_update 사용 권장"""
        return self.process_ors_update(dataset_name, data)
    
    def update_ors_history_sheet(
        self,
        dataset_name: str,
        data: pd.DataFrame,
        old_data: pd.DataFrame = None
    ) -> Dict[str, Any]:
        """레거시 호환 - process_ors_update가 이력도 처리함"""
        return {"success": True}


def create_handler_from_env() -> AllocationSheetsHandler:
    """환경변수에서 핸들러 생성"""
    credentials_json = os.environ.get('GOOGLE_SHEETS_CREDS')
    spreadsheet_id = os.environ.get('KAU_SHEET_ID')
    
    if not credentials_json:
        raise ValueError("GOOGLE_SHEETS_CREDS 환경변수가 설정되지 않았습니다")
    if not spreadsheet_id:
        raise ValueError("KAU_SHEET_ID 환경변수가 설정되지 않았습니다")
    
    return AllocationSheetsHandler(credentials_json, spreadsheet_id)
