"""
NGMS Web Scraper
국가온실가스종합관리시스템 웹 스크래퍼

테이블 데이터를 직접 스크래핑하는 방식 사용
(Excel 다운로드 방식은 WebSquare 이벤트 핸들러 문제로 작동하지 않음)
"""

import os
import asyncio
import tempfile
import json
from typing import Dict, Optional, List, Any
import pandas as pd
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Frame

from config import (
    NGMS_BASE_URL,
    NGMS_MAIN_URL,
    IFRAME_URLS,
    BROWSER_SETTINGS,
    DEBUG,
)
from utils import setup_logging, clean_excel_data, ensure_directory

logger = setup_logging()


class NGMSScraper:
    """NGMS 웹사이트 스크래퍼 클래스 - 테이블 스크래핑 방식"""
    
    def __init__(self, download_dir: Optional[str] = None, debug_mode: bool = False):
        """
        Initialize scraper
        
        Args:
            download_dir: Directory for debug files
            debug_mode: Enable debug mode for screenshots and HTML dumps
        """
        self.download_dir = download_dir or tempfile.mkdtemp()
        self.debug_mode = debug_mode or DEBUG.get('save_screenshots', False)
        self.debug_dir = os.path.join(self.download_dir, 'debug')
        
        ensure_directory(self.download_dir)
        if self.debug_mode:
            ensure_directory(self.debug_dir)
        
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        
        logger.info(f"Scraper 초기화 - 디버그 경로: {self.debug_dir}")
    
    async def initialize(self) -> None:
        """Initialize browser and page"""
        logger.info("브라우저 초기화 중...")
        
        self.playwright = await async_playwright().start()
        
        self.browser = await self.playwright.chromium.launch(
            headless=BROWSER_SETTINGS['headless'],
            slow_mo=BROWSER_SETTINGS.get('slow_mo', 50)
        )
        
        self.context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080}
        )
        
        self.page = await self.context.new_page()
        self.page.set_default_timeout(BROWSER_SETTINGS['timeout'])
        
        logger.info("브라우저 초기화 완료")
    
    async def close(self) -> None:
        """Close browser and cleanup"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("브라우저 종료")
    
    async def _save_debug(self, name: str, page_or_frame=None) -> None:
        """Save debug screenshot and HTML"""
        if not self.debug_mode:
            return
        
        target = page_or_frame or self.page
        try:
            # Save screenshot (only for Page, not Frame)
            if isinstance(target, Page):
                await target.screenshot(path=os.path.join(self.debug_dir, f"{name}.png"))
            
            # Save HTML
            html = await target.content()
            with open(os.path.join(self.debug_dir, f"{name}.html"), 'w', encoding='utf-8') as f:
                f.write(html)
            
            logger.debug(f"디버그 저장: {name}")
        except Exception as e:
            logger.debug(f"디버그 저장 실패 ({name}): {e}")
    
    async def _direct_iframe_scrape(self, data_type: str) -> Optional[pd.DataFrame]:
        """
        Directly access iframe URL and scrape data
        
        Args:
            data_type: Type of data
            
        Returns:
            DataFrame with scraped data
        """
        logger.info(f"직접 iframe 접속 스크래핑: {data_type}")
        
        iframe_url = IFRAME_URLS.get(data_type)
        if not iframe_url:
            logger.error(f"iframe URL 없음: {data_type}")
            return None
        
        new_page = await self.context.new_page()
        new_page.set_default_timeout(60000)
        
        try:
            logger.info(f"URL 접속: {iframe_url}")
            await new_page.goto(iframe_url, wait_until='networkidle')
            await asyncio.sleep(3)  # Wait for page to fully load
            
            # ★★★ 핵심: 검색 버튼 클릭하여 데이터 로드 ★★★
            await self._click_search_button(new_page, data_type)
            
            # Wait for data to load after search
            await asyncio.sleep(5)
            
            await self._save_debug(f"after_search_{data_type}", new_page)
            
            # Try multiple extraction methods
            df = None
            
            # Method 1: WebSquare grid getAllJSON
            df = await self._extract_websquare_grid(new_page, data_type)
            if df is not None and len(df) > 0:
                return df
            
            # Method 2: Extract from visible table DOM
            df = await self._extract_table_dom(new_page, data_type)
            if df is not None and len(df) > 0:
                return df
            
            # Method 3: Extract with pagination
            df = await self._extract_with_pagination(new_page, data_type)
            if df is not None and len(df) > 0:
                return df
            
            return None
            
        except Exception as e:
            logger.error(f"직접 스크래핑 실패: {e}")
            return None
        finally:
            await new_page.close()
    
    async def _click_search_button(self, page: Page, data_type: str) -> bool:
        """
        Click search/query button to load data
        
        Args:
            page: Page object
            data_type: Type of data
            
        Returns:
            True if clicked successfully
        """
        logger.info(f"검색 버튼 클릭 시도: {data_type}")
        
        # 검색/조회 버튼 선택자들
        search_selectors = [
            'input[value="검색"]',
            'input[value="조회"]',
            'button:has-text("검색")',
            'button:has-text("조회")',
            'a:has-text("검색")',
            'a:has-text("조회")',
            '#mf_trigger1',  # WebSquare 기본 버튼 ID 패턴
            'input[type="button"][value*="검색"]',
            'input[type="button"][value*="조회"]',
        ]
        
        for selector in search_selectors:
            try:
                button = await page.query_selector(selector)
                if button:
                    logger.info(f"검색 버튼 발견: {selector}")
                    await button.click()
                    
                    # Wait for data loading
                    await asyncio.sleep(3)
                    
                    # Wait for network to be idle (data loaded)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=10000)
                    except:
                        pass
                    
                    logger.info("검색 버튼 클릭 완료, 데이터 로딩 대기...")
                    return True
            except Exception as e:
                logger.debug(f"검색 버튼 클릭 실패 ({selector}): {e}")
                continue
        
        # JavaScript로 검색 함수 직접 호출 시도
        js_search_functions = [
            'scwin.btn_search_onclick()',
            'scwin.fn_search()',
            'fn_search()',
            'doSearch()',
        ]
        
        for js_func in js_search_functions:
            try:
                await page.evaluate(js_func)
                logger.info(f"JS 검색 함수 호출: {js_func}")
                await asyncio.sleep(3)
                return True
            except:
                continue
        
        logger.warning("검색 버튼을 찾을 수 없습니다")
        return False
    
    async def _extract_websquare_grid(self, page: Page, data_type: str) -> Optional[pd.DataFrame]:
        """Extract data using WebSquare grid API"""
        logger.info("WebSquare 그리드 API 추출 시도")
        
        # JavaScript to extract all data from WebSquare grid
        script = """
        async () => {
            try {
                // Wait a bit for WebSquare to be ready
                await new Promise(r => setTimeout(r, 1000));
                
                if (typeof WebSquare === 'undefined') {
                    return {error: 'WebSquare not found'};
                }
                
                // Find grid component - try multiple ID patterns
                const gridIdPatterns = ['grd1', 'grd', 'grid1', 'grid', 'mf_grd1', 'mf_grid1'];
                let grid = null;
                let gridId = null;
                
                for (const pattern of gridIdPatterns) {
                    try {
                        const g = WebSquare.util.getComponentById(pattern);
                        if (g && typeof g.getRowCount === 'function') {
                            const count = g.getRowCount();
                            if (count > 0) {
                                grid = g;
                                gridId = pattern;
                                break;
                            }
                        }
                    } catch(e) {}
                }
                
                // Also search by element
                if (!grid) {
                    const gridElements = document.querySelectorAll('[id*="grd"], [id*="grid"], .w2grid');
                    for (const gridEl of gridElements) {
                        const id = gridEl.id;
                        if (!id) continue;
                        
                        try {
                            const g = WebSquare.util.getComponentById(id);
                            if (g && typeof g.getRowCount === 'function') {
                                const count = g.getRowCount();
                                if (count > 0) {
                                    grid = g;
                                    gridId = id;
                                    break;
                                }
                            }
                        } catch(e) {}
                    }
                }
                
                if (!grid) {
                    return {error: 'Grid not found or empty', debug: 'No grid with data found'};
                }
                
                const rowCount = grid.getRowCount();
                
                // Try getAllJSON first (가장 확실한 방법)
                if (typeof grid.getAllJSON === 'function') {
                    try {
                        const jsonStr = grid.getAllJSON();
                        const data = JSON.parse(jsonStr);
                        if (data && data.length > 0) {
                            return {success: true, data: data, method: 'getAllJSON', gridId: gridId, rowCount: rowCount};
                        }
                    } catch(e) {}
                }
                
                // Try getRowJSON for each row
                if (typeof grid.getRowJSON === 'function') {
                    try {
                        const data = [];
                        for (let r = 0; r < rowCount; r++) {
                            const rowJson = grid.getRowJSON(r);
                            if (rowJson) {
                                data.push(JSON.parse(rowJson));
                            }
                        }
                        if (data.length > 0) {
                            return {success: true, data: data, method: 'getRowJSON', gridId: gridId, rowCount: rowCount};
                        }
                    } catch(e) {}
                }
                
                // Try getCellData with column IDs
                if (typeof grid.getCellData === 'function') {
                    const data = [];
                    let colIds = [];
                    
                    // Get column IDs
                    if (typeof grid.getColumnIDArray === 'function') {
                        colIds = grid.getColumnIDArray() || [];
                    }
                    
                    // Fallback: get from header
                    if (colIds.length === 0) {
                        const colCount = grid.getColumnCount ? grid.getColumnCount() : 20;
                        for (let c = 0; c < colCount; c++) {
                            try {
                                const id = grid.getColumnID ? grid.getColumnID(c) : `col_${c}`;
                                colIds.push(id || `col_${c}`);
                            } catch(e) { break; }
                        }
                    }
                    
                    for (let r = 0; r < rowCount; r++) {
                        const row = {};
                        for (let c = 0; c < colIds.length; c++) {
                            try {
                                const val = grid.getCellData(r, colIds[c]) || grid.getCellData(r, c);
                                row[colIds[c]] = val !== undefined ? String(val) : '';
                            } catch(e) {
                                row[colIds[c]] = '';
                            }
                        }
                        data.push(row);
                    }
                    
                    if (data.length > 0) {
                        return {success: true, data: data, method: 'getCellData', gridId: gridId, rowCount: rowCount};
                    }
                }
                
                // Try through DataList (WebSquare data binding)
                const dataListIds = ['dataList1', 'dataList', 'dlt_list', 'dlt_data', 'dlt1'];
                for (const dlId of dataListIds) {
                    try {
                        const dl = WebSquare.util.getComponentById(dlId);
                        if (dl) {
                            if (typeof dl.getAllJSON === 'function') {
                                const jsonStr = dl.getAllJSON();
                                const data = JSON.parse(jsonStr);
                                if (data && data.length > 0) {
                                    return {success: true, data: data, method: 'dataList', dataListId: dlId, rowCount: data.length};
                                }
                            }
                            if (typeof dl.getRowCount === 'function') {
                                const count = dl.getRowCount();
                                if (count > 0) {
                                    const data = [];
                                    for (let r = 0; r < count; r++) {
                                        const rowData = dl.getRowJSON ? JSON.parse(dl.getRowJSON(r)) : {};
                                        data.push(rowData);
                                    }
                                    if (data.length > 0) {
                                        return {success: true, data: data, method: 'dataList-row', dataListId: dlId, rowCount: data.length};
                                    }
                                }
                            }
                        }
                    } catch(e) {}
                }
                
                return {error: 'Failed to extract data', gridId: gridId, rowCount: rowCount};
                
            } catch(e) {
                return {error: e.message, stack: e.stack};
            }
        }
        """
        
        try:
            result = await page.evaluate(script)
            
            logger.info(f"WebSquare 결과: {result.get('method', result.get('error', 'unknown'))}, rows={result.get('rowCount', 0)}")
            
            if result and result.get('success') and result.get('data'):
                data = result['data']
                df = pd.DataFrame(data)
                logger.info(f"WebSquare 추출 성공 ({result.get('method')}): {len(df)}행")
                return df
            else:
                logger.debug(f"WebSquare 추출 실패: {result}")
                
        except Exception as e:
            logger.debug(f"WebSquare API 실패: {e}")
        
        return None
    
    async def _extract_table_dom(self, page: Page, data_type: str) -> Optional[pd.DataFrame]:
        """Extract data from table DOM elements"""
        logger.info("DOM 테이블 추출 시도")
        
        script = """
        () => {
            const results = [];
            const headers = [];
            
            // Get headers - try multiple selectors
            const headerSelectors = [
                '.gridHeaderTable thead th nobr',
                '.gridHeaderTable th nobr', 
                '.w2grid_header th',
                'thead th',
                '.gridHeaderTD nobr',
                '.gridHeaderTD'
            ];
            
            for (const selector of headerSelectors) {
                document.querySelectorAll(selector).forEach(el => {
                    const text = el.textContent.trim();
                    if (text && text !== '' && !headers.includes(text)) {
                        headers.push(text);
                    }
                });
                if (headers.length > 0) break;
            }
            
            // Get data rows - try multiple selectors
            const bodySelectors = [
                '.gridBodyTable tbody tr',
                '.w2grid tbody tr',
                '#mf_grd1_body_tbody tr',
                'table[id*="grid"] tbody tr',
                'table[id*="grd"] tbody tr'
            ];
            
            let rows = [];
            for (const selector of bodySelectors) {
                rows = document.querySelectorAll(selector);
                if (rows.length > 0) break;
            }
            
            rows.forEach(row => {
                const cells = row.querySelectorAll('td');
                if (cells.length === 0) return;
                
                const rowData = {};
                let hasData = false;
                
                cells.forEach((cell, idx) => {
                    // Get cell content (handle nobr, div, span)
                    let value = '';
                    const nobr = cell.querySelector('nobr');
                    const div = cell.querySelector('div');
                    
                    if (nobr) {
                        value = nobr.textContent.trim();
                    } else if (div) {
                        value = div.textContent.trim();
                    } else {
                        value = cell.textContent.trim();
                    }
                    
                    const key = headers[idx] || `col_${idx}`;
                    rowData[key] = value;
                    
                    if (value && value.length > 0) {
                        hasData = true;
                    }
                });
                
                if (hasData) {
                    results.push(rowData);
                }
            });
            
            return {
                headers: headers,
                rowCount: results.length,
                data: results,
                debug: {
                    headerSelector: headerSelectors.find(s => document.querySelector(s)),
                    bodySelector: bodySelectors.find(s => document.querySelector(s))
                }
            };
        }
        """
        
        try:
            result = await page.evaluate(script)
            
            if result and result.get('data') and len(result['data']) > 0:
                df = pd.DataFrame(result['data'])
                logger.info(f"DOM 추출 성공: {len(df)}행, 헤더: {result.get('headers', [])[:5]}")
                logger.debug(f"사용된 선택자: {result.get('debug')}")
                return df
            else:
                logger.debug(f"DOM 추출 실패: 데이터 없음")
                
        except Exception as e:
            logger.debug(f"DOM 테이블 추출 실패: {e}")
        
        return None
    
    async def _extract_with_pagination(self, page: Page, data_type: str) -> Optional[pd.DataFrame]:
        """Extract data by iterating through all pages"""
        logger.info("페이지네이션 추출 시도")
        
        all_data = []
        headers = []
        current_page = 1
        max_pages = 200  # Safety limit (예: 774건 / 20건 per page = ~39 pages)
        seen_first_rows = set()  # 중복 페이지 감지용
        
        # 먼저 헤더 추출
        header_script = """
        () => {
            const headers = [];
            const selectors = [
                '.gridHeaderTable th nobr',
                '.gridHeaderTable th',
                '.gridHeaderTD nobr',
                'thead th nobr',
                'thead th'
            ];
            
            for (const selector of selectors) {
                document.querySelectorAll(selector).forEach(el => {
                    const text = el.textContent.trim();
                    if (text && !headers.includes(text)) {
                        headers.push(text);
                    }
                });
                if (headers.length > 0) break;
            }
            
            return headers;
        }
        """
        
        try:
            headers = await page.evaluate(header_script)
            logger.info(f"헤더 추출: {headers}")
        except Exception as e:
            logger.debug(f"헤더 추출 실패: {e}")
        
        while current_page <= max_pages:
            # Extract current page data
            extract_script = """
            () => {
                const results = [];
                const selectors = [
                    '.gridBodyTable tbody tr',
                    '.w2grid tbody tr',
                    'table[id*="grid"] tbody tr',
                    'table[id*="grd"] tbody tr',
                    '#mf_grd1_body_tbody tr'
                ];
                
                let rows = [];
                for (const selector of selectors) {
                    rows = document.querySelectorAll(selector);
                    if (rows.length > 0) break;
                }
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    const rowData = [];
                    
                    cells.forEach(cell => {
                        const nobr = cell.querySelector('nobr');
                        const div = cell.querySelector('div');
                        let value = '';
                        
                        if (nobr) {
                            value = nobr.textContent.trim();
                        } else if (div) {
                            value = div.textContent.trim();
                        } else {
                            value = cell.textContent.trim();
                        }
                        
                        rowData.push(value);
                    });
                    
                    if (rowData.some(v => v && v.length > 0)) {
                        results.push(rowData);
                    }
                });
                
                return results;
            }
            """
            
            try:
                page_data = await page.evaluate(extract_script)
                
                if page_data and len(page_data) > 0:
                    # 중복 페이지 감지 (첫 행의 데이터로 확인)
                    first_row_key = '|'.join(str(v) for v in page_data[0][:3])
                    
                    if first_row_key in seen_first_rows:
                        logger.info(f"페이지 {current_page}: 중복 데이터 감지, 종료")
                        break
                    
                    seen_first_rows.add(first_row_key)
                    all_data.extend(page_data)
                    logger.info(f"페이지 {current_page}: {len(page_data)}행 추출 (누적: {len(all_data)}행)")
                else:
                    logger.info(f"페이지 {current_page}: 데이터 없음")
                    break
                
                # Try to click next page
                next_clicked = await self._click_next_page_button(page, current_page)
                
                if not next_clicked:
                    logger.info("다음 페이지 없음, 종료")
                    break
                
                current_page += 1
                await asyncio.sleep(1)  # Wait for page to load
                
            except Exception as e:
                logger.debug(f"페이지 {current_page} 추출 실패: {e}")
                break
        
        if all_data:
            # Create DataFrame with headers
            if headers and len(headers) >= len(all_data[0]):
                df = pd.DataFrame(all_data, columns=headers[:len(all_data[0])])
            else:
                df = pd.DataFrame(all_data)
            
            logger.info(f"페이지네이션 추출 완료: {len(df)}행, {current_page-1}페이지")
            return df
        
        return None
    
    async def _click_next_page_button(self, page: Page, current_page: int) -> bool:
        """Click next page button"""
        
        # 다음 페이지 버튼 선택자들
        next_page_num = current_page + 1
        
        selectors = [
            # 페이지 번호 직접 클릭
            f'a:has-text("{next_page_num}")',
            f'span:has-text("{next_page_num}")',
            # 다음 버튼
            'a[title="다음"]',
            'a[title="Next"]',
            'img[alt*="다음"]',
            'a.next',
            'button.next',
            # WebSquare 페이징 버튼
            'a[onclick*="goPage"]',
            '.w2pageList a',
            '.pagination a',
        ]
        
        for selector in selectors:
            try:
                # 페이지 번호의 경우 정확한 숫자 매칭
                if f'"{next_page_num}"' in selector:
                    elements = await page.query_selector_all(selector)
                    for el in elements:
                        text = await el.text_content()
                        if text and text.strip() == str(next_page_num):
                            await el.click()
                            await asyncio.sleep(0.5)
                            return True
                else:
                    element = await page.query_selector(selector)
                    if element:
                        # 비활성화 상태 확인
                        is_disabled = await element.get_attribute('disabled')
                        class_attr = await element.get_attribute('class') or ''
                        
                        if is_disabled or 'disabled' in class_attr:
                            continue
                        
                        await element.click()
                        await asyncio.sleep(0.5)
                        return True
            except:
                continue
        
        # JavaScript로 페이지 이동 시도
        js_page_functions = [
            f'goPage({next_page_num})',
            f'fn_goPage({next_page_num})',
            f'scwin.fn_goPage({next_page_num})',
        ]
        
        for js_func in js_page_functions:
            try:
                await page.evaluate(js_func)
                await asyncio.sleep(0.5)
                return True
            except:
                continue
        
        return False
    
    async def download_data(self, data_type: str) -> Optional[pd.DataFrame]:
        """
        Download data for a specific data type
        
        Args:
            data_type: Type of data to download
            
        Returns:
            DataFrame with downloaded data or None
        """
        logger.info(f"=== {data_type} 데이터 수집 시작 ===")
        
        # 명세서배출량통계는 별도 처리 (연도별 다운로드 목록 형태)
        if data_type == "명세서배출량통계":
            df = await self._scrape_emission_statistics()
        else:
            # Use direct iframe access (more reliable)
            df = await self._direct_iframe_scrape(data_type)
        
        if df is not None and len(df) > 0:
            # Clean the data
            df = clean_excel_data(df)
            
            # Remove any completely empty columns
            df = df.dropna(axis=1, how='all')
            
            logger.info(f"데이터 수집 완료: {len(df)}행, {len(df.columns)}열")
            logger.info(f"컬럼: {list(df.columns)}")
            
            return df
        else:
            logger.error(f"데이터 수집 실패: {data_type}")
            return None
    
    async def _scrape_emission_statistics(self) -> Optional[pd.DataFrame]:
        """
        명세서배출량통계 전용 스크래퍼
        이 페이지는 연도별 다운로드 목록 형태로, 조회 버튼 클릭 후 목록 데이터를 스크래핑
        
        Returns:
            DataFrame with emission statistics
        """
        logger.info("명세서배출량통계 스크래핑 (조회 버튼 클릭 후 데이터 로드)")
        
        iframe_url = IFRAME_URLS.get("명세서배출량통계")
        new_page = await self.context.new_page()
        new_page.set_default_timeout(60000)
        
        try:
            await new_page.goto(iframe_url, wait_until='networkidle')
            await asyncio.sleep(3)
            
            # ★★★ 조회 버튼 클릭 ★★★
            await self._click_search_button(new_page, "명세서배출량통계")
            await asyncio.sleep(5)
            
            await self._save_debug("emission_stats_after_search", new_page)
            
            # WebSquare 그리드에서 데이터 추출 시도
            df = await self._extract_websquare_grid(new_page, "명세서배출량통계")
            if df is not None and len(df) > 0:
                return df
            
            # DOM에서 테이블 추출 시도
            df = await self._extract_table_dom(new_page, "명세서배출량통계")
            if df is not None and len(df) > 0:
                return df
            
            # 목록 형태 데이터 추출 (fallback)
            list_script = """
            () => {
                const results = [];
                const rows = document.querySelectorAll('table tbody tr, .w2grid tbody tr, .gridBodyTable tbody tr');
                
                rows.forEach((row, idx) => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const rowData = {};
                        cells.forEach((cell, cellIdx) => {
                            const nobr = cell.querySelector('nobr');
                            const value = nobr ? nobr.textContent.trim() : cell.textContent.trim();
                            rowData[`col_${cellIdx}`] = value;
                        });
                        
                        // Only add rows with actual data
                        if (Object.values(rowData).some(v => v && v.length > 0)) {
                            results.push(rowData);
                        }
                    }
                });
                
                return results;
            }
            """
            
            try:
                list_data = await new_page.evaluate(list_script)
                
                if list_data and len(list_data) > 0:
                    df = pd.DataFrame(list_data)
                    logger.info(f"목록 형태 데이터 추출: {len(df)}행")
                    return df
            except Exception as e:
                logger.debug(f"목록 추출 실패: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"명세서배출량통계 스크래핑 실패: {e}")
            return None
        finally:
            await new_page.close()
    
    async def download_all(self) -> Dict[str, pd.DataFrame]:
        """
        Download all data types
        
        Returns:
            Dictionary mapping data type to DataFrame
        """
        results = {}
        
        for data_type in ['할당대상업체', '목표관리대상업체', '명세서배출량통계']:
            try:
                df = await self.download_data(data_type)
                if df is not None and len(df) > 0:
                    results[data_type] = df
                    logger.info(f"✓ {data_type}: {len(df)}행 수집됨")
                else:
                    logger.warning(f"✗ {data_type}: 데이터 없음")
            except Exception as e:
                logger.error(f"✗ {data_type} 처리 중 오류: {e}")
        
        return results


async def run_scraper(debug_mode: bool = False) -> Dict[str, pd.DataFrame]:
    """
    Run the scraper and download all data
    
    Args:
        debug_mode: Enable debug mode
        
    Returns:
        Dictionary with downloaded data
    """
    scraper = NGMSScraper(debug_mode=debug_mode)
    
    try:
        await scraper.initialize()
        return await scraper.download_all()
    finally:
        await scraper.close()


if __name__ == "__main__":
    async def main():
        results = await run_scraper(debug_mode=True)
        for data_type, df in results.items():
            print(f"\n=== {data_type} ===")
            print(f"Shape: {df.shape}")
            print(f"Columns: {list(df.columns)}")
            print(df.head())
    
    asyncio.run(main())
