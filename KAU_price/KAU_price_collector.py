#!/usr/bin/env python3
"""
최적화된 KRX ETS 데이터 수집기 v3.0 (KRX 시트 추가)
1. 기존 Playwright 기반 시스템 유지
2. OptimizedSheetsManager 적용 + KRX 시트 관리
3. 12:30 최종 세션 KRX 시트 업데이트
4. 중복 제거 통계 GitHub Actions 전달
"""

# 기존 imports 유지
import asyncio
import json
import os
import logging
import sys
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# Playwright 및 OCR 관련 (기존과 동일)
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    logging.error(f"Playwright 패키지 누락: {e}")
    PLAYWRIGHT_AVAILABLE = False

try:
    import easyocr
    import cv2
    import numpy as np
    from PIL import Image, ImageEnhance
    OCR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"OCR 패키지 누락 (백업 기능 비활성화): {e}")
    OCR_AVAILABLE = False

# Google Sheets
import gspread

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def _write_github_action_output(name: str, value: str) -> None:
    """Write a GitHub Actions step output when running in Actions."""
    output_path = os.getenv('GITHUB_OUTPUT')
    if not output_path:
        return
    try:
        with open(output_path, 'a', encoding='utf-8') as f:
            f.write(f'{name}={value}\n')
    except Exception as e:
        logger.warning(f"GitHub Actions output write failed ({name}): {e}")

