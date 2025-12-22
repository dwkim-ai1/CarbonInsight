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

★★★ 변경사항 (v1.1) ★★★
- process_etrs_update_incremental(): 기존 데이터에 새 연도 데이터 merge
- 자동 수집 모드 지원
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
        
        ★★★ 헤더 검증: 첫 번째 행이 유효한 헤더인지 확인 ★★★
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
                
                # ★★★ 헤더 유효성 검증 ★★★
                # 유효한 헤더: 문자열이고, 숫자로만 이루어지지 않음
                valid_header = self._is_valid_header(headers)
                
                if not valid_header:
                    logger.warning(f"⚠️ {sheet_name}: 헤더가 없거나 유효하지 않음 - 첫 번째 행: {headers[:5]}...")
                    # 헤더가 없으면 빈 DataFrame 반환 (안전 모드)
                    return pd.DataFrame()
                
                df = pd.DataFrame(data, columns=headers)
                self.sheet_cache[sheet_name] = df
                logger.debug(f"시트 데이터 로드: {sheet_name} ({len(df)}행)")
                return df
            elif all_values and len(all_values) == 1:
                headers = all_values[0]
                if self._is_valid_header(headers):
                    df = pd.DataFrame(columns=headers)
                    self.sheet_cache[sheet_name] = df
                    return df
                else:
                    logger.warning(f"⚠️ {sheet_name}: 헤더만 있어야 하는데 데이터처럼 보임")
                    return pd.DataFrame()
            else:
                return pd.DataFrame()
        except gspread.WorksheetNotFound:
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"시트 데이터 로드 실패: {sheet_name} - {e}")
            return pd.DataFrame()
    
    def _is_valid_header(self, row: List[str]) -> bool:
        """
        행이 유효한 헤더인지 검증
        
        유효한 헤더 조건:
        1. 빈 값이 아닌 셀이 최소 2개 이상
        2. 알려진 헤더 키워드 포함 (업체명, 부문, 이행연도 등)
        3. 순수 숫자로만 이루어진 셀이 과반수 미만
        """
        if not row:
            return False
        
        # 빈 값 제외
        non_empty = [str(cell).strip() for cell in row if str(cell).strip()]
        
        if len(non_empty) < 2:
            return False
        
        # 알려진 헤더 키워드 체크
        known_headers = {'업체명', '부문', '이행연도', '계획기간', '할당량', '배출량', 
                        '인증', '이월', '차입', '상쇄', '순번', '업종', '사업장'}
        
        for cell in non_empty:
            for keyword in known_headers:
                if keyword in cell:
                    return True
        
        # 숫자로만 이루어진 셀 비율 체크
        numeric_count = 0
        for cell in non_empty:
            # 숫자, 쉼표, 마이너스만 있으면 숫자로 간주
            cleaned = cell.replace(',', '').replace('-', '').replace('.', '')
            if cleaned.isdigit():
                numeric_count += 1
        
        # 숫자 셀이 과반수 이상이면 데이터 행으로 간주
        if numeric_count >= len(non_empty) / 2:
            return False
        
        return True
    
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
        
        for sheet_name, update_info in self.update_queue.items():
            try:
                mode = update_info['mode']
                data = update_info['data']
                
                if mode == 'replace':
                    self._write_sheet_replace(sheet_name, data)
                else:  # append
                    self._write_sheet_append(sheet_name, data)
                
                results['updated'] += 1
                results['details'][sheet_name] = {
                    'success': True,
                    'mode': mode,
                    'rows': len(data)
                }
                
                logger.info(f"✅ {sheet_name}: {len(data)}행 ({mode})")
                
                # API 쿼터 보호
                time.sleep(API_WRITE_DELAY)
                
            except Exception as e:
                logger.error(f"❌ {sheet_name} 업데이트 실패: {e}")
                results['failed'] += 1
                results['details'][sheet_name] = {
                    'success': False,
                    'error': str(e)
                }
        
        # 큐 클리어
        self.update_queue.clear()
        self.sheet_cache.clear()
        
        results['success'] = results['failed'] == 0
        logger.info(f"\n✅ 큐 플러시 완료: 성공 {results['updated']}, 실패 {results['failed']}")
        
        return results
    
    def _write_sheet_replace(self, sheet_name: str, data: pd.DataFrame) -> None:
        """시트 전체 교체 (1-2 API 호출)"""
        worksheet = self._get_or_create_worksheet(
            sheet_name,
            rows=max(1000, len(data) + 100),
            cols=max(30, len(data.columns) + 5)
        )
        
        # 기존 데이터 클리어 (1 API 호출)
        worksheet.clear()
        time.sleep(1)
        
        # 새 데이터 쓰기 (1 API 호출)
        headers = data.columns.tolist()
        rows = data.fillna('').astype(str).values.tolist()
        
        all_data = [headers] + rows
        worksheet.update('A1', all_data, value_input_option='RAW')
    
    def _write_sheet_append(self, sheet_name: str, data: pd.DataFrame) -> None:
        """시트에 데이터 추가 (1-2 API 호출)"""
        worksheet = self._get_or_create_worksheet(sheet_name)
        
        # 현재 데이터 확인
        current_data = worksheet.get_all_values()
        
        headers = data.columns.tolist()
        rows = data.fillna('').astype(str).values.tolist()
        
        # ★★★ 헤더 확인 로직 개선 ★★★
        if len(current_data) == 0:
            # 완전히 빈 시트
            need_header = True
            next_row = 2  # 헤더 다음 행
        elif all(not str(cell).strip() for cell in current_data[0]):
            # 첫 번째 행이 모두 빈 값
            logger.debug(f"{sheet_name}: 첫 번째 행이 비어있음 - 헤더 추가")
            need_header = True
            next_row = 2  # 헤더 다음 행
        elif not self._is_valid_header(current_data[0]):
            # 첫 번째 행이 유효한 헤더가 아님 (데이터처럼 보임)
            logger.warning(f"⚠️ {sheet_name}: 헤더 없음 감지 - 헤더 추가 후 데이터 append")
            need_header = True
            # 기존 데이터가 있으므로, 헤더를 먼저 쓰고 기존 데이터 유지
            # 이 경우 기존 데이터 위에 헤더를 삽입해야 하므로 별도 처리
            self._insert_header_and_append(worksheet, headers, rows, current_data)
            return
        else:
            # 정상적인 헤더 존재
            need_header = False
            next_row = len(current_data) + 1
        
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
            next_row = 2  # 헤더 다음 행부터
        if rows:
            batch_data.append({'range': f'A{next_row}', 'values': rows})
        
        if batch_data:
            worksheet.batch_update(batch_data, value_input_option='RAW')
    
    def _insert_header_and_append(
        self, 
        worksheet, 
        headers: List[str], 
        new_rows: List[List[str]], 
        existing_data: List[List[str]]
    ) -> None:
        """
        헤더가 없는 시트에 헤더를 삽입하고 새 데이터 추가
        
        기존 데이터 위에 헤더를 추가하고, 새 데이터를 맨 아래에 append
        """
        # 시트 크기 확장
        total_rows = 1 + len(existing_data) + len(new_rows) + 10
        total_cols = max(len(headers), max(len(row) for row in existing_data) if existing_data else 0) + 2
        
        if worksheet.row_count < total_rows or worksheet.col_count < total_cols:
            worksheet.resize(rows=total_rows, cols=total_cols)
            time.sleep(1)
        
        # 전체 데이터 구성: 헤더 + 기존 데이터 + 새 데이터
        all_data = [headers] + existing_data + new_rows
        
        # 전체 교체 (기존 데이터 + 헤더 + 새 데이터)
        worksheet.clear()
        time.sleep(1)
        worksheet.update('A1', all_data, value_input_option='RAW')
        
        logger.info(f"  → 헤더 삽입 완료: 기존 {len(existing_data)}행 + 신규 {len(new_rows)}행")
    
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
        전체 데이터 교체 모드
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
    
    def process_etrs_update_incremental(
        self,
        dataset_name: str,
        period_data: Dict[int, pd.DataFrame]
    ) -> Dict[str, Any]:
        """
        ★★★ ETRS 데이터셋 증분 업데이트 처리 (자동 수집 모드) ★★★
        
        올바른 로직:
        - 최신 연도 데이터만 수집
        - 누적 시트의 기존 데이터에 없는 최신 연도 데이터만 필터링
        - 해당 데이터만 append (기존 데이터는 건드리지 않음)
        
        예: 기존 ETRS_인증배출량에 2021~2023년 데이터가 있고,
            새로 수집한 2024년 데이터 중 기존에 없는 것만 append
        """
        logger.info(f"\n{'='*40}")
        logger.info(f"📊 {dataset_name} 증분 업데이트 처리 중...")
        logger.info(f"{'='*40}")
        
        if dataset_name not in ETRS_DATASETS:
            logger.error(f"알 수 없는 데이터셋: {dataset_name}")
            return {"success": False}
        
        dataset = ETRS_DATASETS[dataset_name]
        sheet_latest = dataset['sheet_latest']
        sheet_history = dataset['sheet_history']
        key_cols = KEY_COLUMNS.get(dataset_name, [])
        value_cols = VALUE_COLUMNS.get(dataset_name, [])
        has_impl_year = dataset.get('has_implementation_year', False)
        
        # 새로 수집한 데이터 합치기
        new_data_list = []
        for period, df in period_data.items():
            df_copy = df.copy()
            if '_계획기간' not in df_copy.columns:
                df_copy['_계획기간'] = f"{period}차"
            new_data_list.append(df_copy)
        
        if not new_data_list:
            logger.warning(f"새 데이터 없음: {dataset_name}")
            return {"success": False}
        
        new_df = pd.concat(new_data_list, ignore_index=True)
        
        # 기존 데이터 가져오기
        old_data = self.get_sheet_data(sheet_latest, use_cache=False)
        
        if old_data.empty:
            # ★★★ 첫 실행 또는 헤더 없는 시트 → 새 데이터로 전체 교체 ★★★
            logger.info(f"기존 데이터 없음 (첫 실행 또는 헤더 없음): {len(new_df)}행 저장")
            self.queue_latest_update(sheet_latest, new_df)
            self.queue_history_update(sheet_history, new_df, '신규')
            return {
                "success": True,
                "dataset_name": dataset_name,
                "rows": len(new_df),
                "changes": {'added': len(new_df), 'changed': 0}
            }
        
        # ★★★ 핵심 로직: 기존 데이터에 없는 새 데이터만 필터링 ★★★
        if has_impl_year and '이행연도' in new_df.columns:
            # 이행연도 데이터셋: 업체명 + 부문 + 이행연도로 중복 체크
            rows_to_append = self._filter_new_rows_by_impl_year(old_data, new_df, key_cols)
        else:
            # 일반 데이터셋: 키 컬럼으로 중복 체크
            rows_to_append = self._filter_new_rows_by_key(old_data, new_df, key_cols)
        
        if rows_to_append.empty:
            logger.info(f"추가할 새 데이터 없음 (이미 모든 데이터가 존재)")
            return {
                "success": True,
                "dataset_name": dataset_name,
                "rows": len(old_data),
                "changes": {'added': 0, 'changed': 0}
            }
        
        logger.info(f"기존 {len(old_data)}행 + 신규 {len(rows_to_append)}행 append")
        
        # ★★★ 기존 데이터 유지 + 새 데이터 append ★★★
        combined_df = pd.concat([old_data, rows_to_append], ignore_index=True)
        
        # 정렬 (이행연도 있으면 내림차순)
        if '이행연도' in combined_df.columns:
            combined_df = combined_df.sort_values('이행연도', ascending=False)
        
        # ★★★ 최신 시트 업데이트 큐잉 ★★★
        self.queue_latest_update(sheet_latest, combined_df)
        
        # ★★★ 이력 시트에 새로 추가된 데이터만 append ★★★
        self.queue_history_update(sheet_history, rows_to_append, '신규')
        
        changes = {'added': len(rows_to_append), 'changed': 0}
        
        logger.info(f"  → 최신: {len(combined_df)}행, 신규 append: {changes['added']}행")
        
        return {
            "success": True,
            "dataset_name": dataset_name,
            "rows": len(combined_df),
            "changes": changes
        }
    
    def _filter_new_rows_by_impl_year(
        self,
        old_df: pd.DataFrame,
        new_df: pd.DataFrame,
        key_cols: List[str]
    ) -> pd.DataFrame:
        """
        ★★★ 이행연도 기반으로 기존 데이터에 없는 행만 필터링 ★★★
        
        인증배출량, 배출권이월량, 배출권차입량 데이터셋용
        
        - 키: 업체명 + 부문 + 이행연도
        - 기존 데이터에 이미 있는 키는 제외
        - 새 데이터 중 기존에 없는 것만 반환
        """
        # 이행연도가 있는 경우, 키에 이행연도 포함
        full_key_cols = key_cols.copy()
        if '이행연도' not in full_key_cols:
            full_key_cols.append('이행연도')
        
        # 사용 가능한 키 컬럼만 필터링
        available_key_cols = [c for c in full_key_cols if c in old_df.columns and c in new_df.columns]
        
        if not available_key_cols:
            # 키 없으면 전체 반환 (안전 장치)
            logger.warning("키 컬럼을 찾을 수 없어 전체 데이터 반환")
            return new_df.copy()
        
        # 기존 데이터의 키 세트 생성
        existing_keys = set()
        for _, row in old_df.iterrows():
            key = tuple(str(row.get(c, '')).strip() for c in available_key_cols)
            existing_keys.add(key)
        
        logger.debug(f"기존 데이터 키 수: {len(existing_keys)}")
        
        # 새 데이터에서 기존에 없는 행만 필터링
        new_rows = []
        for _, row in new_df.iterrows():
            key = tuple(str(row.get(c, '')).strip() for c in available_key_cols)
            if key not in existing_keys:
                new_rows.append(row)
        
        if new_rows:
            result_df = pd.DataFrame(new_rows)
            logger.info(f"새로 추가할 행: {len(result_df)}개 (키: {available_key_cols})")
            return result_df
        else:
            return pd.DataFrame(columns=new_df.columns)
    
    def _filter_new_rows_by_key(
        self,
        old_df: pd.DataFrame,
        new_df: pd.DataFrame,
        key_cols: List[str]
    ) -> pd.DataFrame:
        """
        키 기반으로 기존 데이터에 없는 행만 필터링 (일반 데이터셋용)
        """
        available_key_cols = [c for c in key_cols if c in old_df.columns and c in new_df.columns]
        
        if not available_key_cols:
            # 키 없으면 공통 컬럼 중 처음 2개 사용
            common = [c for c in new_df.columns if c in old_df.columns and not c.startswith('_')]
            available_key_cols = common[:2] if common else []
        
        if not available_key_cols:
            logger.warning("키 컬럼을 찾을 수 없어 전체 데이터 반환")
            return new_df.copy()
        
        # 기존 데이터의 키 세트
        existing_keys = set()
        for _, row in old_df.iterrows():
            key = tuple(str(row.get(c, '')).strip() for c in available_key_cols)
            existing_keys.add(key)
        
        # 새 데이터에서 기존에 없는 행만 필터링
        new_rows = []
        for _, row in new_df.iterrows():
            key = tuple(str(row.get(c, '')).strip() for c in available_key_cols)
            if key not in existing_keys:
                new_rows.append(row)
        
        if new_rows:
            result_df = pd.DataFrame(new_rows)
            logger.info(f"새로 추가할 행: {len(result_df)}개 (키: {available_key_cols})")
            return result_df
        else:
            return pd.DataFrame(columns=new_df.columns)
    
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
