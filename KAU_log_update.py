#!/usr/bin/env python3
"""
KRX ETS 거래시간 인식 데이터 수집기
실제 거래시간에 맞춘 스마트 수집 로직
"""

import requests
import gspread
import json
import os
import time
import logging
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from bs4 import BeautifulSoup
import re

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TradingHoursAwareCollector:
    def __init__(self):
        """거래시간 인식 수집기"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8',
            'Connection': 'keep-alive'
        })
        
        # KRX 거래시간 정보
        self.trading_hours = {
            'order_start': 9,      # 09:00 - 호가접수 시작
            'market_open': 10,     # 10:00 - 매매거래 시작  
            'market_close': 12,    # 12:00 - 매매거래 종료
            'order_end': 12        # 12:00 - 호가접수 종료
        }
        
        self.base_url = "https://ets.krx.co.kr"
    
    def get_current_market_phase(self) -> Dict[str, str]:
        """현재 시장 단계 확인"""
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        
        # 평일 여부 확인 (0=월요일, 6=일요일)
        is_weekday = now.weekday() < 5
        
        if not is_weekday:
            return {
                'phase': 'weekend',
                'description': '주말 - 시장 비운영',
                'next_action': '다음 거래일까지 대기',
                'is_trading_time': False
            }
        
        # 거래시간 단계 구분
        if current_hour < self.trading_hours['order_start']:
            phase = 'pre_market'
            description = '장 시작 전'
            next_action = f"{self.trading_hours['order_start']:02d}:00 호가접수 시작 대기"
            is_trading = False
            
        elif current_hour == self.trading_hours['order_start'] and current_minute < 60:
            phase = 'order_only'
            description = '호가접수만 가능'
            next_action = f"{self.trading_hours['market_open']:02d}:00 매매거래 시작 대기"
            is_trading = False
            
        elif (current_hour >= self.trading_hours['market_open'] and 
              current_hour < self.trading_hours['market_close']):
            phase = 'trading_hours'
            description = '매매거래 시간'
            next_action = f"{self.trading_hours['market_close']:02d}:00 장 마감까지"
            is_trading = True
            
        elif current_hour == self.trading_hours['market_close'] and current_minute < 30:
            phase = 'just_closed'
            description = '장 마감 직후'
            next_action = '최종 데이터 수집 완료 후 대기'
            is_trading = False
            
        else:
            phase = 'post_market'
            description = '장 마감 후'
            next_action = '내일 거래시간까지 대기'
            is_trading = False
        
        return {
            'phase': phase,
            'description': description,
            'next_action': next_action,
            'is_trading_time': is_trading,
            'current_time': now.strftime('%H:%M:%S'),
            'is_weekday': is_weekday
        }
    
    def should_collect_data(self, phase_info: Dict) -> bool:
        """데이터 수집 필요성 판단"""
        phase = phase_info['phase']
        
        # 수집이 필요한 단계들
        collect_phases = [
            'trading_hours',     # 거래시간 중 - 실시간 수집
            'just_closed',       # 장 마감 직후 - 최종 수집
            'post_market'        # 장 마감 후 - 확인 수집
        ]
        
        should_collect = phase in collect_phases
        
        logger.info(f"시장 단계: {phase_info['description']}")
        logger.info(f"수집 필요: {'예' if should_collect else '아니오'}")
        
        return should_collect
    
    def get_ets_data_with_context(self, date_str: str = None, phase: str = None) -> List[Dict]:
        """거래시간 컨텍스트를 포함한 데이터 수집"""
        try:
            if date_str is None:
                date_str = datetime.now().strftime('%Y%m%d')
            
            phase_info = self.get_current_market_phase()
            current_phase = phase or phase_info['phase']
            
            logger.info(f"=== KRX ETS 데이터 수집 시작 ===")
            logger.info(f"날짜: {date_str}")
            logger.info(f"시장 단계: {phase_info['description']}")
            logger.info(f"거래시간 여부: {phase_info['is_trading_time']}")
            
            # 거래시간이 아닌 경우 경고
            if not self.should_collect_data(phase_info) and current_phase not in ['pre_market', 'manual']:
                logger.warning(f"현재 시간({phase_info['current_time']})은 최적 수집시간이 아닙니다")
                logger.info(f"권장 수집시간: 10:30, 12:30, 15:00")
            
            # 실제 데이터 수집
            data = self._fetch_krx_data(date_str)
            
            # 데이터에 수집 컨텍스트 추가
            for item in data:
                item.update({
                    'collection_phase': current_phase,
                    'collection_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'is_trading_time': phase_info['is_trading_time'],
                    'market_status': phase_info['description']
                })
            
            logger.info(f"수집 완료: {len(data)}개 종목")
            
            return data
            
        except Exception as e:
            logger.error(f"데이터 수집 실패: {e}")
            return []
    
    def _fetch_krx_data(self, date_str: str) -> List[Dict]:
        """실제 KRX 데이터 가져오기"""
        try:
            # 메인 페이지 접속
            main_url = f"{self.base_url}/contents/ETS/03/03010000/ETS03010000.jsp"
            response = self.session.get(main_url, timeout=30)
            response.raise_for_status()
            
            # 데이터 요청
            data_url = f"{self.base_url}/contents/ETS/03/03010000/ETS03010000M.jsp"
            
            post_data = {
                'locale': 'ko_KR',
                'searchDate': date_str,
                'mktTpCd': '1',
                'csvxls_isNo': 'false'
            }
            
            response = self.session.post(data_url, data=post_data, timeout=30)
            response.raise_for_status()
            
            # 응답 파싱
            if 'json' in response.headers.get('content-type', ''):
                return self._parse_json_response(response.json(), date_str)
            else:
                return self._parse_html_response(response.text, date_str)
                
        except Exception as e:
            logger.error(f"KRX 데이터 가져오기 실패: {e}")
            return []
    
    def _parse_json_response(self, data: Dict, date_str: str) -> List[Dict]:
        """JSON 응답 파싱"""
        result_list = []
        
        try:
            if 'result' in data and isinstance(data['result'], list):
                for item in data['result']:
                    parsed_item = {
                        'date': self._format_date(date_str),
                        'symbol': item.get('itemCd', '').strip(),
                        'name': item.get('itemNm', '').strip(),
                        'current_price': self._safe_number(item.get('currentPrice', 0)),
                        'change': self._safe_number(item.get('change', 0)),
                        'change_rate': self._safe_number(item.get('changeRate', 0)),
                        'open_price': self._safe_number(item.get('openPrice', 0)),
                        'high_price': self._safe_number(item.get('highPrice', 0)),
                        'low_price': self._safe_number(item.get('lowPrice', 0)),
                        'volume': self._safe_number(item.get('volume', 0)),
                        'trading_value': self._safe_number(item.get('tradingValue', 0)),
                        'weighted_avg': self._safe_number(item.get('weightedAvg', 0))
                    }
                    result_list.append(parsed_item)
            
            return result_list
            
        except Exception as e:
            logger.error(f"JSON 파싱 오류: {e}")
            return []
    
    def _parse_html_response(self, html_content: str, date_str: str) -> List[Dict]:
        """HTML 응답 파싱"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            result_list = []
            
            table = soup.find('table')
            if not table:
                return []
            
            rows = table.find_all('tr')[1:]  # 헤더 제외
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                
                if len(cells) >= 9:
                    try:
                        parsed_item = {
                            'date': self._format_date(date_str),
                            'symbol': cells[0].get_text(strip=True),
                            'name': cells[0].get_text(strip=True),
                            'current_price': self._extract_number(cells[1].get_text(strip=True)),
                            'change': self._extract_number(cells[2].get_text(strip=True)),
                            'change_rate': self._extract_number(cells[3].get_text(strip=True)),
                            'open_price': self._extract_number(cells[4].get_text(strip=True)),
                            'high_price': self._extract_number(cells[5].get_text(strip=True)),
                            'low_price': self._extract_number(cells[6].get_text(strip=True)),
                            'volume': self._extract_number(cells[7].get_text(strip=True)),
                            'trading_value': self._extract_number(cells[8].get_text(strip=True)),
                            'weighted_avg': self._extract_number(cells[9].get_text(strip=True)) if len(cells) > 9 else 0
                        }
                        result_list.append(parsed_item)
                        
                    except Exception:
                        continue
            
            return result_list
            
        except Exception as e:
            logger.error(f"HTML 파싱 오류: {e}")
            return []
    
    def _format_date(self, date_str: str) -> str:
        """날짜 형식 변환"""
        try:
            if len(date_str) == 8:
                return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            return date_str
        except:
            return datetime.now().strftime('%Y-%m-%d')
    
    def _safe_number(self, value, default=0):
        """안전한 숫자 변환"""
        try:
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                cleaned = re.sub(r'[^\d.-]', '', value.replace(',', ''))
                return float(cleaned) if cleaned else default
            return default
        except:
            return default
    
    def _extract_number(self, text: str) -> float:
        """텍스트에서 숫자 추출"""
        try:
            if not text:
                return 0
            cleaned = re.sub(r'[^\d.-]', '', text.replace(',', ''))
            return float(cleaned) if cleaned else 0
        except:
            return 0

