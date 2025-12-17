#!/usr/bin/env python3
"""
최적화된 KRX ETS 데이터 수집기 v3.2.1 (0값 행 정리 추가)
1. 기존 Playwright 기반 시스템 유지
2. OptimizedSheetsManager 적용 + KRX 시트 관리
3. 12:30 최종 세션 KRX 시트 업데이트
4. 12:30 최종 세션 ets.KRX 시트 0값 행 정리 (신규)
5. 중복 제거 통계 GitHub Actions 전달
6. pytesseract 경량 OCR 백업 시스템
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

# Playwright 관련
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError as e:
    logging.error(f"Playwright 패키지 누락: {e}")
    PLAYWRIGHT_AVAILABLE = False

# pytesseract OCR 백업 (v3.2 경량화)
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError as e:
    logging.warning(f"OCR 패키지 누락 (백업 기능 비활성화): {e}")
    OCR_AVAILABLE = False

# Google Sheets
import gspread

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PlaywrightKRXCollector:
    """Playwright 기반 KRX 데이터 수집기"""
    
    def __init__(self):
        self.base_url = "https://ets.krx.co.kr"
        self.main_page_url = f"{self.base_url}/contents/ETS/03/03010000/ETS03010000.jsp"
        self.trading_config = self._analyze_trading_context()
        self.browser = None
        self.context = None
        self.page = None
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
            
            if trading_phase in ['opening_price_decision', 'closing_price_decision', 'final_settlement']:
                config.update({
                    'max_retries': 5, 
                    'timeout': 60000, 
                    'wait_timeout': 45000,
                    'high_accuracy': True,
                    'headless': False
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
        """브라우저 초기화"""
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
        """네트워크 요청 모니터링 설정"""
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
        """Playwright 기반 실제 KRX ETS 데이터 수집"""
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
        """DOM에서 데이터 추출"""
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
        """AJAX 응답에서 데이터 추출"""
        return []

    async def _ocr_backup_extraction(self) -> List[Dict]:
        """OCR 백업 데이터 추출 (pytesseract 경량 버전)"""
        try:
            if not OCR_AVAILABLE:
                return []
            
            logger.info("🔍 pytesseract OCR 백업 추출 시작...")
            screenshot_path = self.screenshots_dir / f"krx_table_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            await self.page.screenshot(path=str(screenshot_path), full_page=False)
            
            image = Image.open(screenshot_path)
            text = pytesseract.image_to_string(image, lang='kor+eng')
            logger.info(f"📸 OCR 텍스트 추출: {len(text)} 문자")
            
            return self._parse_ocr_text(text)
            
        except Exception as e:
            logger.warning(f"OCR 백업 추출 실패: {e}")
            return []
    
    def _parse_ocr_text(self, text: str) -> List[Dict]:
        """OCR 추출 텍스트에서 종목 데이터 파싱"""
        result_list = []
        try:
            lines = text.strip().split('\n')
            for line in lines:
                symbol_match = re.search(r'(KAU|KCU|KOC|i-KOC|i-KCU)\d+(-\d+)?', line)
                if symbol_match:
                    symbol = symbol_match.group()
                    numbers = re.findall(r'[\d,]+', line)
                    if len(numbers) >= 4:
                        try:
                            parsed_item = {
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'time': datetime.now().strftime('%H:%M:%S'),
                                'symbol': symbol,
                                'current_price': float(numbers[0].replace(',', '')),
                                'change': float(numbers[1].replace(',', '')) if len(numbers) > 1 else 0,
                                'change_rate': float(numbers[2].replace(',', '')) if len(numbers) > 2 else 0,
                                'open_price': 0, 'high_price': 0, 'low_price': 0,
                                'volume': float(numbers[3].replace(',', '')) if len(numbers) > 3 else 0,
                                'trading_value': float(numbers[4].replace(',', '')) if len(numbers) > 4 else 0,
                                'weighted_avg': 0,
                                'collection_time': datetime.now().strftime('%H:%M:%S'),
                                'data_source': 'playwright_ocr'
                            }
                            result_list.append(parsed_item)
                        except (ValueError, IndexError):
                            continue
        except Exception as e:
            logger.error(f"OCR 텍스트 파싱 오류: {e}")
        return result_list

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


class OptimizedSheetsManager:
    """최적화된 Google Sheets 관리자 (중복 제거 + KRX 시트 + 0값 행 정리)"""
    
    def __init__(self, credentials_json: str, sheet_id: str):
        try:
            logger.info("Google Sheets 연결 시작 (최적화 v3.2.1)...")
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
            try:
                self.worksheet = self.spreadsheet.worksheet('ets.KRX')
                self._verify_and_fix_headers()
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(title='ets.KRX', rows=2000, cols=16)
                self._setup_headers()
            
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
        except Exception:
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
        except Exception:
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
                return True, "첫 수집"
            if symbol not in existing_data:
                return True, "신규 종목"
            
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
            logger.warning(f"업데이트 판단 오류: {e}")
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
        """12:30 최종 세션 KRX 시트 업데이트 (A~K열) - 0값도 포함 (차트용)"""
        try:
            if not data:
                logger.warning("KRX 최종 요약: 데이터 없음")
                return False
            
            current_date = data[0]['date']
            logger.info(f"🎯 KRX 시트 최종 요약 업데이트: {current_date}")
            
            all_symbols = [
                'KAU25', 'KCU25', 'KOC21-26', 'KOC22-27', 'KOC23-28', 'KOC24-29', 'KOC25-30',
                'i-KCU25', 'i-KOC20-22', 'i-KOC21-26', 'i-KOC22-27', 'i-KOC23-28', 'i-KOC24-29', 'i-KOC25-30'
            ]
            
            collected_data = {item['symbol']: item for item in data}
            
            # KRX 시트는 0값도 포함 (차트 일관성)
            krx_rows = []
            for symbol in all_symbols:
                if symbol in collected_data:
                    item = collected_data[symbol]
                    row = [
                        str(item['date']), str(item['symbol']),
                        str(item['current_price']), str(item['change']), str(item['change_rate']),
                        str(item['open_price']), str(item['high_price']), str(item['low_price']),
                        str(item['volume']), str(item['trading_value']), str(item['weighted_avg'])
                    ]
                else:
                    row = [current_date, symbol, '0', '0', '0.00', '0', '0', '0', '0', '0', '0']
                krx_rows.append(row)
            
            if krx_rows:
                self.krx_worksheet.append_rows(krx_rows)
                logger.info(f"✅ KRX 시트 최종 요약 완료: {len(krx_rows)}개 종목 (0값 포함 - 차트용)")
                return True
            return False
                
        except Exception as e:
            logger.error(f"KRX 시트 최종 요약 실패: {e}")
            return False
    
    def cleanup_zero_rows(self, target_date: str) -> Dict:
        """
        해당 날짜의 의미없는 0값 행 삭제 (ets.KRX 시트 전용)
        
        삭제 기준: 대비, 등락률, 시가, 고가, 저가, 거래량, 거래대금이 모두 0인 행
        (현재가는 종가이므로 기준에서 제외 - 이미지 참고)
        
        주의: KRX 시트는 차트용이므로 0값도 유지 (이 메서드에서 처리하지 않음)
        """
        try:
            logger.info(f"🧹 ets.KRX 시트 0값 행 정리 시작: {target_date}")
            
            # 전체 데이터 조회
            all_data = self.worksheet.get_all_records()
            
            if not all_data:
                logger.warning("0값 행 정리: 데이터 없음")
                return {'target_date': target_date, 'deleted_count': 0, 'remaining_rows': 0}
            
            # 삭제할 행 인덱스 수집 (2부터 시작 - 헤더가 1행)
            rows_to_delete = []
            
            for idx, row in enumerate(all_data, start=2):
                # 해당 날짜의 데이터만 검사
                if row.get('날짜') != target_date:
                    continue
                
                try:
                    # 0값 판단 기준 (이미지 참고):
                    # 대비, 등락률, 시가, 고가, 저가, 거래량, 거래대금이 모두 0
                    change = float(str(row.get('대비', 0)).replace(',', ''))
                    change_rate = float(str(row.get('등락률', 0)).replace(',', ''))
                    open_price = float(str(row.get('시가', 0)).replace(',', ''))
                    high_price = float(str(row.get('고가', 0)).replace(',', ''))
                    low_price = float(str(row.get('저가', 0)).replace(',', ''))
                    volume = float(str(row.get('거래량', 0)).replace(',', ''))
                    trading_value = float(str(row.get('거래대금', 0)).replace(',', ''))
                    
                    # 모든 값이 0인 경우에만 삭제 대상
                    is_zero_row = (
                        change == 0 and 
                        change_rate == 0 and 
                        open_price == 0 and 
                        high_price == 0 and 
                        low_price == 0 and 
                        volume == 0 and 
                        trading_value == 0
                    )
                    
                    if is_zero_row:
                        symbol = row.get('종목명', 'Unknown')
                        collection_time = row.get('수집시간', 'Unknown')
                        rows_to_delete.append({
                            'index': idx,
                            'symbol': symbol,
                            'time': collection_time
                        })
                        logger.debug(f"🗑️ 삭제 대상: {symbol} ({collection_time})")
                        
                except (ValueError, TypeError) as e:
                    logger.debug(f"행 {idx} 값 파싱 오류: {e}")
                    continue
            
            # 삭제 실행 (역순으로 - 인덱스 변경 방지)
            deleted_count = 0
            deleted_symbols = []
            
            for row_info in reversed(rows_to_delete):
                try:
                    self.worksheet.delete_rows(row_info['index'])
                    deleted_count += 1
                    deleted_symbols.append(f"{row_info['symbol']}({row_info['time']})")
                    logger.info(f"🗑️ 삭제 완료: {row_info['symbol']} ({row_info['time']})")
                except Exception as e:
                    logger.warning(f"행 {row_info['index']} 삭제 실패: {e}")
            
            # 결과 통계
            today_rows_before = len([r for r in all_data if r.get('날짜') == target_date])
            today_rows_after = today_rows_before - deleted_count
            
            cleanup_stats = {
                'target_date': target_date,
                'deleted_count': deleted_count,
                'remaining_rows': today_rows_after,
                'total_rows_before': today_rows_before,
                'deleted_symbols': deleted_symbols[:10]
            }
            
            logger.info(f"🧹 0값 행 정리 완료: {target_date}")
            logger.info(f"📊 삭제: {deleted_count}개 행")
            logger.info(f"📊 남은 행: {today_rows_after}개 (해당 날짜)")
            if today_rows_before > 0:
                logger.info(f"⚡ 용량 절약: {round(deleted_count / today_rows_before * 100, 1)}%")
            
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"0값 행 정리 실패: {e}")
            return {'target_date': target_date, 'deleted_count': 0, 'error': str(e)}
    
    def append_real_data(self, data: List[Dict]) -> bool:
        """기존 인터페이스 호환성"""
        success, stats = self.append_optimized_data(data)
        return success


class EnhancedSheetsManager(OptimizedSheetsManager):
    """기존 호환성을 위한 별칭"""
    pass


async def main():
    """최적화된 메인 실행 함수 (v3.2.1)"""
    try:
        logger.info("=== 최적화된 KRX ETS 데이터 수집 시스템 v3.2.1 시작 ===")
        
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("❌ Playwright 패키지가 없습니다")
            sys.exit(1)
        
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
        
        if not creds_json or not sheet_id:
            raise ValueError("필수 환경변수가 설정되지 않았습니다")
        
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
        logger.info(f"🚀 시스템: 최적화 v3.2.1 (0값 행 정리 추가)")
        
        collector = PlaywrightKRXCollector()
        real_data = await collector.get_real_krx_data()
        
        if real_data:
            logger.info(f"✅ 데이터 수집 성공: {len(real_data)}개 종목")
            
            if test_mode:
                print(f"✅ 최적화 테스트 모드 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"🎯 최종 세션: {is_final_session}")
                print(f"🚀 시스템: v3.2.1 (0값 행 정리)")
                return
            
            sheets_manager = OptimizedSheetsManager(creds_json, sheet_id)
            
            # 1. ets.KRX 시트에 최적화된 데이터 추가
            success, optimization_stats = sheets_manager.append_optimized_data(real_data)
            
            # 2. 12:30 최종 세션 처리
            krx_updated = False
            cleanup_stats = {}
            
            if is_final_session:
                current_date = real_data[0]['date']
                
                # 2-1. KRX 시트에 요약 추가 (0값 포함 - 차트용)
                logger.info("🎯 12:30 최종 세션 - KRX 시트 요약 업데이트 (0값 포함)")
                krx_updated = sheets_manager.update_krx_final_summary(real_data)
                
                # 2-2. ets.KRX 시트에서 0값 행 정리 (신규 기능)
                logger.info("🧹 12:30 최종 세션 - ets.KRX 시트 0값 행 정리")
                cleanup_stats = sheets_manager.cleanup_zero_rows(current_date)
            
            if success:
                total_volume = sum(item['volume'] for item in real_data)
                active_items = len([item for item in real_data if item['volume'] > 0])
                
                optimization_json = json.dumps(optimization_stats)
                print(f"::set-output name=optimization_stats::{optimization_json}")
                
                print(f"✅ 최적화된 데이터 수집 및 저장 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"🔥 활성 종목: {active_items}개")
                print(f"📈 총 거래량: {total_volume:,} 톤")
                print(f"💾 최적화 결과: 추가 {optimization_stats['added']}개, 생략 {optimization_stats['skipped']}개")
                
                if optimization_stats['total'] > 0:
                    print(f"⚡ 중복 제거 효율: {round((optimization_stats['skipped']/optimization_stats['total']*100), 1)}%")
                
                if is_final_session:
                    print(f"\n🎯 12:30 KST 최종 마감 세션 완료!")
                    
                    if krx_updated:
                        print(f"✅ KRX 시트 요약 업데이트 성공 (0값 포함 - 차트용)")
                    else:
                        print(f"⚠️ KRX 시트 요약 업데이트 실패")
                    
                    if cleanup_stats:
                        print(f"\n🧹 ets.KRX 시트 0값 행 정리 결과:")
                        print(f"   • 삭제된 행: {cleanup_stats.get('deleted_count', 0)}개")
                        print(f"   • 남은 행: {cleanup_stats.get('remaining_rows', 0)}개")
                        if cleanup_stats.get('deleted_count', 0) > 0 and cleanup_stats.get('total_rows_before', 0) > 0:
                            efficiency = round(cleanup_stats['deleted_count'] / cleanup_stats['total_rows_before'] * 100, 1)
                            print(f"   • 용량 절약: {efficiency}%")
                    
                    print(f"\n📱 텔레그램 일일 요약 발송 예정")
            else:
                print("❌ Google Sheets 업데이트 실패")
                sys.exit(1)
        else:
            logger.error("❌ 모든 데이터 수집 방법 실패")
            print("❌ 최적화된 데이터 수집 실패!")
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
