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
            await asyncio.sleep(5)  # Wait for grid to fully load
            
            await self._save_debug(f"direct_{data_type}", new_page)
            
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
                
                // Find grid component
                const gridElements = document.querySelectorAll('[id*="grd"], [id*="grid"], .w2grid');
                
                for (const gridEl of gridElements) {
                    const gridId = gridEl.id;
                    if (!gridId) continue;
                    
                    try {
                        const grid = WebSquare.util.getComponentById(gridId);
                        if (!grid) continue;
                        
                        // Try getAllJSON first
                        if (typeof grid.getAllJSON === 'function') {
                            const jsonStr = grid.getAllJSON();
                            const data = JSON.parse(jsonStr);
                            if (data && data.length > 0) {
                                return {success: true, data: data, method: 'getAllJSON', gridId: gridId};
                            }
                        }
                        
                        // Try getRowCount and getCellData
                        if (typeof grid.getRowCount === 'function') {
                            const rowCount = grid.getRowCount();
                            if (rowCount > 0) {
                                const data = [];
                                const colIds = grid.getColumnIDArray ? grid.getColumnIDArray() : [];
                                
                                for (let r = 0; r < rowCount; r++) {
                                    const row = {};
                                    if (colIds.length > 0) {
                                        for (const colId of colIds) {
                                            row[colId] = grid.getCellData(r, colId) || '';
                                        }
                                    } else {
                                        // Fallback to index-based
                                        for (let c = 0; c < 20; c++) {
                                            try {
                                                const val = grid.getCellData(r, c);
                                                if (val !== undefined) {
                                                    row['col_' + c] = val;
                                                }
                                            } catch(e) { break; }
                                        }
                                    }
                                    data.push(row);
                                }
                                
                                if (data.length > 0) {
                                    return {success: true, data: data, method: 'getCellData', gridId: gridId};
                                }
                            }
                        }
                    } catch(e) {
                        continue;
                    }
                }
                
                // Try through dataList
                const dataListIds = ['dataList1', 'dataList', 'dlt_list', 'dlt_data'];
                for (const dlId of dataListIds) {
                    try {
                        const dl = WebSquare.util.getComponentById(dlId);
                        if (dl && typeof dl.getAllJSON === 'function') {
                            const jsonStr = dl.getAllJSON();
                            const data = JSON.parse(jsonStr);
                            if (data && data.length > 0) {
                                return {success: true, data: data, method: 'dataList', dataListId: dlId};
                            }
                        }
                    } catch(e) {
                        continue;
                    }
                }
                
                return {error: 'No data found in grids'};
                
            } catch(e) {
                return {error: e.message};
            }
        }
        """
        
        try:
            result = await page.evaluate(script)
            
            if result and result.get('success') and result.get('data'):
                data = result['data']
                df = pd.DataFrame(data)
                logger.info(f"WebSquare 추출 성공 ({result.get('method')}): {len(df)}행")
                return df
            else:
                logger.debug(f"WebSquare 추출 실패: {result.get('error', 'unknown')}")
                
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
        current_page = 1
        max_pages = 50  # Safety limit
        
        while current_page <= max_pages:
            # Extract current page data
            script = """
            () => {
                const results = [];
                const rows = document.querySelectorAll('.gridBodyTable tbody tr, table[id*="grid"] tbody tr');
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    const rowData = [];
                    cells.forEach(cell => {
                        const nobr = cell.querySelector('nobr');
                        rowData.push(nobr ? nobr.textContent.trim() : cell.textContent.trim());
                    });
                    if (rowData.some(v => v && v.length > 0)) {
                        results.push(rowData);
                    }
                });
                
                return results;
            }
            """
            
            try:
                page_data = await page.evaluate(script)
                
                if page_data and len(page_data) > 0:
                    all_data.extend(page_data)
                    logger.debug(f"페이지 {current_page}: {len(page_data)}행")
                
                # Try to click next page
                next_clicked = False
                next_selectors = [
                    f'a[href*="goPage({current_page + 1})"]',
                    f'a:text("{current_page + 1}")',
                    'a.next',
                    'img[alt*="다음"]',
                ]
                
                for selector in next_selectors:
                    try:
                        next_btn = await page.query_selector(selector)
                        if next_btn:
                            await next_btn.click()
                            await asyncio.sleep(1)
                            next_clicked = True
                            break
                    except:
                        continue
                
                if not next_clicked:
                    break
                
                current_page += 1
                
            except Exception as e:
                logger.debug(f"페이지 {current_page} 추출 실패: {e}")
                break
        
        if all_data:
            # Get headers for column names
            header_script = """
            () => {
                const headers = [];
                document.querySelectorAll('.gridHeaderTable th nobr, thead th').forEach(el => {
                    headers.push(el.textContent.trim());
                });
                return headers;
            }
            """
            
            try:
                headers = await page.evaluate(header_script)
                if headers:
                    df = pd.DataFrame(all_data, columns=headers[:len(all_data[0])] if all_data else headers)
                else:
                    df = pd.DataFrame(all_data)
                
                logger.info(f"페이지네이션 추출 성공: {len(df)}행")
                return df
            except:
                df = pd.DataFrame(all_data)
                return df
        
        return None
    
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
        이 페이지는 연도별 다운로드 목록 형태로, 최신 연도의 업체배출량 데이터를 스크래핑
        
        Returns:
            DataFrame with emission statistics
        """
        logger.info("명세서배출량통계 스크래핑 (연도별 목록에서 최신 데이터)")
        
        iframe_url = IFRAME_URLS.get("명세서배출량통계")
        new_page = await self.context.new_page()
        new_page.set_default_timeout(60000)
        
        try:
            await new_page.goto(iframe_url, wait_until='networkidle')
            await asyncio.sleep(3)
            
            await self._save_debug("emission_stats_page", new_page)
            
            # 이 페이지에서 최신 연도의 "다운" 버튼을 찾아 클릭해서 데이터를 가져옴
            # 하지만 다운로드가 작동하지 않으므로, 테이블에서 데이터 추출 시도
            
            # 먼저 목록에서 연도 정보 추출
            year_list_script = """
            () => {
                const rows = document.querySelectorAll('table tbody tr, .listTable tbody tr');
                const years = [];
                
                rows.forEach(row => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length > 0) {
                        const yearText = cells[0].textContent.trim();
                        if (yearText && yearText.match(/\\d{4}/)) {
                            years.push(yearText);
                        }
                    }
                });
                
                return years;
            }
            """
            
            years = await new_page.evaluate(year_list_script)
            logger.info(f"발견된 연도: {years[:5]}...")
            
            # 목록 형태의 데이터 추출 (배출년도, 제목, 다운로드 가능 여부 등)
            list_script = """
            () => {
                const results = [];
                const rows = document.querySelectorAll('table tbody tr, .w2grid tbody tr');
                
                rows.forEach((row, idx) => {
                    const cells = row.querySelectorAll('td');
                    if (cells.length >= 2) {
                        const rowData = {
                            '배출년도': cells[0]?.textContent?.trim() || '',
                            '제목': cells[1]?.textContent?.trim() || '',
                        };
                        
                        // 다운로드 버튼 존재 여부 확인
                        for (let i = 2; i < cells.length; i++) {
                            const btn = cells[i].querySelector('a, button, input');
                            if (btn) {
                                const colName = `다운${i-1}`;
                                rowData[colName] = '가능';
                            }
                        }
                        
                        if (rowData['배출년도'] || rowData['제목']) {
                            results.push(rowData);
                        }
                    }
                });
                
                return results;
            }
            """
            
            list_data = await new_page.evaluate(list_script)
            
            if list_data and len(list_data) > 0:
                # 목록 형태 데이터 반환 (다운로드 불가 시 대안)
                df = pd.DataFrame(list_data)
                logger.info(f"목록 형태 데이터 추출: {len(df)}행")
                
                # Note: 실제 상세 배출량 데이터는 Excel 다운로드가 필요하지만
                # 다운로드가 작동하지 않아 목록만 반환
                logger.warning("명세서배출량통계: 상세 데이터는 Excel 다운로드 필요 (현재 미지원)")
                
                return df
            
            # Alternative: Try WebSquare grid extraction anyway
            df = await self._extract_websquare_grid(new_page, "명세서배출량통계")
            if df is not None and len(df) > 0:
                return df
            
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
