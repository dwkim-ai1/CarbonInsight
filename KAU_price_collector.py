#!/usr/bin/env python3
"""
Playwright 기반 KRX ETS 데이터 수집기 (BeautifulSoup 완전 대체)
1. JavaScript 렌더링 완료 후 실제 데이터 추출
2. 동적 테이블(CI-GRID) 완벽 지원
3. 네트워크 요청 모니터링으로 AJAX 데이터 캐치
4. OCR 백업 시스템 통합
5. 실패시 명확한 실패 처리 (샘플 데이터 제거)
"""

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

# Playwright 및 OCR 관련
try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
    import easyocr
    from PIL import Image, ImageEnhance
    import cv2
    import numpy as np
except ImportError as e:
    logging.error(f"필수 패키지 누락: {e}")
    logging.error("설치 명령: pip install playwright easyocr opencv-python pillow")
    sys.exit(1)

# Google Sheets
import gspread

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
                'timeout': 45000,  # Playwright는 밀리초 단위
                'wait_timeout': 30000,
                'high_accuracy': False,
                'headless': True
            }
            
            # 거래 단계별 설정
            if trading_phase in ['opening_price_decision', 'closing_price_decision']:
                config.update({
                    'max_retries': 5, 
                    'timeout': 60000, 
                    'wait_timeout': 45000,
                    'high_accuracy': True,
                    'headless': False  # 중요 시점은 시각적 확인
                })
                logger.info(f"🔥 {trading_phase} 모드 - 최고 정확도")
            elif trading_phase == 'real_time_trading':
                config.update({
                    'max_retries': 4, 
                    'timeout': 30000,
                    'wait_timeout': 20000,
                    'headless': True  # 빠른 처리
                })
                logger.info("⚡ 실시간 거래 모드 - 고속 처리")
            
            return config
            
        except Exception as e:
            logger.warning(f"거래 상황 분석 중 오류: {e}")
            return {
                'market_phase': 'unknown', 'trading_phase': 'unknown', 'execution_priority': 'low',
                'max_retries': 3, 'timeout': 45000, 'wait_timeout': 30000, 'high_accuracy': False, 'headless': True
            }

    async def initialize_browser(self):
        """브라우저 초기화"""
        try:
            self.playwright = await async_playwright().start()
            
            # 브라우저 설정
            browser_config = {
                'headless': self.trading_config['headless'],
                'args': [
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-web-security',
                    '--disable-extensions',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            }
            
            self.browser = await self.playwright.chromium.launch(**browser_config)
            
            # 컨텍스트 설정 (한국 환경)
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                locale='ko-KR',
                timezone_id='Asia/Seoul',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            # 페이지 생성
            self.page = await self.context.new_page()
            
            # 네트워크 요청 모니터링 설정
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
            """AJAX 응답 캐치"""
            try:
                url = response.url
                
                # KRX AJAX 요청 감지
                if ('ETS99000001.jspx' in url or 
                    'json' in response.headers.get('content-type', '').lower() or
                    'application' in response.headers.get('content-type', '').lower()):
                    
                    if response.status == 200:
                        try:
                            content = await response.text()
                            if content and len(content) > 100:  # 의미있는 데이터만
                                self.ajax_responses.append({
                                    'url': url,
                                    'content': content,
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
            logger.info("=== Playwright 기반 KRX ETS 데이터 수집 시작 ===")
            logger.info(f"거래 단계: {config['trading_phase']}")
            logger.info(f"브라우저 모드: {'Headless' if config['headless'] else 'Visual'}")
            
            # 브라우저 초기화
            if not await self.initialize_browser():
                logger.error("브라우저 초기화 실패")
                return []
            
            for attempt in range(config['max_retries']):
                try:
                    logger.info(f"🌐 데이터 수집 시도 {attempt + 1}/{config['max_retries']}")
                    
                    # 1단계: 메인 페이지 로드
                    await self._load_main_page()
                    
                    # 2단계: 동적 테이블 로딩 대기
                    table_loaded = await self._wait_for_table_load()
                    
                    if table_loaded:
                        # 3단계: DOM에서 데이터 추출
                        dom_data = await self._extract_from_dom()
                        
                        if self._validate_real_data(dom_data):
                            logger.info(f"✅ DOM 추출 성공: {len(dom_data)}개 종목")
                            return self._enhance_with_trading_info(dom_data, config)
                    
                    # 4단계: AJAX 응답에서 데이터 추출 (백업)
                    ajax_data = await self._extract_from_ajax()
                    
                    if self._validate_real_data(ajax_data):
                        logger.info(f"✅ AJAX 추출 성공: {len(ajax_data)}개 종목")
                        return self._enhance_with_trading_info(ajax_data, config)
                    
                    # 5단계: OCR 백업 시스템 (최후 수단)
                    if config['high_accuracy']:  # 중요 시점에만 OCR 사용
                        ocr_data = await self._ocr_backup_extraction()
                        
                        if self._validate_real_data(ocr_data):
                            logger.info(f"✅ OCR 백업 성공: {len(ocr_data)}개 종목")
                            return self._enhance_with_trading_info(ocr_data, config)
                    
                    if attempt < config['max_retries'] - 1:
                        logger.info(f"시도 {attempt + 1} 실패 - 3초 후 재시도")
                        await asyncio.sleep(3)
                        
                except Exception as e:
                    logger.warning(f"시도 {attempt + 1} 중 오류: {e}")
                    if attempt < config['max_retries'] - 1:
                        await asyncio.sleep(5)
            
            # 모든 시도 실패
            logger.error("❌ 모든 데이터 수집 방법 실패")
            logger.error("🔍 시도한 방법: DOM 추출, AJAX 분석, OCR 백업")
            return []
            
        except Exception as e:
            logger.error(f"❌ 데이터 수집 중 심각한 오류: {e}")
            return []
        finally:
            await self.cleanup()

    async def _load_main_page(self):
        """메인 페이지 로드"""
        try:
            logger.info(f"🌐 메인 페이지 로딩: {self.main_page_url}")
            
            # 페이지 로드
            response = await self.page.goto(
                self.main_page_url, 
                timeout=self.trading_config['timeout'],
                wait_until='domcontentloaded'
            )
            
            if response.status != 200:
                raise Exception(f"HTTP {response.status}")
            
            # 기본 요소 로딩 대기
            await self.page.wait_for_load_state('networkidle', timeout=15000)
            
            logger.info("✅ 메인 페이지 로드 완료")
            
        except Exception as e:
            logger.error(f"메인 페이지 로드 실패: {e}")
            raise

    async def _wait_for_table_load(self) -> bool:
        """동적 테이블 로딩 대기"""
        try:
            logger.info("📊 동적 테이블 로딩 대기...")
            
            # 다양한 테이블 선택자 시도
            table_selectors = [
                'table[summary*="배출권 현재가"]',
                'table[id*="gridtable"]',
                '.CI-GRID-BODY-TABLE',
                'table.CI-GRID-BODY-TABLE',
                'table tbody tr td'
            ]
            
            table_found = False
            
            for selector in table_selectors:
                try:
                    # 테이블 요소 대기
                    await self.page.wait_for_selector(
                        selector, 
                        timeout=self.trading_config['wait_timeout']
                    )
                    
                    # 데이터 행이 실제로 있는지 확인
                    rows = await self.page.query_selector_all(f"{selector} tr")
                    
                    if len(rows) > 1:  # 헤더 + 데이터 행
                        logger.info(f"✅ 테이블 발견: {selector} ({len(rows)}개 행)")
                        table_found = True
                        break
                        
                except Exception:
                    continue
            
            if table_found:
                # 추가 대기 (JavaScript 렌더링 완료)
                await asyncio.sleep(2)
                
                # 조회 버튼 클릭 시도 (최신 데이터 로드)
                await self._trigger_data_refresh()
                
                return True
            else:
                logger.warning("⚠️ 테이블을 찾을 수 없음")
                return False
                
        except Exception as e:
            logger.warning(f"테이블 로딩 대기 중 오류: {e}")
            return False

    async def _trigger_data_refresh(self):
        """데이터 새로고침 트리거"""
        try:
            # 조회 버튼 찾기 및 클릭
            refresh_selectors = [
                'button.btn-board-search',
                'button:has-text("조회")',
                'input[type="button"][value*="조회"]',
                '.btn-search'
            ]
            
            for selector in refresh_selectors:
                try:
                    button = await self.page.query_selector(selector)
                    if button:
                        logger.info(f"🔄 데이터 새로고침 버튼 클릭: {selector}")
                        await button.click()
                        await asyncio.sleep(3)  # 데이터 로딩 대기
                        break
                        
                except Exception:
                    continue
                    
        except Exception as e:
            logger.debug(f"데이터 새로고침 시도 중 오류: {e}")

    async def _extract_from_dom(self) -> List[Dict]:
        """DOM에서 데이터 추출"""
        try:
            logger.info("📋 DOM에서 테이블 데이터 추출 중...")
            
            # 모든 테이블 행 찾기
            table_selectors = [
                'table[summary*="배출권"] tbody tr',
                'table[id*="gridtable"] tbody tr',
                '.CI-GRID-BODY-TABLE tbody tr',
                'table tbody tr'
            ]
            
            rows = []
            for selector in table_selectors:
                try:
                    found_rows = await self.page.query_selector_all(selector)
                    if len(found_rows) > 0:
                        rows = found_rows
                        logger.info(f"✅ 테이블 행 발견: {selector} ({len(rows)}개)")
                        break
                except Exception:
                    continue
            
            if not rows:
                logger.warning("DOM에서 테이블 행을 찾을 수 없음")
                return []
            
            extracted_data = []
            
            for i, row in enumerate(rows):
                try:
                    # 각 행의 셀 데이터 추출
                    cells = await row.query_selector_all('td')
                    
                    if len(cells) >= 8:  # 최소 필요 컬럼 수
                        cell_texts = []
                        for cell in cells:
                            text = await cell.text_content()
                            cell_texts.append(text.strip() if text else '')
                        
                        # 종목명 확인
                        symbol = cell_texts[0] if cell_texts else ''
                        if symbol and re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                            parsed_item = self._parse_row_data(cell_texts, i)
                            if parsed_item:
                                extracted_data.append(parsed_item)
                                logger.info(f"✅ {symbol}: {parsed_item['current_price']:,}원")
                                
                except Exception as e:
                    logger.debug(f"행 {i} 추출 중 오류: {e}")
                    continue
            
            logger.info(f"📊 DOM 추출 완료: {len(extracted_data)}개 종목")
            return extracted_data
            
        except Exception as e:
            logger.error(f"DOM 추출 중 오류: {e}")
            return []

    async def _extract_from_ajax(self) -> List[Dict]:
        """AJAX 응답에서 데이터 추출"""
        try:
            logger.info("📡 AJAX 응답 분석 중...")
            
            if not self.ajax_responses:
                logger.warning("캐치된 AJAX 응답 없음")
                return []
            
            for ajax_response in self.ajax_responses:
                try:
                    content = ajax_response['content']
                    
                    # JSON 형태 데이터 시도
                    if content.strip().startswith('{') or content.strip().startswith('['):
                        try:
                            json_data = json.loads(content)
                            extracted = self._parse_json_response(json_data)
                            if extracted:
                                logger.info(f"✅ JSON 응답에서 {len(extracted)}개 종목 추출")
                                return extracted
                        except json.JSONDecodeError:
                            pass
                    
                    # HTML 테이블 형태 데이터 시도
                    if '<table' in content or '<tr' in content:
                        extracted = self._parse_html_response(content)
                        if extracted:
                            logger.info(f"✅ HTML 응답에서 {len(extracted)}개 종목 추출")
                            return extracted
                            
                except Exception as e:
                    logger.debug(f"AJAX 응답 분석 오류: {e}")
                    continue
            
            logger.warning("AJAX 응답에서 유효한 데이터를 찾지 못함")
            return []
            
        except Exception as e:
            logger.error(f"AJAX 추출 중 오류: {e}")
            return []

    async def _ocr_backup_extraction(self) -> List[Dict]:
        """OCR 백업 데이터 추출"""
        try:
            logger.info("📷 OCR 백업 시스템 시작...")
            
            # OCR 리더 초기화
            if not self.ocr_reader:
                self.ocr_reader = easyocr.Reader(['ko', 'en'], gpu=False)
            
            # 페이지 스크린샷 촬영
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = self.screenshots_dir / f"krx_table_{timestamp}.png"
            
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            logger.info(f"📸 스크린샷 저장: {screenshot_path}")
            
            # 테이블 영역 크롭
            table_image = await self._crop_table_area(screenshot_path)
            
            if table_image:
                # OCR 실행
                ocr_results = self.ocr_reader.readtext(table_image)
                
                # OCR 결과를 테이블 데이터로 변환
                extracted_data = self._parse_ocr_results(ocr_results)
                
                if extracted_data:
                    logger.info(f"✅ OCR 추출 성공: {len(extracted_data)}개 종목")
                    return extracted_data
            
            logger.warning("OCR 백업 추출 실패")
            return []
            
        except Exception as e:
            logger.error(f"OCR 백업 중 오류: {e}")
            return []

    async def _crop_table_area(self, screenshot_path: Path) -> Optional[np.ndarray]:
        """테이블 영역 크롭"""
        try:
            # 테이블 요소의 위치 정보 가져오기
            table_selector = 'table[summary*="배출권"], .CI-GRID-AREA, table'
            
            table_element = await self.page.query_selector(table_selector)
            if not table_element:
                return None
            
            # 요소의 경계 박스 가져오기
            bbox = await table_element.bounding_box()
            if not bbox:
                return None
            
            # 이미지 크롭
            image = cv2.imread(str(screenshot_path))
            if image is None:
                return None
            
            x, y, width, height = int(bbox['x']), int(bbox['y']), int(bbox['width']), int(bbox['height'])
            cropped = image[y:y+height, x:x+width]
            
            # 이미지 전처리 (OCR 정확도 향상)
            gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
            enhanced = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
            
            return enhanced
            
        except Exception as e:
            logger.debug(f"이미지 크롭 중 오류: {e}")
            return None

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

    def _parse_json_response(self, json_data) -> List[Dict]:
        """JSON 응답 파싱"""
        try:
            # JSON 구조 분석 및 데이터 추출 로직
            # (실제 KRX AJAX 응답 구조에 맞게 구현)
            extracted_data = []
            
            # JSON 구조 탐색
            if isinstance(json_data, dict):
                for key, value in json_data.items():
                    if isinstance(value, list) and len(value) > 0:
                        for item in value:
                            if isinstance(item, dict):
                                parsed = self._parse_json_item(item)
                                if parsed:
                                    extracted_data.append(parsed)
            
            return extracted_data
            
        except Exception as e:
            logger.debug(f"JSON 파싱 오류: {e}")
            return []

    def _parse_json_item(self, item: Dict) -> Optional[Dict]:
        """JSON 아이템 파싱"""
        try:
            # JSON 아이템에서 필요한 필드 추출
            symbol = item.get('isu_cd', item.get('symbol', ''))
            if not symbol or not re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                return None
            
            current_price = float(item.get('tdd_clsprc', item.get('current_price', 0)))
            if current_price <= 0:
                return None
            
            return {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'time': datetime.now().strftime('%H:%M:%S'),
                'symbol': symbol,
                'current_price': current_price,
                'change': float(item.get('cmpprevdd_prc', item.get('change', 0))),
                'change_rate': float(item.get('fluc_rt', item.get('change_rate', 0))),
                'open_price': float(item.get('tdd_opnprc', item.get('open_price', 0))),
                'high_price': float(item.get('tdd_hgprc', item.get('high_price', 0))),
                'low_price': float(item.get('tdd_lwprc', item.get('low_price', 0))),
                'volume': float(item.get('acc_trdvol', item.get('volume', 0))),
                'trading_value': float(item.get('acc_trdval', item.get('trading_value', 0))),
                'weighted_avg': float(item.get('wt_avg_prc', item.get('weighted_avg', 0))),
                'collection_time': datetime.now().strftime('%H:%M:%S'),
                'data_source': 'playwright_ajax'
            }
            
        except Exception as e:
            logger.debug(f"JSON 아이템 파싱 오류: {e}")
            return None

    def _parse_html_response(self, html_content: str) -> List[Dict]:
        """HTML 응답 파싱"""
        try:
            # BeautifulSoup 사용하여 HTML 파싱
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html_content, 'html.parser')
            rows = soup.find_all('tr')
            
            extracted_data = []
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 8:
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    parsed = self._parse_row_data(cell_texts, 0)
                    if parsed:
                        parsed['data_source'] = 'playwright_html'
                        extracted_data.append(parsed)
            
            return extracted_data
            
        except Exception as e:
            logger.debug(f"HTML 파싱 오류: {e}")
            return []

    def _parse_ocr_results(self, ocr_results) -> List[Dict]:
        """OCR 결과 파싱"""
        try:
            # OCR 결과를 테이블 형태로 정리
            extracted_data = []
            
            # OCR 텍스트 추출 및 정렬
            texts = []
            for (bbox, text, confidence) in ocr_results:
                if confidence > 0.5:  # 신뢰도 50% 이상만
                    texts.append({
                        'text': text.strip(),
                        'x': bbox[0][0],
                        'y': bbox[0][1],
                        'confidence': confidence
                    })
            
            # Y 좌표로 행 그룹핑
            texts.sort(key=lambda x: (x['y'], x['x']))
            
            rows = []
            current_row = []
            current_y = None
            
            for text_info in texts:
                if current_y is None or abs(text_info['y'] - current_y) > 20:
                    if current_row:
                        rows.append(current_row)
                    current_row = [text_info['text']]
                    current_y = text_info['y']
                else:
                    current_row.append(text_info['text'])
            
            if current_row:
                rows.append(current_row)
            
            # 각 행을 데이터로 변환
            for row_texts in rows:
                if len(row_texts) >= 3:  # 최소 종목명, 가격, 변동 있어야 함
                    symbol = row_texts[0]
                    if re.match(r'^(KAU|KCU|KOC|i-)', symbol):
                        try:
                            parsed = {
                                'date': datetime.now().strftime('%Y-%m-%d'),
                                'time': datetime.now().strftime('%H:%M:%S'),
                                'symbol': symbol,
                                'current_price': self._extract_number(row_texts[1]),
                                'change': self._extract_number(row_texts[2] if len(row_texts) > 2 else '0'),
                                'change_rate': self._extract_number(row_texts[3] if len(row_texts) > 3 else '0'),
                                'open_price': self._extract_number(row_texts[4] if len(row_texts) > 4 else '0'),
                                'high_price': self._extract_number(row_texts[5] if len(row_texts) > 5 else '0'),
                                'low_price': self._extract_number(row_texts[6] if len(row_texts) > 6 else '0'),
                                'volume': self._extract_number(row_texts[7] if len(row_texts) > 7 else '0'),
                                'trading_value': self._extract_number(row_texts[8] if len(row_texts) > 8 else '0'),
                                'weighted_avg': 0,
                                'collection_time': datetime.now().strftime('%H:%M:%S'),
                                'data_source': 'playwright_ocr'
                            }
                            
                            if parsed['current_price'] > 0:
                                extracted_data.append(parsed)
                                
                        except Exception as e:
                            logger.debug(f"OCR 행 파싱 오류: {e}")
                            continue
            
            return extracted_data
            
        except Exception as e:
            logger.error(f"OCR 결과 파싱 오류: {e}")
            return []

    def _validate_real_data(self, data: List[Dict]) -> bool:
        """실제 데이터 검증"""
        if not data or len(data) < 3:
            return False
            
        symbols = {item.get('symbol', '') for item in data}
        required_symbols = {'KAU25', 'KCU25'}
        found_symbols = required_symbols.intersection(symbols)
        
        return len(found_symbols) > 0

    def _enhance_with_trading_info(self, data: List[Dict], config: Dict) -> List[Dict]:
        """거래 정보로 데이터 강화"""
        for item in data:
            item['trading_phase'] = config['trading_phase']
            item['market_phase'] = config['market_phase']
            item['execution_priority'] = config['execution_priority']
            item['extraction_method'] = item.get('data_source', 'unknown')
            
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
            
            logger.info("✅ 브라우저 리소스 정리 완료")
            
        except Exception as e:
            logger.warning(f"리소스 정리 중 오류: {e}")


class EnhancedSheetsManager:
    """Google Sheets 관리자 (기존과 동일)"""
    def __init__(self, credentials_json: str, sheet_id: str):
        try:
            logger.info("Google Sheets 연결 시작...")
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            self.setup_worksheet()
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def setup_worksheet(self):
        try:
            try:
                self.worksheet = self.spreadsheet.worksheet('ets.KRX')
                self._verify_and_fix_headers()
            except gspread.WorksheetNotFound:
                self.worksheet = self.spreadsheet.add_worksheet(title='ets.KRX', rows=2000, cols=16)
                self._setup_headers()
        except Exception as e:
            logger.error(f"워크시트 설정 실패: {e}")
            raise
    
    def _verify_and_fix_headers(self):
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
    
    def append_real_data(self, data: List[Dict]) -> bool:
        try:
            if not data:
                return False
            
            current_date = data[0]['date']
            current_time = data[0].get('time', datetime.now().strftime('%H:%M:%S'))
            
            new_rows = []
            for item in data:
                row = [
                    str(item['date']), str(item.get('time', current_time)), str(item['symbol']),
                    str(item['current_price']), str(item['change']), str(item['change_rate']),
                    str(item['open_price']), str(item['high_price']), str(item['low_price']),
                    str(item['volume']), str(item['trading_value']), str(item['weighted_avg']),
                    str(item.get('collection_time', current_time)), str(item.get('data_source', 'unknown')),
                    str(item.get('trading_phase', 'unknown')), str(item.get('execution_priority', 'unknown'))
                ]
                new_rows.append(row)
            
            if new_rows:
                self.worksheet.append_rows(new_rows)
                logger.info(f"✅ {len(new_rows)}개 행 추가 완료")
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"데이터 추가 실패: {e}")
            return False


async def main():
    """메인 실행 함수"""
    try:
        logger.info("=== Playwright 기반 KRX ETS 데이터 수집 시작 ===")
        
        # 환경변수 확인
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        test_mode = os.getenv('TEST_MODE', 'false').lower() == 'true'
        
        if not creds_json or not sheet_id:
            raise ValueError("필수 환경변수가 설정되지 않았습니다")
        
        # 거래 정보
        now = datetime.now()
        trading_phase = os.getenv('TRADING_PHASE', 'unknown')
        execution_priority = os.getenv('EXECUTION_PRIORITY', 'unknown')
        
        logger.info(f"🕐 수집 시작: {now.strftime('%Y-%m-%d %H:%M:%S')} KST")
        logger.info(f"⚡ 거래 단계: {trading_phase}")
        logger.info(f"🎯 실행 우선도: {execution_priority}")
        
        # Playwright 데이터 수집기 초기화
        collector = PlaywrightKRXCollector()
        
        # 데이터 수집 실행
        real_data = await collector.get_real_krx_data()
        
        if real_data:
            logger.info(f"✅ 데이터 수집 성공: {len(real_data)}개 종목")
            
            # 수집 방법별 통계
            extraction_methods = {}
            for item in real_data:
                method = item.get('data_source', 'unknown')
                extraction_methods[method] = extraction_methods.get(method, 0) + 1
            
            logger.info("📊 추출 방법별 통계:")
            for method, count in extraction_methods.items():
                method_name = {
                    'playwright_dom': '🌐 DOM 추출',
                    'playwright_ajax': '📡 AJAX 분석',
                    'playwright_ocr': '📷 OCR 백업',
                    'playwright_html': '📄 HTML 파싱'
                }.get(method, f'❓ {method}')
                logger.info(f"  • {method_name}: {count}개")
            
            if test_mode:
                print(f"✅ Playwright 기반 데이터 수집 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"🎯 우선도: {execution_priority}")
                print(f"🔧 추출 방법: {list(extraction_methods.keys())}")
                return
            
            # Google Sheets 저장
            sheets_manager = EnhancedSheetsManager(creds_json, sheet_id)
            success = sheets_manager.append_real_data(real_data)
            
            if success:
                total_volume = sum(item['volume'] for item in real_data)
                active_items = len([item for item in real_data if item['volume'] > 0])
                
                print(f"✅ Playwright 기반 데이터 수집 및 저장 성공!")
                print(f"📊 총 종목: {len(real_data)}개")
                print(f"🔥 활성 종목: {active_items}개")
                print(f"📈 총 거래량: {total_volume:,} 톤")
                print(f"🌐 추출 방법: {', '.join(extraction_methods.keys())}")
                print(f"⚡ 거래 단계: {trading_phase}")
                print(f"🕐 수집 시간: {now.strftime('%H:%M:%S')} KST")
                
                # 활성 거래 종목 정보
                active_data = [item for item in real_data if item['volume'] > 0]
                if active_data:
                    print(f"\n📋 활성 거래 종목:")
                    for item in active_data[:5]:
                        print(f"  • {item['symbol']}: {item['current_price']:,}원 "
                              f"({item['change']:+.0f}, {item['change_rate']:+.2f}%) "
                              f"거래량: {item['volume']:,}톤")
                
            else:
                print("❌ Google Sheets 업데이트 실패")
                sys.exit(1)
                
        else:
            # 완전 실패 처리
            logger.error("❌ 모든 데이터 수집 방법 실패")
            print("❌ Playwright 기반 데이터 수집 실패!")
            print("🔍 시도한 방법:")
            print("   • DOM 직접 추출")
            print("   • AJAX 응답 분석")
            print("   • OCR 백업 시스템")
            print("📞 문제 지속시 GitHub Issues에 신고하세요")
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