class TradingHoursAwareSheetsManager:
    def __init__(self, credentials_json: str, sheet_id: str):
        """거래시간 인식 시트 매니저"""
        try:
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            
            # 'ets.KRX' 워크시트 확인/생성
            try:
                self.worksheet = self.spreadsheet.worksheet('ets.KRX')
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(
                    title='ets.KRX', rows=1000, cols=12
                )
                self._setup_headers()
            
            logger.info("Google Sheets 연결 성공")
            
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def _setup_headers(self):
        """헤더 설정"""
        headers = [
            '날짜', '종목명', '현재가', '대비', '등락률', 
            '시가', '고가', '저가', '거래량', '거래대금', '가중평균', '수집단계'
        ]
        self.worksheet.update('A1:L1', [headers])
        
        # 헤더 서식
        self.worksheet.format('A1:L1', {
            'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 0.9},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
            'horizontalAlignment': 'CENTER'
        })
    
    def update_data_with_trading_context(self, ets_data: List[Dict]):
        """거래시간 컨텍스트를 포함한 데이터 업데이트"""
        try:
            if not ets_data:
                logger.warning("업데이트할 데이터가 없습니다")
                return
            
            current_date = ets_data[0]['date']
            collection_phase = ets_data[0].get('collection_phase', 'unknown')
            
            logger.info(f"날짜 {current_date}, 단계 {collection_phase} 데이터 업데이트")
            
            # 기존 같은 날짜 + 같은 단계 데이터 확인
            all_values = self.worksheet.get_all_values()
            
            # 같은 날짜의 데이터 찾기
            rows_to_update = []
            for i, row in enumerate(all_values[1:], start=2):
                if len(row) >= 12 and row[0] == current_date:
                    # 같은 날짜의 다른 수집단계는 유지, 같은 단계는 업데이트
                    existing_phase = row[11] if len(row) > 11 else ''
                    if existing_phase == collection_phase:
                        rows_to_update.append(i)
            
            # 기존 같은 단계 데이터 삭제
            for row_num in reversed(rows_to_update):
                self.worksheet.delete_rows(row_num)
                logger.info(f"기존 {collection_phase} 단계 데이터 삭제: 행 {row_num}")
            
            # 새 데이터 추가
            new_rows = []
            for item in ets_data:
                row = [
                    item['date'],
                    item['symbol'] or item['name'],
                    item['current_price'],
                    item['change'],
                    item['change_rate'],
                    item['open_price'],
                    item['high_price'],
                    item['low_price'],
                    item['volume'],
                    item['trading_value'],
                    item['weighted_avg'],
                    item.get('collection_phase', 'unknown')
                ]
                new_rows.append(row)
            
            if new_rows:
                self.worksheet.append_rows(new_rows)
                logger.info(f"{len(new_rows)}개 {collection_phase} 단계 데이터 추가")
                
                # 정렬 및 형식 적용
                self._sort_and_format_data()
                
                # 거래시간별 통계
                self._log_trading_statistics(ets_data)
            
        except Exception as e:
            logger.error(f"데이터 업데이트 실패: {e}")
            raise
    
    def _sort_and_format_data(self):
        """데이터 정렬 및 형식 적용"""
        try:
            # 날짜 내림차순, 수집단계 순서로 정렬
            all_values = self.worksheet.get_all_values()
            if len(all_values) <= 1:
                return
            
            headers = all_values[0]
            data_rows = all_values[1:]
            
            # 정렬 (날짜 desc, 수집단계 asc)
            phase_order = {'trading_hours': 1, 'just_closed': 2, 'post_market': 3, 'pre_market': 4}
            
            data_rows.sort(key=lambda x: (
                x[0],  # 날짜
                phase_order.get(x[11] if len(x) > 11 else '', 99)  # 수집단계
            ), reverse=True)
            
            # 업데이트
            sorted_data = [headers] + data_rows
            range_name = f'A1:L{len(sorted_data)}'
            self.worksheet.update(range_name, sorted_data)
            
            # 숫자 형식 적용
            self._apply_number_formats()
            
        except Exception as e:
            logger.warning(f"정렬 중 오류: {e}")
    
    def _apply_number_formats(self):
        """숫자 형식 적용"""
        try:
            last_row = len(self.worksheet.get_all_values())
            
            if last_row > 1:
                # 가격 컬럼
                price_format = {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}}
                for col in ['C', 'D', 'F', 'G', 'H', 'K']:  # 현재가, 대비, 시가, 고가, 저가, 가중평균
                    self.worksheet.format(f'{col}2:{col}{last_row}', price_format)
                
                # 등락률
                self.worksheet.format(f'E2:E{last_row}', {
                    'numberFormat': {'type': 'NUMBER', 'pattern': '0.00%'}
                })
                
                # 거래량, 거래대금
                volume_format = {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}}
                for col in ['I', 'J']:
                    self.worksheet.format(f'{col}2:{col}{last_row}', volume_format)
                
        except Exception as e:
            logger.warning(f"형식 적용 중 오류: {e}")
    
    def _log_trading_statistics(self, ets_data: List[Dict]):
        """거래 통계 로깅"""
        try:
            total_volume = sum(item['volume'] for item in ets_data)
            active_items = len([item for item in ets_data if item['volume'] > 0])
            total_items = len(ets_data)
            
            collection_phase = ets_data[0].get('collection_phase', 'unknown')
            is_trading_time = ets_data[0].get('is_trading_time', False)
            
            logger.info(f"=== 거래 통계 ({collection_phase}) ===")
            logger.info(f"거래시간 여부: {is_trading_time}")
            logger.info(f"총 거래량: {total_volume:,} 톤")
            logger.info(f"활성 종목: {active_items}/{total_items}개")
            
            if is_trading_time:
                logger.info("🔥 실시간 거래시간 중 수집된 데이터")
            else:
                logger.info("📊 장외시간 수집된 데이터")
                
        except Exception as e:
            logger.warning(f"통계 로깅 중 오류: {e}")

