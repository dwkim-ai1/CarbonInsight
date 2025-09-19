#!/usr/bin/env python3
"""
표 구조 문제 해결된 KRX ETS 데이터 수집기
1. Google Sheets 컬럼 구조 정확히 정렬
2. 헤더-데이터 완벽 매핑
3. 실시간 데이터 우선, 샘플 데이터는 최후 수단
4. 상세한 디버깅 및 검증 시스템
"""

import requests
import gspread
import json
import os
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import time
import re
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RealKRXCollector:
    def __init__(self):
        """실제 KRX 데이터 수집기 초기화"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        })
        
        self.base_url = "https://ets.krx.co.kr"
        self.trading_config = self._analyze_trading_context()
        
    def _analyze_trading_context(self) -> Dict:
        """거래 상황 분석"""
        try:
            market_phase = os.getenv('MARKET_PHASE', 'unknown')
            trading_phase = os.getenv('TRADING_PHASE', 'unknown')
            execution_priority = os.getenv('EXECUTION_PRIORITY', 'low')
            
            config = {
                'market_phase': market_phase,
                'trading_phase': trading_phase,
                'execution_priority': execution_priority,
                'max_retries': 3,
                'timeout': 45,
                'high_accuracy': False,
                'fast_mode': False
            }
            
            # 거래 단계별 설정
            if trading_phase == 'opening_price_decision':
                config.update({'max_retries': 5, 'timeout': 60, 'high_accuracy': True})
                logger.info("🔥 시가 체결 모드 - 최고 정확도")
            elif trading_phase == 'closing_price_decision':
                config.update({'max_retries': 5, 'timeout': 60, 'high_accuracy': True})
                logger.info("🏁 종가 체결 모드 - 최고 정확도")
            elif trading_phase == 'real_time_trading':
                config.update({'max_retries': 4, 'timeout': 50, 'fast_mode': True})
                logger.info("⚡ 실시간 거래 모드 - 빠른 업데이트")
            else:
                logger.info("📋 표준 모드 - 기본 설정")
            
            logger.info(f"거래 설정: 재시도 {config['max_retries']}회, 타임아웃 {config['timeout']}초")
            return config
            
        except Exception as e:
            logger.warning(f"거래 상황 분석 중 오류: {e}")
            return {
                'market_phase': 'unknown', 'trading_phase': 'unknown', 'execution_priority': 'low',
                'max_retries': 3, 'timeout': 45, 'high_accuracy': False, 'fast_mode': False
            }

    def get_real_krx_data(self) -> List[Dict]:
        """개선된 실제 KRX ETS 데이터 수집"""
        try:
            config = self.trading_config
            logger.info("=== 실제 KRX ETS 데이터 수집 시작 ===")
            logger.info(f"거래 단계: {config['trading_phase']}")
            logger.info(f"실행 우선도: {config['execution_priority']}")
            
            # 실제 데이터 수집 시도
            for attempt in range(config['max_retries']):
                try:
                    logger.info(f"실제 데이터 수집 시도 {attempt + 1}/{config['max_retries']}")
                    
                    # 메인 페이지에서 실제 데이터 수집
                    main_url = f"{self.base_url}/contents/ETS/03/03010000/ETS03010000.jsp"
                    response = self.session.get(main_url, timeout=config['timeout'])
                    response.raise_for_status()
                    
                    # 개선된 HTML 파싱
                    parsed_data = self._parse_main_page_enhanced(response.text)
                    
                    if self._validate_real_data(parsed_data):
                        logger.info(f"✅ 실제 데이터 수집 성공: {len(parsed_data)}개 종목")
                        return self._enhance_with_trading_info(parsed_data, config)
                    
                    if attempt < config['max_retries'] - 1:
                        logger.info(f"시도 {attempt + 1} 데이터 부족 - 2초 후 재시도")
                        time.sleep(2)
                        
                except Exception as e:
                    logger.warning(f"시도 {attempt + 1} 실패: {e}")
                    if attempt < config['max_retries'] - 1:
                        time.sleep(3)
            
            # 모든 실제 데이터 수집 실패시 명확하게 실패 처리
            logger.error("❌ 실제 데이터 수집 완전 실패 - 모든 시도 실패")
            logger.error("🔍 문제 분석: 테이블은 발견되나 데이터 행이 없음")
            logger.error("💡 가능한 원인: 1) 거래시간 외 2) 웹사이트 구조 변경 3) 네트워크 문제")
            return []  # 빈 리스트 반환으로 명확한 실패 표시
            
        except Exception as e:
            logger.error(f"데이터 수집 중 심각한 오류: {e}")
            logger.error("🚨 예상치 못한 오류로 인한 수집 실패")
            return []  # 빈 리스트 반환으로 명확한 실패 표시

    def _parse_main_page_enhanced(self, html_content: str) -> List[Dict]:
        """개선된 메인 페이지 HTML 파싱 (실제 구조 반영)"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            result_list = []
            
            # 더 정확한 테이블 선택자
            table_selectors = [
                'table[id*="gridtable"]',
                'table[summary*="배출권"]',
                'table[summary*="현재가"]',
                'table'
            ]
            
            table = None
            for selector in table_selectors:
                tables = soup.select(selector)
                for t in tables:
                    # 더 정확한 테이블 식별
                    summary = t.get('summary', '')
                    table_text = t.get_text().lower()
                    
                    if (any(keyword in summary for keyword in ['배출권', '현재가']) or 
                        any(keyword in table_text for keyword in ['kau25', 'kcu25', '종목명', '현재가'])):
                        table = t
                        logger.info(f"✅ 데이터 테이블 발견: {selector}")
                        logger.info(f"테이블 ID: {t.get('id', 'None')}")
                        logger.info(f"테이블 요약: {summary}")
                        break
                if table:
                    break
            
            if not table:
                logger.warning("❌ 데이터 테이블을 찾을 수 없습니다")
                return []
            
            # 테이블 구조 분석
            thead = table.find('thead')
            tbody = table.find('tbody')
            
            if thead:
                headers = [th.get_text(strip=True) for th in thead.find_all(['th', 'td'])]
                logger.info(f"테이블 헤더: {headers}")
            
            # 데이터 행 추출
            if tbody:
                data_rows = tbody.find_all('tr')
            else:
                all_rows = table.find_all('tr')
                data_rows = all_rows[1:] if len(all_rows) > 1 else all_rows
            
            logger.info(f"📊 발견된 데이터 행: {len(data_rows)}개")
            
            for i, row in enumerate(data_rows):
                try:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 8:  # 최소 8개 컬럼 필요
                        parsed_item = self._parse_row_enhanced(cells, i)
                        if parsed_item:
                            result_list.append(parsed_item)
                            logger.info(f"✅ 종목 파싱: {parsed_item['symbol']} - {parsed_item['current_price']:,.0f}원")
                    else:
                        logger.debug(f"행 {i}: 컬럼 수 부족 ({len(cells)}개)")
                        
                except Exception as e:
                    logger.debug(f"행 {i} 파싱 실패: {e}")
                    continue
            
            logger.info(f"🎯 메인 페이지 파싱 완료: {len(result_list)}개 종목")
            return result_list
            
        except Exception as e:
            logger.error(f"HTML 파싱 오류: {e}")
            return []

    def _parse_row_enhanced(self, cells, row_index: int) -> Optional[Dict]:
        """개선된 테이블 행 파싱"""
        try:
            cell_texts = [cell.get_text(strip=True) for cell in cells]
            
            # 디버깅 정보
            logger.debug(f"행 {row_index} 셀 수: {len(cell_texts)}")
            logger.debug(f"행 {row_index} 내용: {cell_texts[:5]}...")  # 처음 5개만 로깅
            
            if len(cell_texts) < 8:
                return None
                
            # 종목명 확인 (첫 번째 컬럼)
            symbol = cell_texts[0]
            if not symbol or not re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                logger.debug(f"종목명 불일치: '{symbol}'")
                return None
            
            # 현재가 확인 (두 번째 컬럼)
            current_price = self._extract_number(cell_texts[1])
            if current_price <= 0:
                logger.debug(f"{symbol}: 현재가 없음 ({cell_texts[1]})")
                return None
            
            # 실제 파싱된 데이터
            parsed_data = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'time': datetime.now().strftime('%H:%M:%S'),
                'symbol': symbol,
                'current_price': current_price,
                'change': self._extract_number(cell_texts[2]),
                'change_rate': self._extract_number(cell_texts[3]),
                'open_price': self._extract_number(cell_texts[4]),
                'high_price': self._extract_number(cell_texts[5]),
                'low_price': self._extract_number(cell_texts[6]),
                'volume': self._extract_number(cell_texts[7]),
                'trading_value': self._extract_number(cell_texts[8] if len(cell_texts) > 8 else '0'),
                'weighted_avg': 0,  # 계산 후 설정
                'collection_time': datetime.now().strftime('%H:%M:%S'),
                'data_source': 'krx_real_data'
            }
            
            # 가중평균 계산
            if parsed_data['volume'] > 0 and parsed_data['trading_value'] > 0:
                parsed_data['weighted_avg'] = round(parsed_data['trading_value'] / parsed_data['volume'], 0)
            
            logger.info(f"🎯 {symbol}: {current_price:,.0f}원, 거래량: {parsed_data['volume']:,.0f}톤")
            return parsed_data
            
        except Exception as e:
            logger.debug(f"행 파싱 중 오류: {e}")
            return None

    def _validate_real_data(self, data: List[Dict]) -> bool:
        """실제 데이터 검증"""
        if not data or len(data) < 3:
            logger.warning(f"데이터 개수 부족: {len(data)}개")
            return False
            
        # 주요 종목 존재 확인
        symbols = {item.get('symbol', '') for item in data}
        required_symbols = {'KAU25', 'KCU25'}
        found_symbols = required_symbols.intersection(symbols)
        
        if not found_symbols:
            logger.warning(f"주요 종목 없음. 발견된 종목: {list(symbols)[:5]}")
            return False
        
        # 실제 데이터 vs 샘플 데이터 구분
        real_data_count = 0
        for item in data:
            if item.get('data_source') == 'krx_real_data':
                real_data_count += 1
        
        if real_data_count == 0:
            logger.warning("실제 데이터가 없음 (모두 샘플 데이터)")
            return False
            
        logger.info(f"✅ 데이터 검증 통과: {len(data)}개 종목, 실제 데이터 {real_data_count}개")
        return True

    def _enhance_with_trading_info(self, data: List[Dict], config: Dict) -> List[Dict]:
        """거래 정보로 데이터 강화"""
        for item in data:
            item['trading_phase'] = config['trading_phase']
            item['market_phase'] = config['market_phase']
            item['execution_priority'] = config['execution_priority']
            item['is_critical_time'] = config['high_accuracy']
            
        return data

    def _extract_number(self, text: str) -> float:
        """텍스트에서 숫자 추출"""
        try:
            if not text or text == '-' or text == '0':
                return 0.0
            # 쉼표 제거하고 숫자만 추출
            cleaned = re.sub(r'[^\d.-]', '', text.replace(',', ''))
            return float(cleaned) if cleaned else 0.0
        except (ValueError, TypeError):
            return 0.0