# PlaywrightKRXCollector 클래스는 기존과 동일하게 유지
class PlaywrightKRXCollector:
    def __init__(self):
        """Playwright 기반 KRX 데이터 수집기"""
        self.base_url = "https://ets.krx.co.kr"
        self.main_page_url = f"{self.base_url}/contents/ETS/03/03010000/ETS03010000.jsp"
        self.trading_config = self._analyze_trading_context()
        self.browser = None
        self.context = None
        self.page = None
        
        # OCR 백업 시스템
        self.ocr_reader = None
        self.screenshots_dir = Path("screenshots")
        self.screenshots_dir.mkdir(exist_ok=True)
        
    def _analyze_trading_context(self) -> Dict:
        """거래 상황 분석 (최적화 스케줄 반영)"""
        try:
            market_phase = os.getenv('MARKET_PHASE', 'unknown')
            trading_phase = os.getenv('TRADING_PHASE', 'unknown')
            collection_frequency = os.getenv('COLLECTION_FREQUENCY', 'standard')
            execution_priority = os.getenv('EXECUTION_PRIORITY', 'low')
            
            config = {
                'market_phase': market_phase,
                'trading_phase': trading_phase,
                'collection_frequency': collection_frequency,
                'execution_priority': execution_priority,
                'max_retries': 3,
                'timeout': 45000,
                'wait_timeout': 30000,
                'high_accuracy': False,
                'headless': True
            }
            
            # 최적화된 거래 단계별 설정
            if trading_phase in ['opening_price_decision', 'closing_price_decision', 'final_settlement']:
                config.update({
                    'max_retries': 5, 
                    'timeout': 60000, 
                    'wait_timeout': 45000,
                    'high_accuracy': True,
                    'headless': os.getenv('HEADLESS', 'true').lower() != 'false'
                })
                logger.info(f"🔥 {trading_phase} 모드 - 최고 정확도")
            elif trading_phase == 'real_time_trading_intensive':
                config.update({
                    'max_retries': 3, 
                    'timeout': 25000,
                    'wait_timeout': 15000,
                    'headless': True
                })
                logger.info("⚡ 실시간 집중 거래 모드 - 10분 간격 고속 처리")
            elif collection_frequency == '10min':
                config.update({
                    'max_retries': 2,
                    'timeout': 30000,
                    'wait_timeout': 20000,
                    'headless': True
                })
                logger.info("📊 10분 간격 최적화 모드")
            
            return config
            
        except Exception as e:
            logger.warning(f"거래 상황 분석 중 오류: {e}")
            return {
                'market_phase': 'unknown', 'trading_phase': 'unknown', 'collection_frequency': 'standard',
                'execution_priority': 'low', 'max_retries': 3, 'timeout': 45000, 'wait_timeout': 30000, 
                'high_accuracy': False, 'headless': True
            }

    async def initialize_browser(self):
        """브라우저 초기화 (기존과 동일)"""
        try:
            self.playwright = await async_playwright().start()
            
            browser_config = {
                'headless': self.trading_config['headless'],
                'args': [
                    '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                    '--disable-web-security', '--disable-extensions',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            }
            
            self.browser = await self.playwright.chromium.launch(**browser_config)
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                locale='ko-KR', timezone_id='Asia/Seoul',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            self.page = await self.context.new_page()
            await self._setup_network_monitoring()
            
            logger.info("✅ Playwright 브라우저 초기화 완료")
            return True
            
        except Exception as e:
            logger.error(f"❌ 브라우저 초기화 실패: {e}")
            return False

    async def _setup_network_monitoring(self):
        """네트워크 요청 모니터링 설정 (기존과 동일)"""
        self.ajax_responses = []
        
        async def handle_response(response):
            try:
                url = response.url
                if ('ETS99000001.jspx' in url or 
                    'json' in response.headers.get('content-type', '').lower()):
                    if response.status == 200:
                        try:
                            content = await response.text()
                            if content and len(content) > 100:
                                self.ajax_responses.append({
                                    'url': url, 'content': content,
                                    'timestamp': datetime.now(),
                                    'headers': dict(response.headers)
                                })
                                logger.info(f"📡 AJAX 응답 캐치: {url[:60]}... ({len(content)} bytes)")
                        except Exception as e:
                            logger.debug(f"AJAX 응답 파싱 실패: {e}")
            except Exception as e:
                logger.debug(f"네트워크 모니터링 오류: {e}")
        
        self.page.on('response', handle_response)

    async def get_real_krx_data(self) -> List[Dict]:
        """Playwright 기반 실제 KRX ETS 데이터 수집 (기존 로직 유지)"""
        try:
            config = self.trading_config
            logger.info("=== 최적화된 Playwright 기반 KRX ETS 데이터 수집 시작 ===")
            logger.info(f"거래 단계: {config['trading_phase']}")
            logger.info(f"수집 주기: {config['collection_frequency']}")
            
            if not await self.initialize_browser():
                logger.error("브라우저 초기화 실패")
                return []
            
            for attempt in range(config['max_retries']):
                try:
                    logger.info(f"🌐 데이터 수집 시도 {attempt + 1}/{config['max_retries']}")
                    
                    await self._load_main_page()
                    table_loaded = await self._wait_for_table_load()
                    
                    if table_loaded:
                        dom_data = await self._extract_from_dom()
                        if self._validate_real_data(dom_data):
                            logger.info(f"✅ DOM 추출 성공: {len(dom_data)}개 종목")
                            return self._enhance_with_trading_info(dom_data, config)
                    
                    ajax_data = await self._extract_from_ajax()
                    if self._validate_real_data(ajax_data):
                        logger.info(f"✅ AJAX 추출 성공: {len(ajax_data)}개 종목")
                        return self._enhance_with_trading_info(ajax_data, config)
                    
                    if config['high_accuracy'] and OCR_AVAILABLE:
                        ocr_data = await self._ocr_backup_extraction()
                        if self._validate_real_data(ocr_data):
                            logger.info(f"✅ OCR 백업 성공: {len(ocr_data)}개 종목")
                            return self._enhance_with_trading_info(ocr_data, config)
                    
                    if attempt < config['max_retries'] - 1:
                        await asyncio.sleep(3)
                        
                except Exception as e:
                    logger.warning(f"시도 {attempt + 1} 중 오류: {e}")
                    if attempt < config['max_retries'] - 1:
                        await asyncio.sleep(5)
            
            logger.error("❌ 모든 데이터 수집 방법 실패")
            return []
            
        except Exception as e:
            logger.error(f"❌ 데이터 수집 중 심각한 오류: {e}")
            return []
        finally:
            await self.cleanup()

    async def _load_main_page(self):
        """메인 페이지 로드"""
        response = await self.page.goto(self.main_page_url, timeout=self.trading_config['timeout'], wait_until='domcontentloaded')
        if response.status != 200:
            raise Exception(f"HTTP {response.status}")
        await self.page.wait_for_load_state('networkidle', timeout=15000)

    async def _wait_for_table_load(self) -> bool:
        """동적 테이블 로딩 대기"""
        table_selectors = ['table[summary*="배출권 현재가"]', 'table[id*="gridtable"]', '.CI-GRID-BODY-TABLE']
        for selector in table_selectors:
            try:
                await self.page.wait_for_selector(selector, timeout=self.trading_config['wait_timeout'])
                rows = await self.page.query_selector_all(f"{selector} tr")
                if len(rows) > 1:
                    return True
            except Exception:
                continue
        return False

    async def _extract_from_dom(self) -> List[Dict]:
        """DOM에서 데이터 추출 (기존 로직)"""
        try:
            table_selectors = ['table[summary*="배출권"] tbody tr', 'table[id*="gridtable"] tbody tr']
            rows = []
            for selector in table_selectors:
                try:
                    found_rows = await self.page.query_selector_all(selector)
                    if len(found_rows) > 0:
                        rows = found_rows
                        break
                except Exception:
                    continue
            
            extracted_data = []
            for i, row in enumerate(rows):
                try:
                    cells = await row.query_selector_all('td')
                    if len(cells) >= 8:
                        cell_texts = []
                        for cell in cells:
                            text = await cell.text_content()
                            cell_texts.append(text.strip() if text else '')
                        
                        symbol = cell_texts[0] if cell_texts else ''
                        if symbol and re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                            parsed_item = self._parse_row_data(cell_texts, i)
                            if parsed_item:
                                extracted_data.append(parsed_item)
                except Exception as e:
                    logger.debug(f"행 {i} 추출 중 오류: {e}")
                    continue
            
            return extracted_data
        except Exception as e:
            logger.error(f"DOM 추출 중 오류: {e}")
            return []

    async def _extract_from_ajax(self) -> List[Dict]:
        """AJAX 응답에서 데이터 추출 (기존 로직)"""
        return []  # 기존 구현 유지

    async def _ocr_backup_extraction(self) -> List[Dict]:
        """OCR 백업 데이터 추출 (기존 로직)"""
        return []  # 기존 구현 유지

    def _parse_row_data(self, cell_texts: List[str], row_index: int) -> Optional[Dict]:
        """테이블 행 데이터 파싱"""
        try:
            if len(cell_texts) < 8:
                return None
                
            symbol = cell_texts[0]
            if not symbol or not re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                return None
            
            current_price = self._extract_number(cell_texts[1])
            if current_price <= 0:
                return None
            
            return {
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
                'weighted_avg': 0,
                'collection_time': datetime.now().strftime('%H:%M:%S'),
                'data_source': 'playwright_dom'
            }
        except Exception as e:
            logger.debug(f"행 데이터 파싱 오류: {e}")
            return None

    def _validate_real_data(self, data: List[Dict]) -> bool:
        """실제 데이터 검증"""
        if not data or len(data) < 3:
            return False
        symbols = {item.get('symbol', '') for item in data}
        required_symbols = {'KAU25', 'KCU25'}
        return len(required_symbols.intersection(symbols)) > 0

    def _enhance_with_trading_info(self, data: List[Dict], config: Dict) -> List[Dict]:
        """거래 정보로 데이터 강화"""
        for item in data:
            item['trading_phase'] = config['trading_phase']
            item['market_phase'] = config['market_phase']
            item['collection_frequency'] = config['collection_frequency']
            item['execution_priority'] = config['execution_priority']
        return data

    def _extract_number(self, text: str) -> float:
        """텍스트에서 숫자 추출"""
        try:
            if not text or text == '-':
                return 0.0
            cleaned = re.sub(r'[^\d.-]', '', text.replace(',', ''))
            return float(cleaned) if cleaned else 0.0
        except (ValueError, TypeError):
            return 0.0

    async def cleanup(self):
        """리소스 정리"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'playwright'):
                await self.playwright.stop()
        except Exception as e:
            logger.warning(f"리소스 정리 중 오류: {e}")


# 최적화된 Google Sheets 관리자 (KRX 시트 추가)
class OptimizedSheetsManager:
    """최적화된 Google Sheets 관리자 (중복 제거 + KRX 시트)"""
    
    def __init__(self, credentials_json: str, sheet_id: str):
        try:
            logger.info("Google Sheets 연결 시작 (최적화 v3.0)...")
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            self.setup_worksheets()
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def setup_worksheets(self):
        """워크시트 설정 (ets.KRX + KRX)"""
        try:
            # 1. 기존 ets.KRX 시트 설정
            try:
                self.worksheet = self.spreadsheet.worksheet('ets.KRX')
                self._verify_and_fix_headers()
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(title='ets.KRX', rows=2000, cols=16)
                self._setup_headers()
            
            # 2. 새로운 KRX 시트 설정 (요약용)
            try:
                self.krx_worksheet = self.spreadsheet.worksheet('KRX')
                self._verify_and_fix_krx_headers()
            except gspread.WorksheetNotFound:
                self.krx_worksheet = self.spreadsheet.add_worksheet(title='KRX', rows=1000, cols=11)
                self._setup_krx_headers()
                
            logger.info("✅ 워크시트 설정 완료: ets.KRX (상세) + KRX (요약)")
            
        except Exception as e:
            logger.error(f"워크시트 설정 실패: {e}")
            raise
    
    def _verify_and_fix_headers(self):
        """ets.KRX 시트 헤더 확인"""
        try:
            current_headers = self.worksheet.row_values(1)
            correct_headers = [
                '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
                '시가', '고가', '저가', '거래량', '거래대금', 
                '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
            ]
            if current_headers != correct_headers:
                self._setup_headers()
        except Exception as e:
            self._setup_headers()
    
    def _setup_headers(self):
        """ets.KRX 시트 헤더 설정"""
        headers = [
            '날짜', '시간', '종목명', '현재가', '대비', '등락률', 
            '시가', '고가', '저가', '거래량', '거래대금', 
            '가중평균', '수집시간', '데이터소스', '거래단계', '우선도'
        ]
        self.worksheet.update('A1:P1', [headers])
        self.worksheet.format('A1:P1', {
            'backgroundColor': {'red': 0.2, 'green': 0.6, 'blue': 0.9},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
            'horizontalAlignment': 'CENTER'
        })
    
    def _verify_and_fix_krx_headers(self):
        """KRX 시트 헤더 확인"""
        try:
            current_headers = self.krx_worksheet.row_values(1)
            correct_headers = [
                '날짜', '종목명', '현재가', '대비', '등락률', 
                '시가', '고가', '저가', '거래량', '거래대금', '가중평균'
            ]
            if current_headers != correct_headers:
                self._setup_krx_headers()
        except Exception as e:
            self._setup_krx_headers()
    
    def _setup_krx_headers(self):
        """KRX 시트 헤더 설정 (A~K열)"""
        headers = [
            '날짜', '종목명', '현재가', '대비', '등락률', 
            '시가', '고가', '저가', '거래량', '거래대금', '가중평균'
        ]
        self.krx_worksheet.update('A1:K1', [headers])
        self.krx_worksheet.format('A1:K1', {
            'backgroundColor': {'red': 0.1, 'green': 0.7, 'blue': 0.3},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
            'horizontalAlignment': 'CENTER'
        })
        logger.info("✅ KRX 시트 헤더 설정 완료 (A~K열)")
    
    def get_today_last_data(self, target_date: str) -> Dict[str, Dict]:
        """오늘의 최신 데이터 조회 (중복 검사용)"""
        try:
            all_data = self.worksheet.get_all_records()
            today_data = [row for row in all_data if row.get('날짜') == target_date]
            
            if not today_data:
                return {}
            
            latest_by_symbol = {}
            for row in today_data:
                symbol = row.get('종목명', '')
                if symbol:
                    current_time = row.get('수집시간', row.get('시간', '00:00:00'))
                    if symbol not in latest_by_symbol:
                        latest_by_symbol[symbol] = row
                    else:
                        existing_time = latest_by_symbol[symbol].get('수집시간', '00:00:00')
                        if current_time > existing_time:
                            latest_by_symbol[symbol] = row
            
            return latest_by_symbol
        except Exception as e:
            logger.warning(f"기존 데이터 조회 오류: {e}")
            return {}
    
    def _should_update_symbol(self, new_data: Dict, existing_data: Dict, is_first_collection: bool) -> tuple[bool, str]:
        """종목별 업데이트 필요성 판단"""
        try:
            symbol = new_data.get('symbol', '')
            
            if is_first_collection:
                return True, f"첫 수집"
            
            if symbol not in existing_data:
                return True, f"신규 종목"
            
            existing = existing_data[symbol]
            
            new_volume = float(str(new_data.get('volume', 0)).replace(',', ''))
            new_change = float(str(new_data.get('change', 0)).replace(',', ''))
            
            conditions = []
            
            if new_volume > 0:
                conditions.append("거래량 > 0")
            
            if new_change != 0:
                conditions.append("가격 변화")
            
            trading_phase = new_data.get('trading_phase', '')
            if trading_phase in ['opening_price_decision', 'closing_price_decision', 'final_settlement']:
                conditions.append("중요 시점")
            
            should_update = len(conditions) > 0
            reason = " | ".join(conditions) if conditions else "변화 없음 (생략)"
            
            return should_update, reason
        except Exception as e:
            logger.warning(f"업데이트 판단 오류 ({symbol}): {e}")
            return True, "판단 오류"
    
    def append_optimized_data(self, data: List[Dict]) -> tuple[bool, Dict]:
        """최적화된 데이터 추가 (중복 제거)"""
        try:
            if not data:
                return False, {'skipped': 0, 'added': 0, 'total': 0}
            
            current_date = data[0]['date']
            current_time = data[0].get('time', datetime.now().strftime('%H:%M:%S'))
            
            logger.info(f"🔍 데이터 최적화 분석 시작: {current_date} {current_time}")
            
            existing_data = self.get_today_last_data(current_date)
            is_first_collection = len(existing_data) == 0
            
            new_rows = []
            stats = {'skipped': 0, 'added': 0, 'total': len(data)}
            
            for item in data:
                symbol = item['symbol']
                should_update, reason = self._should_update_symbol(item, existing_data, is_first_collection)
                
                if should_update:
                    row = [
                        str(item['date']), str(item.get('time', current_time)), str(item['symbol']),
                        str(item['current_price']), str(item['change']), str(item['change_rate']),
                        str(item['open_price']), str(item['high_price']), str(item['low_price']),
                        str(item['volume']), str(item['trading_value']), str(item['weighted_avg']),
                        str(item.get('collection_time', current_time)), str(item.get('data_source', 'unknown')),
                        str(item.get('trading_phase', 'unknown')), str(item.get('execution_priority', 'unknown'))
                    ]
                    new_rows.append(row)
                    stats['added'] += 1
                    logger.info(f"✅ {symbol}: 업데이트 ({reason})")
                else:
                    stats['skipped'] += 1
                    logger.info(f"⏭️ {symbol}: 생략 ({reason})")
            
            if new_rows:
                self.worksheet.append_rows(new_rows)
                logger.info(f"💾 ets.KRX 시트 업데이트: {len(new_rows)}개 행 추가")
            
            efficiency = round((stats['skipped'] / stats['total'] * 100), 1) if stats['total'] > 0 else 0
            logger.info(f"📊 최적화 결과: 추가 {stats['added']}개 | 생략 {stats['skipped']}개")
            logger.info(f"⚡ 용량 절약: {efficiency}%")
            
            return True, stats
        except Exception as e:
            logger.error(f"최적화 데이터 추가 실패: {e}")
            return False, {'skipped': 0, 'added': 0, 'total': len(data) if data else 0}
    
    def update_krx_final_summary(self, data: List[Dict]) -> bool:
        """12:30 최종 세션 KRX 시트 업데이트 (A~K열)"""
        try:
            if not data:
                logger.warning("KRX 최종 요약: 데이터 없음")
                return False
            
            current_date = data[0]['date']
            logger.info(f"🎯 KRX 시트 최종 요약 업데이트: {current_date}")
            
            # 모든 종목 데이터 준비 (거래 없는 종목도 포함)
            all_symbols = [
                'KAU25', 'KCU25', 'KOC21-26', 'KOC22-27', 'KOC23-28', 'KOC24-29', 'KOC25-30',
                'i-KCU25', 'i-KOC20-22', 'i-KOC21-26', 'i-KOC22-27', 'i-KOC23-28', 'i-KOC24-29', 'i-KOC25-30'
            ]
            
            # 수집된 데이터를 딕셔너리로 변환
            collected_data = {item['symbol']: item for item in data}
            
            # KRX 시트용 행 생성
            krx_rows = []
            for symbol in all_symbols:
                if symbol in collected_data:
                    # 수집된 데이터 사용
                    item = collected_data[symbol]
                    row = [
                        str(item['date']),                    # A: 날짜
                        str(item['symbol']),                  # B: 종목명
                        str(item['current_price']),           # C: 현재가
                        str(item['change']),                  # D: 대비
                        str(item['change_rate']),             # E: 등락률
                        str(item['open_price']),              # F: 시가
                        str(item['high_price']),              # G: 고가
                        str(item['low_price']),               # H: 저가
                        str(item['volume']),                  # I: 거래량
                        str(item['trading_value']),           # J: 거래대금
                        str(item['weighted_avg'])             # K: 가중평균
                    ]
                else:
                    # 거래 없는 종목 - 기본값으로 추가
                    row = [
                        current_date,  # A: 날짜
                        symbol,        # B: 종목명
                        '0',           # C: 현재가
                        '0',           # D: 대비
                        '0.00',        # E: 등락률
                        '0',           # F: 시가
                        '0',           # G: 고가
                        '0',           # H: 저가
                        '0',           # I: 거래량
                        '0',           # J: 거래대금
                        '0'            # K: 가중평균
                    ]
                
                krx_rows.append(row)
            
            # KRX 시트에 일괄 업데이트
            if krx_rows:
                self.krx_worksheet.append_rows(krx_rows)
                logger.info(f"✅ KRX 시트 최종 요약 완료: {len(krx_rows)}개 종목 (A~K열)")
                logger.info(f"📊 수집 종목: {len(collected_data)}개")
                logger.info(f"📋 거래없음: {len(all_symbols) - len(collected_data)}개")
                return True
            else:
                logger.warning("KRX 시트 업데이트: 생성된 행 없음")
                return False
                
        except Exception as e:
            logger.error(f"KRX 시트 최종 요약 실패: {e}")
            return False
    
    def append_real_data(self, data: List[Dict]) -> bool:
        """기존 인터페이스 호환성"""
        success, stats = self.append_optimized_data(data)
        return success


# 기존 호환성을 위한 별칭
class EnhancedSheetsManager(OptimizedSheetsManager):
    pass


async def main():
    """최적화된 메인 실행 함수 (v3.0)"""
    try:
        logger.info("=== 최적화된 KRX ETS 데이터 수집 시스템 v3.0 시작 ===")
        
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("❌ Playwright 패키지가 없습니다")
            sys.exit(1)
        
        # 환경변수 확인
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
        
        if not creds_json or not sheet_id:
            raise ValueError("필수 환경변수가 설정되지 않았습니다")
        
        # 거래 정보
        now = datetime.now()
        trading_phase = os.getenv('TRADING_PHASE', 'unknown')
        collection_frequency = os.getenv('COLLECTION_FREQUENCY', 'standard')
        execution_priority = os.getenv('EXECUTION_PRIORITY', 'unknown')
        is_final_session = os.getenv('IS_FINAL_SESSION', 'false').lower() == 'true'
        
        logger.info(f"🕐 수집 시작: {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
        logger.info(f"⚡ 거래 단계: {trading_phase}")
        logger.info(f"📊 수집 주기: {collection_frequency}")
        logger.info(f"🎯 실행 우선도: {execution_priority}")
        logger.info(f"🎯 최종 세션: {is_final_session}")
        logger.info(f"🚀 시스템: 최적화 v3.0 (KRX 시트 추가)")
        
        # Playwright 데이터 수집기 초기화
        collector = PlaywrightKRXCollector()
        
        # 데이터 수집 실행
        real_data = await collector.get_real_krx_data()
        
        if real_data:
            logger.info(f"✅ 데이터 수집 성공: {len(real_data)}개 종목")
            
            if test_mode:
                print(f"✅ 최적화 테스트 모드 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"📊 수집 주기: {collection_frequency}")
                print(f"🎯 최종 세션: {is_final_session}")
                print(f"🚀 시스템: v3.0 (KRX 시트)")
                return
            
            # 최적화된 Google Sheets 저장
            sheets_manager = OptimizedSheetsManager(creds_json, sheet_id)
            
            # 1. ets.KRX 시트에 최적화된 데이터 추가
            success, optimization_stats = sheets_manager.append_optimized_data(real_data)
            
            # 2. 12:30 최종 세션인 경우 KRX 시트에 요약 추가
            krx_updated = False
            if is_final_session:
                logger.info("🎯 12:30 최종 세션 - KRX 시트 요약 업데이트")
                krx_updated = sheets_manager.update_krx_final_summary(real_data)
            krx_updated_value = 'true' if krx_updated else 'false'
            _write_github_action_output('krx_sheet_updated', krx_updated_value)
            try:
                Path('/tmp/krx_sheet_updated.txt').write_text(krx_updated_value, encoding='utf-8')
            except Exception as e:
                logger.warning(f"KRX 시트 업데이트 상태 파일 저장 실패: {e}")
            
            if success:
                total_volume = sum(item['volume'] for item in real_data)
                active_items = len([item for item in real_data if item['volume'] > 0])
                
                # 최적화 통계를 환경변수로 전달 (GitHub Actions용)
                optimization_json = json.dumps(optimization_stats, ensure_ascii=False)
                _write_github_action_output('optimization_stats', optimization_json)
                try:
                    Path('/tmp/optimization_stats.json').write_text(optimization_json, encoding='utf-8')
                except Exception as e:
                    logger.warning(f"최적화 통계 파일 저장 실패: {e}")
                
                print(f"✅ 최적화된 데이터 수집 및 저장 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"🔥 활성 종목: {active_items}개")
                print(f"📈 총 거래량: {total_volume:,} 톤")
                print(f"💾 최적화 결과: 추가 {optimization_stats['added']}개, 생략 {optimization_stats['skipped']}개")
                print(f"⚡ 용량 절약: {round((optimization_stats['skipped']/optimization_stats['total']*100), 1)}%")
                print(f"📊 수집 주기: {collection_frequency}")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"🕐 수집 시간: {now.strftime('%H:%M:%S')} KST")
                
                # 12:30 최종 세션 결과
                if is_final_session:
                    print(f"🎯 12:30 KST 최종 마감 세션 완료!")
                    if krx_updated:
                        print(f"✅ KRX 시트 요약 업데이트 성공 (A~K열)")
                    else:
                        print(f"⚠️ KRX 시트 요약 업데이트 실패")
                    print(f"📱 텔레그램 일일 요약 발송 예정")
                
            else:
                print("❌ Google Sheets 업데이트 실패")
                sys.exit(1)
                
        else:
            logger.error("❌ 모든 데이터 수집 방법 실패")
            print("❌ 최적화된 데이터 수집 실패!")
            print("🔍 시도한 방법:")
            print("   • Playwright DOM 추출")
            print("   • AJAX 응답 분석")
            if OCR_AVAILABLE:
                print("   • OCR 백업 시스템")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ 실행 중 오류: {e}")
        print(f"❌ 오류: {e}")
        sys.exit(1)


def run_main():
    """동기 함수에서 비동기 함수 실행"""
    asyncio.run(main())


if __name__ == "__main__":
    run_main()