def main():
    """메인 실행 함수"""
    parser = argparse.ArgumentParser(description='KRX ETS 거래시간 인식 수집기')
    parser.add_argument('--date', help='수집할 날짜 (YYYYMMDD)')
    parser.add_argument('--phase', help='수집 단계 지정')
    parser.add_argument('--force', action='store_true', help='거래시간 무시하고 강제 수집')
    
    args = parser.parse_args()
    
    try:
        logger.info("=== KRX ETS 거래시간 인식 수집기 시작 ===")
        
        # 환경변수 확인
        google_creds = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        
        if not google_creds or not sheet_id:
            raise ValueError("필수 환경변수가 설정되지 않았습니다")
        
        # 수집기 및 매니저 초기화
        collector = TradingHoursAwareCollector()
        sheets_manager = TradingHoursAwareSheetsManager(google_creds, sheet_id)
        
        # 현재 시장 상태 확인
        phase_info = collector.get_current_market_phase()
        
        logger.info(f"📊 현재 시장 상태: {phase_info['description']}")
        logger.info(f"🕐 현재 시간: {phase_info['current_time']} KST")
        logger.info(f"📈 거래시간: 10:00-12:00, 호가접수: 09:00-12:00")
        
        # 수집 필요성 판단
        if not args.force and not collector.should_collect_data(phase_info):
            logger.info(f"💡 권장 수집시간: 10:30 (장시작), 12:30 (장마감), 15:00 (일일정리)")
            logger.info(f"⏰ 다음 액션: {phase_info['next_action']}")
            
            if phase_info['phase'] == 'weekend':
                logger.info("주말이므로 수집을 건너뜁니다.")
                return
        
        # 데이터 수집
        target_date = args.date or datetime.now().strftime('%Y%m%d')
        ets_data = collector.get_ets_data_with_context(target_date, args.phase)
        
        if ets_data:
            # Google Sheets 업데이트
            sheets_manager.update_data_with_trading_context(ets_data)
            
            # 결과 요약
            collection_phase = ets_data[0].get('collection_phase', 'unknown')
            is_trading_time = ets_data[0].get('is_trading_time', False)
            
            logger.info("✅ 수집 및 업데이트 완료!")
            print(f"✅ 성공: {len(ets_data)}개 종목 데이터 수집")
            print(f"📊 수집 단계: {collection_phase}")
            print(f"🕐 거래시간 여부: {'예' if is_trading_time else '아니오'}")
            
        else:
            logger.warning("⚠️ 수집된 데이터가 없습니다")
            print("⚠️ 수집된 데이터가 없습니다. 시장 상태를 확인해주세요.")
        
    except Exception as e:
        logger.error(f"❌ 실행 중 오류: {e}")
        print(f"❌ 오류: {e}")
        raise

if __name__ == "__main__":
    main()