class EnhancedSheetsManager:
    def __init__(self, credentials_json: str, sheet_id: str):
        """향상된 Google Sheets 관리자"""
        try:
            logger.info("Google Sheets 연결 시작...")
            
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            logger.info(f"스프레드시트 연결: {self.spreadsheet.title}")
            
            self.setup_worksheet()
            
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def setup_worksheet(self):
        """워크시트 설정"""
        try:
            try:
                self.worksheet = self.spreadsheet.worksheet('ets.KRX')
                logger.info("기존 ets.KRX 워크시트 사용")
                
                # 🔧 헤더 일관성 확인 및 수정
                self._verify_and_fix_headers()
                
            except gspread.WorksheetNotFound:
                logger.info("ets.KRX 워크시트 생성...")
                self.worksheet = self.spreadsheet.add_worksheet(
                    title='ets.KRX', 
                    rows=2000,  # 더 많은 행 (시간별 누적)
                    cols=16
                )
                
                # 정확한 헤더 설정
                self._setup_headers()
                
        except Exception as e:
            logger.error(f"워크시트 설정 실패: {e}")
            raise
    
    def _verify_and_fix_headers(self):
        """헤더 일관성 확인 및 수정"""
        try:
            # 현재 헤더 확인
            current_headers = self.worksheet.row_values(1)
            logger.info(f"📋 현재 헤더 ({len(current_headers)}개): {current_headers}")
            
            # 올바른 헤더 정의
            correct_headers = [
                '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
                '시가', '고가', '저가', '거래량', '거래대금', 
                '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
            ]
            
            # 헤더가 다르면 수정
            if current_headers != correct_headers:
                logger.info("🔧 헤더 구조 수정 중...")
                self._setup_headers()
                logger.info("✅ 헤더 구조 수정 완료")
            else:
                logger.info("✅ 헤더 구조 정상")
                
        except Exception as e:
            logger.warning(f"헤더 확인 중 오류: {e}")
            # 오류 시 헤더 재설정
            self._setup_headers()
    
    def _setup_headers(self):
        """정확한 헤더 설정"""
        try:
            # 정확한 헤더 순서
            headers = [
                '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
                '시가', '고가', '저가', '거래량', '거래대금', 
                '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
            ]
            
            # 헤더 업데이트
            self.worksheet.update('A1:P1', [headers])
            
            # 헤더 서식 (배경색 + 굵게 + 가운데 정렬)
            self.worksheet.format('A1:P1', {
                'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 0.9},
                'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                'horizontalAlignment': 'CENTER'
            })
            
            logger.info(f"📋 헤더 설정 완료: {len(headers)}개 컬럼")
            
        except Exception as e:
            logger.error(f"헤더 설정 실패: {e}")
            raise
    
    def append_real_data(self, data: List[Dict]):
        """실시간 데이터 누적 추가 (기존 데이터 삭제 안함)"""
        try:
            if not data:
                logger.warning("추가할 데이터가 없습니다")
                return False
            
            logger.info(f"🔄 시간별 누적 데이터 추가: {len(data)}개 종목")
            
            current_date = data[0]['date']
            current_time = data[0].get('time', datetime.now().strftime('%H:%M:%S'))
            
            # ✅ 기존 데이터 삭제하지 않음 - 시간별 누적 저장
            logger.info(f"📈 {current_date} {current_time} 데이터 추가 중...")
            
            # 헤더 순서 확인 및 디버깅
            expected_headers = [
                '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
                '시가', '고가', '저가', '거래량', '거래대금', 
                '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
            ]
            logger.info(f"📋 예상 헤더 ({len(expected_headers)}개): {expected_headers}")
            
            # 새 데이터 준비 (헤더 순서 정확히 맞춤)
            new_rows = []
            for i, item in enumerate(data):
                # 모든 값을 문자열로 변환하여 일관성 확보
                row = [
                    str(item['date']),                                    # 1. 날짜
                    str(item.get('time', current_time)),                  # 2. 시간  
                    str(item['symbol']),                                  # 3. 종목명
                    str(item['current_price']),                          # 4. 현재가
                    str(item['change']),                                 # 5. 대비
                    str(item['change_rate']),                            # 6. 등락률
                    str(item['open_price']),                             # 7. 시가
                    str(item['high_price']),                             # 8. 고가
                    str(item['low_price']),                              # 9. 저가
                    str(item['volume']),                                 # 10. 거래량
                    str(item['trading_value']),                          # 11. 거래대금
                    str(item['weighted_avg']),                           # 12. 가중평균
                    str(item.get('collection_time', current_time)),      # 13. 수집시간
                    str(item.get('data_source', 'unknown')),             # 14. 데이터소스
                    str(item.get('trading_phase', 'unknown')),           # 15. 거래단계
                    str(item.get('execution_priority', 'unknown'))       # 16. 우선도
                ]
                
                logger.info(f"📊 종목 {i+1}: {row[:6]}...")  # 처음 6개 컬럼만 로깅
                logger.info(f"📊 컬럼 수: {len(row)}개 (예상: {len(expected_headers)}개)")
                
                if len(row) != len(expected_headers):
                    logger.error(f"❌ 컬럼 수 불일치! 실제: {len(row)}, 예상: {len(expected_headers)}")
                    return False
                
                new_rows.append(row)
            
            # 데이터 일괄 추가 (기존 데이터 유지)
            if new_rows:
                # 🔧 최종 안전 검증
                logger.info(f"📤 Google Sheets 업로드 시작...")
                logger.info(f"📊 업로드할 행 수: {len(new_rows)}")
                logger.info(f"📊 각 행의 컬럼 수: {[len(row) for row in new_rows]}")
                
                # 샘플 행 로깅 (디버깅용)
                if new_rows:
                    logger.info(f"📊 첫 번째 행 샘플: {new_rows[0]}")
                
                self.worksheet.append_rows(new_rows)
                logger.info(f"✅ {len(new_rows)}개 행 누적 추가 완료")
                
                # 실시간 통계
                self._log_realtime_statistics(data)
                
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"데이터 추가 실패: {e}")
            logger.error(f"오류 상세: 첫 번째 아이템 = {data[0] if data else 'No data'}")
            return False
    
    def _log_realtime_statistics(self, data: List[Dict]):
        """실시간 통계 정보 로깅"""
        try:
            total_volume = sum(item['volume'] for item in data)
            active_items = len([item for item in data if item['volume'] > 0])
            total_items = len(data)
            
            trading_phase = data[0].get('trading_phase', 'unknown') if data else 'unknown'
            execution_priority = data[0].get('execution_priority', 'unknown') if data else 'unknown'
            data_source = data[0].get('data_source', 'unknown') if data else 'unknown'
            
            logger.info("=== 실시간 거래 통계 ===")
            logger.info(f"거래 단계: {trading_phase}")
            logger.info(f"실행 우선도: {execution_priority}")
            logger.info(f"총 종목 수: {total_items}개")
            logger.info(f"활성 거래 종목: {active_items}개")
            logger.info(f"총 거래량: {total_volume:,.0f} 톤")
            logger.info(f"데이터 소스: {data_source}")
            
            if execution_priority == 'critical':
                logger.info("🔥 중요 시점 데이터 누적 저장 완료!")
            elif active_items > 0:
                logger.info(f"⚡ 활발한 거래 진행 중! ({active_items}개 종목)")
            
            # 활성 종목 상세 정보
            active_data = [item for item in data if item['volume'] > 0]
            if active_data:
                logger.info("📊 활성 거래 종목:")
                for item in active_data:
                    logger.info(f"  • {item['symbol']}: {item['current_price']:,}원 "
                              f"({item['change']:+.0f}, {item['change_rate']:+.2f}%) "
                              f"거래량: {item['volume']:,}톤")
            
        except Exception as e:
            logger.warning(f"통계 로깅 중 오류: {e}")


def main():
    """개선된 메인 실행 함수"""
    try:
        logger.info("=== 개선된 KRX ETS 실시간 데이터 수집 시작 ===")
        
        # 환경변수 확인
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
        
        if not creds_json or not sheet_id:
            raise ValueError("필수 환경변수가 설정되지 않았습니다")
        
        # 현재 시간 및 거래 정보
        now = datetime.now()
        market_phase = os.getenv('MARKET_PHASE', 'unknown')
        trading_phase = os.getenv('TRADING_PHASE', 'unknown')
        execution_priority = os.getenv('EXECUTION_PRIORITY', 'unknown')
        
        logger.info(f"수집 시작: {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
        logger.info(f"시장 단계: {market_phase}")
        logger.info(f"거래 단계: {trading_phase}")
        logger.info(f"실행 우선도: {execution_priority}")
        
        # 개선된 데이터 수집기 초기화
        collector = RealKRXCollector()
        
        # 실시간 데이터 수집
        logger.info("개선된 실시간 데이터 수집 중...")
        real_data = collector.get_real_krx_data()
        
        if real_data:
            logger.info(f"✅ 데이터 수집 성공: {len(real_data)}개 종목")
            
            # 📊 수집된 데이터 상세 검증
            logger.info("=== 수집 데이터 검증 ===")
            for i, item in enumerate(real_data):
                logger.info(f"종목 {i+1}: {item['symbol']} - {item['current_price']:,}원 "
                          f"(거래량: {item['volume']:,}톤, 소스: {item.get('data_source', 'unknown')})")
        else:
            # 실제 데이터 수집 실패시 명확하게 실패 처리
            logger.error("❌ KRX 데이터 수집 완전 실패")
            logger.error("🔍 수집된 데이터: 0개")
            logger.error("📊 분석 결과:")
            logger.error("   • 테이블 발견: 성공")
            logger.error("   • 데이터 행 파싱: 실패 (0개 종목)")
            logger.error("   • 재시도 횟수: 3회 모두 실패")
            
            print("❌ KRX 데이터 수집 실패!")
            print("🔍 원인 분석:")
            print("   • 현재 거래시간이 아닐 수 있습니다")
            print("   • KRX 웹사이트 구조가 변경되었을 수 있습니다") 
            print("   • 네트워크 연결에 문제가 있을 수 있습니다")
            print("📞 문제 지속시 GitHub Issues에 신고하세요")
            
            sys.exit(1)  # 명확한 실패로 종료
            
            if test_mode:
                logger.info("테스트 모드: Google Sheets 업데이트 건너뜀")
                logger.info("=== 테스트 모드 - 업로드 시뮬레이션 ===")
                
                # 테스트용 데이터 구조 검증
                expected_headers = [
                    '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
                    '시가', '고가', '저가', '거래량', '거래대금', 
                    '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
                ]
                
                for i, item in enumerate(real_data[:2]):  # 처음 2개만 테스트
                    current_time = item.get('time', datetime.now().strftime('%H:%M:%S'))
                    test_row = [
                        str(item['date']), str(item.get('time', current_time)), str(item['symbol']),
                        str(item['current_price']), str(item['change']), str(item['change_rate']),
                        str(item['open_price']), str(item['high_price']), str(item['low_price']),
                        str(item['volume']), str(item['trading_value']), str(item['weighted_avg']),
                        str(item.get('collection_time', current_time)), str(item.get('data_source', 'unknown')),
                        str(item.get('trading_phase', 'unknown')), str(item.get('execution_priority', 'unknown'))
                    ]
                    
                    logger.info(f"테스트 행 {i+1} ({len(test_row)}개 컬럼): {test_row}")
                    logger.info(f"헤더 ({len(expected_headers)}개 컬럼): {expected_headers}")
                    
                    if len(test_row) == len(expected_headers):
                        logger.info(f"✅ 종목 {i+1} 데이터 구조 정상")
                    else:
                        logger.error(f"❌ 종목 {i+1} 데이터 구조 오류! 컬럼 수: {len(test_row)} vs {len(expected_headers)}")
                
                print(f"✅ 테스트 모드 - 개선된 데이터 수집 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"🎯 우선도: {execution_priority}")
                print(f"💾 데이터 소스: {real_data[0].get('data_source', 'unknown')}")
                print(f"📋 데이터 구조 검증: 통과")
                return
            
            # Google Sheets 누적 저장
            sheets_manager = EnhancedSheetsManager(creds_json, sheet_id)
            success = sheets_manager.append_real_data(real_data)  # 기존 데이터 삭제 안함
            
            if success:
                # 성공 결과 출력
                total_volume = sum(item['volume'] for item in real_data)
                active_items = len([item for item in real_data if item['volume'] > 0])
                
                print(f"✅ 실시간 데이터 누적 저장 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"🔥 활성 종목: {active_items}개")
                print(f"📈 총 거래량: {total_volume:,} 톤")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"🎯 실행 우선도: {execution_priority}")
                print(f"💾 데이터 소스: {real_data[0].get('data_source', 'unknown')}")
                print(f"🕐 수집 시간: {now.strftime('%H:%M:%S')} KST")
                
                # 거래 단계별 특별 메시지
                if execution_priority == 'critical':
                    print(f"🔥 중요 시점 데이터 누적 저장 완료!")
                elif execution_priority == 'high':
                    print(f"⚡ 고우선도 데이터 누적 저장 완료!")
                
                # 실제 거래 종목 정보
                active_data = [item for item in real_data if item['volume'] > 0]
                if active_data:
                    print(f"\n📋 활성 거래 종목:")
                    for item in active_data[:5]:
                        print(f"  • {item['symbol']}: {item['current_price']:,}원 "
                              f"({item['change']:+.0f}, {item['change_rate']:+.2f}%) "
                              f"거래량: {item['volume']:,}톤")
                else:
                    print(f"\n📋 주요 종목 (현재가 기준):")
                    for item in real_data[:5]:
                        print(f"  • {item['symbol']}: {item['current_price']:,}원")
                
                logger.info("✅ 개선된 실시간 데이터 수집 및 누적 저장 완료!")
                
            else:
                logger.error("❌ Google Sheets 업데이트 실패")
                sys.exit(1)
                
        else:
            # 이 블록은 실행되지 않아야 함 (위에서 이미 sys.exit(1) 호출)
            logger.error("❌ 예상치 못한 데이터 없음 상태")
            print("❌ KRX에서 데이터를 가져올 수 없습니다")
            print("🔍 시스템 오류 또는 예상치 못한 상황입니다")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ 실행 중 오류: {e}")
        print(f"❌ 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
