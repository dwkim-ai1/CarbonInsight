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
            viewport={'width': 1920, 'height': 1080},
            accept_downloads=True  # Excel 다운로드를 위해 필요
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
        subMain.do 접속 후 frame_locator로 테이블 스크래핑
        
        Args:
            data_type: Type of data
            
        Returns:
            DataFrame with scraped data
        """
        logger.info(f"테이블 스크래핑 시작: {data_type}")
        
        page_url = IFRAME_URLS.get(data_type)
        if not page_url:
            logger.error(f"URL 없음: {data_type}")
            return None
        
        new_page = await self.context.new_page()
        new_page.set_default_timeout(120000)  # 2분 타임아웃
        
        try:
            logger.info(f"URL 접속: {page_url}")
            
            # 재시도 로직
            for attempt in range(3):
                try:
                    await new_page.goto(page_url, wait_until='load', timeout=90000)
                    break
                except Exception as e:
                    if attempt < 2:
                        logger.warning(f"접속 시도 {attempt+1} 실패, 재시도: {e}")
                        await asyncio.sleep(5)
                    else:
                        try:
                            await new_page.goto(page_url, wait_until='domcontentloaded', timeout=90000)
                        except Exception as e2:
                            logger.error(f"모든 접속 시도 실패: {e2}")
                            raise
            
            # 페이지 완전 로드 대기
            await asyncio.sleep(5)
            
            # 페이지 로드 후 스크린샷
            await self._save_debug(f"scrape_01_page_loaded_{data_type}", new_page)
            
            # ★★★ frame_locator 사용 ★★★
            iframe_locator = new_page.frame_locator('iframe[src*="websquare"]')
            
            # ★★★ 검색 버튼 클릭 ★★★
            logger.info("검색 버튼 클릭 시도 (frame_locator)...")
            try:
                search_btn = iframe_locator.locator('input[value="검색"]')
                await search_btn.wait_for(timeout=10000)
                await search_btn.click()
                logger.info("검색 버튼 클릭 성공, 데이터 로딩 대기 (10초)...")
                await asyncio.sleep(10)  # 데이터 로딩 시간 증가
            except Exception as e:
                logger.warning(f"검색 버튼 클릭 실패: {e}")
            
            # 검색 후 스크린샷
            await self._save_debug(f"scrape_02_after_search_{data_type}", new_page)
            
            # ★★★ 테이블 데이터 추출 (frame_locator 사용) ★★★
            df = await self._extract_table_with_locator(iframe_locator, data_type)
            
            if df is not None and len(df) > 0:
                logger.info(f"테이블 추출 성공: {len(df)}행")
                return df
            
            # 추출 실패 시 디버그
            await self._save_debug(f"scrape_03_extraction_failed_{data_type}", new_page)
            
            return None
            
        except Exception as e:
            logger.error(f"스크래핑 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
        finally:
            await new_page.close()
    
    async def _extract_table_with_locator(self, iframe_locator, data_type: str) -> Optional[pd.DataFrame]:
        """
        frame_locator를 사용하여 테이블 데이터 추출 (스크롤하여 전체 데이터 수집)
        """
        logger.info("frame_locator로 테이블 추출 시도 (스크롤 방식)...")
        
        try:
            # 헤더 추출
            headers = []
            header_cells = iframe_locator.locator('.gridHeaderTable th nobr, .gridHeaderTable th')
            header_count = await header_cells.count()
            logger.info(f"헤더 셀 수: {header_count}")
            
            for i in range(header_count):
                try:
                    text = await header_cells.nth(i).inner_text()
                    text = text.strip()
                    if text and text not in headers:
                        headers.append(text)
                except:
                    continue
            
            logger.info(f"추출된 헤더: {headers[:7]}...")  # 처음 7개만 표시
            
            # ★★★ DOM 구조 디버깅 ★★★
            logger.info("DOM 구조 분석 중...")
            try:
                # 모든 테이블 확인
                all_tables = iframe_locator.locator('table')
                table_count = await all_tables.count()
                logger.info(f"iframe 내 테이블 수: {table_count}")
                
                # 각 테이블의 ID와 행 수 확인
                for i in range(min(table_count, 5)):  # 최대 5개
                    try:
                        table = all_tables.nth(i)
                        table_id = await table.get_attribute('id') or f'table_{i}'
                        tbody_rows = table.locator('tbody tr')
                        row_count = await tbody_rows.count()
                        logger.info(f"  테이블 '{table_id}': {row_count}개 행")
                    except:
                        pass
            except Exception as e:
                logger.debug(f"DOM 분석 실패: {e}")
            
            # ★★★ 여러 행 선택자 시도 ★★★
            row_selectors = [
                'table[id*="body_table"] tbody tr',
                'table[id*="grd1"] tbody tr',
                'table[id*="grd"] tbody tr',
                '.gridBodyTable tbody tr',
                '.w2grid tbody tr',
                'table tbody tr',
            ]
            
            working_selector = None
            for selector in row_selectors:
                try:
                    rows = iframe_locator.locator(selector)
                    count = await rows.count()
                    logger.info(f"선택자 '{selector}': {count}개 행")
                    if count > 0:
                        working_selector = selector
                        break
                except Exception as e:
                    logger.debug(f"선택자 '{selector}' 실패: {e}")
                    continue
            
            if not working_selector:
                logger.warning("작동하는 행 선택자를 찾을 수 없음")
                return None
            
            logger.info(f"사용할 선택자: {working_selector}")
            
            # ★★★ 스크롤하면서 전체 데이터 수집 ★★★
            all_rows_data = []
            seen_first_cells = set()  # 중복 체크용
            
            # 그리드 컨테이너 찾기
            grid_container = iframe_locator.locator('.w2grid_dataLayer, .w2grid_main, [id*="grd"]').first
            
            # ★★★ 먼저 스크롤바/그리드 영역 찾기 ★★★
            scroll_area = iframe_locator.locator('.w2grid_scrollY_div, [id*="scrollY"], .w2grid')
            
            max_scroll_attempts = 50  # 최대 스크롤 횟수
            scroll_count = 0
            no_new_rows_count = 0
            
            while scroll_count < max_scroll_attempts:
                # 현재 보이는 행 추출
                if working_selector:
                    rows = iframe_locator.locator(working_selector)
                else:
                    rows = iframe_locator.locator('tbody tr')
                current_row_count = await rows.count()
                
                # ★★★ 행이 없어도 스크롤 시도 (처음 몇 번) ★★★
                if current_row_count == 0:
                    if scroll_count < 5:  # 처음 5번은 스크롤 시도
                        logger.info(f"스크롤 {scroll_count + 1}: 행 없음, 스크롤 시도 중...")
                        await self._scroll_grid(iframe_locator, grid_container)
                        await asyncio.sleep(1)
                        scroll_count += 1
                        continue
                    else:
                        logger.warning(f"스크롤 {scroll_count}: 행을 찾을 수 없음, 종료")
                        break
                
                new_rows_added = 0
                
                for i in range(current_row_count):
                    try:
                        row = rows.nth(i)
                        cells = row.locator('td')
                        cell_count = await cells.count()
                        
                        if cell_count == 0:
                            continue
                        
                        row_data = []
                        for j in range(cell_count):
                            try:
                                # nobr 태그 우선 시도
                                nobr = cells.nth(j).locator('nobr')
                                if await nobr.count() > 0:
                                    text = await nobr.first.inner_text()
                                else:
                                    text = await cells.nth(j).inner_text()
                                row_data.append(text.strip())
                            except:
                                row_data.append('')
                        
                        # 빈 행 제외
                        if not any(cell for cell in row_data):
                            continue
                        
                        # 중복 체크 (첫 번째 + 두 번째 셀 조합)
                        row_key = f"{row_data[0]}_{row_data[1] if len(row_data) > 1 else ''}"
                        
                        if row_key not in seen_first_cells:
                            seen_first_cells.add(row_key)
                            all_rows_data.append(row_data)
                            new_rows_added += 1
                            
                    except Exception as e:
                        logger.debug(f"행 추출 실패: {e}")
                        continue
                
                logger.info(f"스크롤 {scroll_count + 1}: 현재 {current_row_count}행 표시, 신규 {new_rows_added}행, 총 {len(all_rows_data)}행")
                
                # 새 행이 없으면 종료 체크
                if new_rows_added == 0:
                    no_new_rows_count += 1
                    if no_new_rows_count >= 3:  # 연속 3번 새 행 없으면 종료
                        logger.info("더 이상 새로운 행 없음, 스크롤 종료")
                        break
                else:
                    no_new_rows_count = 0
                
                # ★★★ 다양한 스크롤 방식 시도 ★★★
                scroll_success = await self._scroll_grid(iframe_locator, grid_container)
                
                if scroll_success:
                    await asyncio.sleep(2)  # 데이터 로드 대기 (2초)
                else:
                    await asyncio.sleep(1)
                    
                scroll_count += 1
            
            logger.info(f"스크롤 완료: 총 {len(all_rows_data)}행 추출")
            
            if all_rows_data:
                # 컬럼 수 맞추기
                max_cols = max(len(row) for row in all_rows_data)
                for row in all_rows_data:
                    while len(row) < max_cols:
                        row.append('')
                
                if headers and len(headers) >= max_cols:
                    df = pd.DataFrame(all_rows_data, columns=headers[:max_cols])
                else:
                    df = pd.DataFrame(all_rows_data)
                
                return df
            
            return None
            
        except Exception as e:
            logger.error(f"테이블 추출 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    async def _scroll_grid(self, iframe_locator, grid_container) -> bool:
        """
        다양한 방식으로 그리드 스크롤 시도
        Returns: 스크롤 성공 여부
        """
        # ★★★ 방법 1: 테이블 영역에 포커스 후 PageDown ★★★
        try:
            # 테이블 영역 클릭하여 포커스
            table_area = iframe_locator.locator('table[id*="body_table"], .gridBodyTable, .w2grid').first
            await table_area.click()
            await asyncio.sleep(0.2)
            
            # PageDown 여러 번
            for _ in range(3):
                await iframe_locator.locator('body').press('PageDown')
                await asyncio.sleep(0.1)
            
            logger.debug("스크롤 방식 'PageDown' 성공")
            return True
        except Exception as e:
            logger.debug(f"PageDown 스크롤 실패: {e}")
        
        # ★★★ 방법 2: 마지막 행 클릭 후 ArrowDown ★★★
        try:
            last_row = iframe_locator.locator('tbody tr').last
            await last_row.click()
            await asyncio.sleep(0.2)
            
            for _ in range(10):
                await iframe_locator.locator('body').press('ArrowDown')
                await asyncio.sleep(0.05)
            
            logger.debug("스크롤 방식 'ArrowDown' 성공")
            return True
        except Exception as e:
            logger.debug(f"ArrowDown 스크롤 실패: {e}")
        
        # ★★★ 방법 3: JavaScript scrollIntoView ★★★
        try:
            await iframe_locator.locator('tbody tr').last.evaluate('el => el.scrollIntoView({behavior: "smooth", block: "end"})')
            logger.debug("스크롤 방식 'scrollIntoView' 성공")
            return True
        except Exception as e:
            logger.debug(f"scrollIntoView 스크롤 실패: {e}")
        
        # ★★★ 방법 4: 스크롤바 영역 클릭 ★★★
        try:
            scrollbar = iframe_locator.locator('.w2grid_scrollY_div, [id*="scrollY_div"]').first
            box = await scrollbar.bounding_box()
            if box:
                # 스크롤바 하단 클릭
                await scrollbar.click(position={'x': box['width'] / 2, 'y': box['height'] * 0.8})
                logger.debug("스크롤 방식 'scrollbar click' 성공")
                return True
        except Exception as e:
            logger.debug(f"scrollbar 스크롤 실패: {e}")
        
        # ★★★ 방법 5: JavaScript scrollTop ★★★
        try:
            await grid_container.evaluate('el => { el.scrollTop += 500; }')
            logger.debug("스크롤 방식 'scrollTop' 성공")
            return True
        except Exception as e:
            logger.debug(f"scrollTop 스크롤 실패: {e}")
        
        logger.warning("모든 스크롤 방식 실패")
        return False
    
    async def _extract_from_iframe_websquare(self, frame, data_type: str) -> Optional[pd.DataFrame]:
        """iframe 내에서 WebSquare API로 데이터 추출"""
        script = """
        async () => {
            const result = {success: false, data: null, error: null, debug: {}};
            
            try {
                // WebSquare 존재 확인
                result.debug.hasWebSquare = typeof WebSquare !== 'undefined';
                
                if (typeof WebSquare === 'undefined') {
                    result.error = 'WebSquare not found';
                    return result;
                }
                
                // 그리드 찾기
                const gridIds = ['grd1', 'mf_grd1', 'grid1', 'mf_grid1'];
                let grid = null;
                
                for (const id of gridIds) {
                    try {
                        const g = WebSquare.util.getComponentById(id);
                        if (g && typeof g.getRowCount === 'function') {
                            const count = g.getRowCount();
                            result.debug[id] = count;
                            if (count > 0) {
                                grid = g;
                                result.debug.foundGrid = id;
                                break;
                            }
                        }
                    } catch(e) {}
                }
                
                if (!grid) {
                    result.error = 'No grid with data found';
                    return result;
                }
                
                // 데이터 추출
                const rowCount = grid.getRowCount();
                result.debug.rowCount = rowCount;
                
                // getAllJSON 시도
                if (typeof grid.getAllJSON === 'function') {
                    try {
                        const jsonStr = grid.getAllJSON();
                        const data = JSON.parse(jsonStr);
                        if (data && data.length > 0) {
                            result.success = true;
                            result.data = data;
                            return result;
                        }
                    } catch(e) {
                        result.debug.getAllJSONError = e.message;
                    }
                }
                
                // getCellData 시도
                if (typeof grid.getCellData === 'function') {
                    const data = [];
                    let colIds = [];
                    
                    // 컬럼 ID 가져오기
                    if (grid.getColumnIDArray) {
                        colIds = grid.getColumnIDArray();
                    }
                    if (colIds.length === 0) {
                        for (let c = 0; c < 20; c++) {
                            try {
                                const id = grid.getColumnID ? grid.getColumnID(c) : null;
                                if (id) colIds.push(id);
                                else break;
                            } catch(e) { break; }
                        }
                    }
                    
                    result.debug.colCount = colIds.length;
                    
                    for (let r = 0; r < rowCount; r++) {
                        const row = {};
                        for (const colId of colIds) {
                            try {
                                row[colId] = grid.getCellData(r, colId) || '';
                            } catch(e) {}
                        }
                        data.push(row);
                    }
                    
                    if (data.length > 0) {
                        result.success = true;
                        result.data = data;
                        return result;
                    }
                }
                
                result.error = 'Failed to extract data';
                return result;
                
            } catch(e) {
                result.error = e.message;
                return result;
            }
        }
        """
        
        try:
            result = await frame.evaluate(script)
            
            if result is None:
                logger.warning("WebSquare evaluate 결과가 None")
                return None
            
            logger.info(f"WebSquare 결과: success={result.get('success')}, debug={result.get('debug', {})}")
            
            if result.get('error'):
                logger.warning(f"WebSquare 오류: {result.get('error')}")
            
            if result.get('success') and result.get('data'):
                df = pd.DataFrame(result['data'])
                return df
                
        except Exception as e:
            logger.error(f"WebSquare 추출 실패: {e}")
        
        return None
    
    async def _extract_from_iframe_dom(self, frame, data_type: str) -> Optional[pd.DataFrame]:
        """iframe 내에서 DOM 테이블 추출"""
        script = """
        () => {
            const result = {headers: [], rows: [], debug: {}};
            
            // 헤더 추출
            const headerSelectors = [
                '.gridHeaderTable th nobr',
                '.gridHeaderTable th',
                'thead th'
            ];
            
            for (const sel of headerSelectors) {
                const elements = document.querySelectorAll(sel);
                result.debug[`header_${sel}`] = elements.length;
                
                elements.forEach(el => {
                    const text = el.textContent.trim();
                    if (text && !result.headers.includes(text)) {
                        result.headers.push(text);
                    }
                });
                
                if (result.headers.length > 0) break;
            }
            
            // 행 추출
            const rowSelectors = [
                '.gridBodyTable tbody tr',
                'table[id*="grd"] tbody tr',
                '.w2grid tbody tr',
                'tbody tr'
            ];
            
            for (const sel of rowSelectors) {
                const rows = document.querySelectorAll(sel);
                result.debug[`rows_${sel}`] = rows.length;
                
                if (rows.length > 0) {
                    rows.forEach(row => {
                        const cells = row.querySelectorAll('td');
                        const rowData = [];
                        
                        cells.forEach(cell => {
                            const nobr = cell.querySelector('nobr');
                            const text = nobr ? nobr.textContent.trim() : cell.textContent.trim();
                            rowData.push(text);
                        });
                        
                        // 빈 행 제외
                        if (rowData.some(v => v && v.length > 0)) {
                            result.rows.push(rowData);
                        }
                    });
                    
                    if (result.rows.length > 0) break;
                }
            }
            
            result.debug.finalHeaders = result.headers.length;
            result.debug.finalRows = result.rows.length;
            
            return result;
        }
        """
        
        try:
            result = await frame.evaluate(script)
            
            if result is None:
                logger.warning("DOM evaluate 결과가 None")
                return None
            
            logger.info(f"DOM 결과: headers={len(result.get('headers', []))}, rows={len(result.get('rows', []))}, debug={result.get('debug', {})}")
            
            rows = result.get('rows', [])
            if rows and len(rows) > 0:
                headers = result.get('headers', [])
                
                if headers and len(headers) >= len(rows[0]):
                    df = pd.DataFrame(rows, columns=headers[:len(rows[0])])
                else:
                    df = pd.DataFrame(rows)
                
                return df
                
        except Exception as e:
            logger.error(f"DOM 추출 실패: {e}")
        
        return None
    
    async def _log_iframe_structure(self, frame, data_type: str) -> None:
        """iframe 구조 로깅"""
        try:
            script = """
            () => {
                return {
                    url: window.location.href,
                    hasWebSquare: typeof WebSquare !== 'undefined',
                    tables: document.querySelectorAll('table').length,
                    grids: document.querySelectorAll('[id*="grd"]').length,
                    tbodyRows: document.querySelectorAll('tbody tr').length,
                    buttons: Array.from(document.querySelectorAll('input[type="button"]'))
                        .slice(0, 5)
                        .map(b => ({id: b.id, value: b.value}))
                };
            }
            """
            
            info = await frame.evaluate(script)
            logger.info(f"iframe 구조 ({data_type}): {info}")
            
        except Exception as e:
            logger.debug(f"iframe 구조 로깅 실패: {e}")
    
    async def _find_content_frame(self, page: Page, data_type: str) -> Optional[Frame]:
        """
        Find the iframe containing the actual content
        
        Args:
            page: Main page
            data_type: Data type for logging
            
        Returns:
            Frame object or None
        """
        logger.info("iframe 검색 중...")
        
        # iframe이 로드될 때까지 대기
        await asyncio.sleep(2)
        
        # 모든 frame 확인
        frames = page.frames
        logger.info(f"발견된 frame 수: {len(frames)}")
        
        target_frame = None
        
        for i, frame in enumerate(frames):
            frame_url = frame.url
            frame_name = frame.name
            logger.debug(f"Frame {i}: name='{frame_name}', url='{frame_url[:80] if frame_url else 'N/A'}...'")
            
            # WebSquare 컨텐츠가 있는 iframe 찾기
            if 'websquare' in frame_url.lower() or 'ngms.do' in frame_url.lower():
                logger.info(f"WebSquare iframe 발견: {frame_url[:100]}")
                target_frame = frame
                break
            
            # tac_layout_contents iframe 찾기 (탭 컨텐츠)
            if 'tac_layout_contents' in frame_name or 'body' in frame_name:
                logger.info(f"컨텐츠 iframe 발견 (name): {frame_name}")
                target_frame = frame
                break
        
        # 첫 번째 자식 frame이 있으면 반환 (메인 페이지 제외)
        if target_frame is None and len(frames) > 1:
            logger.info("첫 번째 자식 frame 사용")
            target_frame = frames[1]
        
        # frame이 로드되었는지 확인
        if target_frame:
            try:
                # frame 컨텍스트 준비 대기
                await asyncio.sleep(1)
                
                # 간단한 테스트로 frame 접근 가능 여부 확인
                test_result = await target_frame.evaluate('() => document.readyState')
                logger.info(f"Frame readyState: {test_result}")
                
                if test_result == 'complete':
                    return target_frame
                else:
                    # 추가 대기
                    await asyncio.sleep(3)
                    return target_frame
                    
            except Exception as e:
                logger.warning(f"Frame 접근 테스트 실패: {e}")
                # 실패해도 frame 반환 (evaluate 시 다시 시도)
                return target_frame
        
        return None
        
        return None
    
    async def _click_search_button_in_frame(self, frame, data_type: str) -> bool:
        """
        Click search button in frame (Page or Frame object)
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
            '#mf_trigger1',
            'input[type="button"][value*="검색"]',
            'input[type="button"][value*="조회"]',
        ]
        
        clicked = False
        for selector in search_selectors:
            try:
                # Frame에서 요소 찾기
                button = await frame.query_selector(selector)
                if button:
                    logger.info(f"검색 버튼 발견: {selector}")
                    
                    # 버튼 정보 로깅
                    try:
                        btn_id = await button.get_attribute('id') or 'N/A'
                        logger.info(f"버튼 ID: {btn_id}")
                    except:
                        pass
                    
                    # 클릭 시도
                    try:
                        await button.click(force=True)
                        logger.info("버튼 클릭 완료")
                        clicked = True
                    except Exception as e:
                        logger.debug(f"click 실패: {e}")
                        # JavaScript로 클릭
                        try:
                            await button.evaluate('el => el.click()')
                            logger.info("JS click 완료")
                            clicked = True
                        except Exception as e2:
                            logger.debug(f"JS click 실패: {e2}")
                    
                    if clicked:
                        break
                        
            except Exception as e:
                logger.debug(f"버튼 처리 실패 ({selector}): {e}")
                continue
        
        # JavaScript로 검색 함수 직접 호출 (WebSquare)
        if not clicked:
            js_functions = [
                'scwin.btn_search_onclick()',
                'scwin.fn_search()',
                'fn_search()',
            ]
            
            for js_func in js_functions:
                try:
                    await frame.evaluate(js_func)
                    logger.info(f"JS 함수 호출 성공: {js_func}")
                    clicked = True
                    break
                except Exception as e:
                    logger.debug(f"JS 함수 실패 ({js_func}): {e}")
        
        if not clicked:
            logger.warning("검색 버튼을 찾거나 클릭할 수 없습니다")
            return False
        
        # 데이터 로딩 대기
        logger.info("검색 버튼 클릭 완료, 데이터 로딩 대기...")
        await asyncio.sleep(2)
        
        # 네트워크 idle 대기 (Page 객체인 경우에만)
        if hasattr(frame, 'wait_for_load_state'):
            try:
                await frame.wait_for_load_state('networkidle', timeout=30000)
            except:
                pass
        
        # 그리드 데이터 로딩 대기
        data_loaded = await self._wait_for_grid_data_in_frame(frame, timeout=30)
        
        if data_loaded:
            logger.info("데이터 로딩 완료 확인됨")
        else:
            logger.warning("데이터 로딩 확인 실패 - 추가 대기 후 진행")
            await asyncio.sleep(5)
        
        return True
    
    async def _wait_for_grid_data_in_frame(self, frame, timeout: int = 30) -> bool:
        """Wait for grid data in frame"""
        logger.info(f"그리드 데이터 로딩 대기 (최대 {timeout}초)")
        
        check_script = """
        () => {
            const result = {loaded: false, debug: {}};
            
            result.debug.hasWebSquare = typeof WebSquare !== 'undefined';
            
            if (typeof WebSquare !== 'undefined') {
                const gridIds = ['grd1', 'mf_grd1', 'grid1', 'grdList'];
                for (const id of gridIds) {
                    try {
                        const grid = WebSquare.util.getComponentById(id);
                        if (grid && typeof grid.getRowCount === 'function') {
                            const count = grid.getRowCount();
                            result.debug[`grid_${id}`] = count;
                            if (count > 0) {
                                result.loaded = true;
                                result.method = 'websquare';
                                result.count = count;
                                return result;
                            }
                        }
                    } catch(e) {}
                }
            }
            
            // DOM check
            const rows = document.querySelectorAll('.gridBodyTable tbody tr, table tbody tr');
            result.debug.domRows = rows.length;
            
            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                for (const cell of cells) {
                    const text = cell.textContent.trim();
                    if (text && text.length > 0 && text !== '-') {
                        result.loaded = true;
                        result.method = 'dom';
                        result.count = rows.length;
                        return result;
                    }
                }
            }
            
            return result;
        }
        """
        
        start_time = asyncio.get_event_loop().time()
        check_count = 0
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                result = await frame.evaluate(check_script)
                check_count += 1
                
                # None 체크
                if result is None:
                    logger.debug(f"체크 #{check_count}: evaluate 결과 None")
                    await asyncio.sleep(1)
                    continue
                
                if check_count % 5 == 1:
                    logger.info(f"로딩 체크 #{check_count}: {result.get('debug', {})}")
                
                if result.get('loaded'):
                    logger.info(f"데이터 감지: method={result.get('method')}, count={result.get('count')}")
                    return True
                    
            except Exception as e:
                logger.debug(f"체크 실패: {e}")
            
            await asyncio.sleep(1)
        
        logger.warning(f"로딩 타임아웃 ({timeout}초)")
        return False
    
    async def _log_frame_structure(self, frame, data_type: str) -> None:
        """Log frame structure for debugging"""
        try:
            debug_script = """
            () => {
                return {
                    url: window.location.href,
                    hasWebSquare: typeof WebSquare !== 'undefined',
                    tables: document.querySelectorAll('table').length,
                    grids: document.querySelectorAll('[id*="grd"], .w2grid').length,
                    buttons: Array.from(document.querySelectorAll('input[type="button"]')).slice(0, 5).map(b => ({id: b.id, value: b.value}))
                };
            }
            """
            
            info = await frame.evaluate(debug_script)
            logger.info(f"Frame 구조 ({data_type}): {info}")
            
        except Exception as e:
            logger.debug(f"Frame 구조 로깅 실패: {e}")
    
    async def _log_page_structure(self, page: Page, data_type: str) -> None:
        """Log page structure for debugging"""
        try:
            debug_script = """
            () => {
                const info = {
                    url: window.location.href,
                    title: document.title,
                    hasWebSquare: typeof WebSquare !== 'undefined',
                    tables: [],
                    grids: [],
                    buttons: []
                };
                
                // Find tables
                document.querySelectorAll('table').forEach((t, i) => {
                    if (i < 5) {
                        info.tables.push({
                            index: i,
                            id: t.id,
                            className: t.className.substring(0, 50),
                            rows: t.querySelectorAll('tr').length
                        });
                    }
                });
                
                // Find grids
                document.querySelectorAll('[id*="grd"], [id*="grid"], .w2grid').forEach((g, i) => {
                    if (i < 5) {
                        info.grids.push({
                            index: i,
                            id: g.id,
                            className: g.className.substring(0, 50)
                        });
                    }
                });
                
                // Find buttons
                document.querySelectorAll('input[type="button"], button').forEach((b, i) => {
                    if (i < 10) {
                        info.buttons.push({
                            index: i,
                            id: b.id,
                            value: b.value || b.textContent?.trim().substring(0, 20),
                            type: b.type
                        });
                    }
                });
                
                // Check WebSquare components
                if (typeof WebSquare !== 'undefined') {
                    try {
                        const gridIds = ['grd1', 'mf_grd1', 'grid1'];
                        info.wsGrids = [];
                        for (const id of gridIds) {
                            try {
                                const g = WebSquare.util.getComponentById(id);
                                if (g) {
                                    info.wsGrids.push({
                                        id: id,
                                        type: g.getType ? g.getType() : 'unknown',
                                        rowCount: g.getRowCount ? g.getRowCount() : 'N/A'
                                    });
                                }
                            } catch(e) {}
                        }
                    } catch(e) {}
                }
                
                return info;
            }
            """
            
            info = await page.evaluate(debug_script)
            logger.info(f"페이지 구조 ({data_type}):")
            logger.info(f"  - URL: {info.get('url', 'N/A')}")
            logger.info(f"  - WebSquare: {info.get('hasWebSquare', False)}")
            logger.info(f"  - Tables: {info.get('tables', [])}")
            logger.info(f"  - Grids: {info.get('grids', [])}")
            logger.info(f"  - WS Grids: {info.get('wsGrids', [])}")
            logger.info(f"  - Buttons: {info.get('buttons', [])[:5]}")
            
        except Exception as e:
            logger.debug(f"페이지 구조 로깅 실패: {e}")
    
    async def _extract_websquare_grid_from_frame(self, frame, data_type: str) -> Optional[pd.DataFrame]:
        """Extract data using WebSquare grid API from frame"""
        logger.info("WebSquare 그리드 API 추출 시도 (frame)")
        
        script = """
        async () => {
            const result = {success: false, error: null, debug: {}};
            
            try {
                await new Promise(r => setTimeout(r, 1000));
                
                result.debug.hasWebSquare = typeof WebSquare !== 'undefined';
                
                if (typeof WebSquare === 'undefined') {
                    result.error = 'WebSquare not found';
                    return result;
                }
                
                const gridIdPatterns = ['grd1', 'mf_grd1', 'grid1', 'mf_grid1', 'grdList'];
                let grid = null;
                let gridId = null;
                
                for (const pattern of gridIdPatterns) {
                    try {
                        const g = WebSquare.util.getComponentById(pattern);
                        if (g && typeof g.getRowCount === 'function') {
                            const count = g.getRowCount();
                            result.debug[pattern] = count;
                            if (count > 0) {
                                grid = g;
                                gridId = pattern;
                                break;
                            }
                        }
                    } catch(e) {}
                }
                
                if (!grid) {
                    result.error = 'Grid not found or empty';
                    return result;
                }
                
                result.debug.gridId = gridId;
                result.debug.rowCount = grid.getRowCount();
                
                // Try getAllJSON
                if (typeof grid.getAllJSON === 'function') {
                    try {
                        const jsonStr = grid.getAllJSON();
                        const data = JSON.parse(jsonStr);
                        if (data && data.length > 0) {
                            result.success = true;
                            result.data = data;
                            result.method = 'getAllJSON';
                            return result;
                        }
                    } catch(e) {
                        result.debug.getAllJSONError = e.message;
                    }
                }
                
                // Try getCellData
                if (typeof grid.getCellData === 'function') {
                    const rowCount = grid.getRowCount();
                    const data = [];
                    let colIds = grid.getColumnIDArray ? grid.getColumnIDArray() : [];
                    
                    if (colIds.length === 0) {
                        for (let c = 0; c < 20; c++) {
                            try {
                                const id = grid.getColumnID ? grid.getColumnID(c) : `col_${c}`;
                                if (id) colIds.push(id);
                                else break;
                            } catch(e) { break; }
                        }
                    }
                    
                    for (let r = 0; r < rowCount; r++) {
                        const row = {};
                        for (const colId of colIds) {
                            try {
                                row[colId] = grid.getCellData(r, colId) || '';
                            } catch(e) {
                                row[colId] = '';
                            }
                        }
                        data.push(row);
                    }
                    
                    if (data.length > 0) {
                        result.success = true;
                        result.data = data;
                        result.method = 'getCellData';
                        return result;
                    }
                }
                
                result.error = 'Failed to extract';
                return result;
                
            } catch(e) {
                result.error = e.message;
                return result;
            }
        }
        """
        
        try:
            result = await frame.evaluate(script)
            
            # None 체크 추가
            if result is None:
                logger.warning("WebSquare evaluate 결과가 None")
                return None
            
            logger.info(f"WebSquare 결과: success={result.get('success', False)}, debug={result.get('debug', {})}")
            
            if result.get('error'):
                logger.warning(f"WebSquare 오류: {result.get('error')}")
            
            if result.get('success') and result.get('data'):
                df = pd.DataFrame(result['data'])
                logger.info(f"WebSquare 추출 성공: {len(df)}행")
                return df
                
        except Exception as e:
            logger.error(f"WebSquare API 실패: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        return None
    
    async def _extract_table_dom_from_frame(self, frame, data_type: str) -> Optional[pd.DataFrame]:
        """Extract table data from DOM in frame"""
        logger.info("DOM 테이블 추출 시도 (frame)")
        
        script = """
        () => {
            const result = {headers: [], rows: [], debug: {}};
            
            // Get headers
            const headerSelectors = [
                '.gridHeaderTable th nobr',
                '.gridHeaderTable th',
                'thead th nobr',
                'thead th'
            ];
            
            for (const sel of headerSelectors) {
                document.querySelectorAll(sel).forEach(el => {
                    const text = el.textContent.trim();
                    if (text && !result.headers.includes(text)) {
                        result.headers.push(text);
                    }
                });
                if (result.headers.length > 0) break;
            }
            
            result.debug.headerCount = result.headers.length;
            
            // Get rows
            const rowSelectors = [
                '.gridBodyTable tbody tr',
                'table[id*="grd"] tbody tr',
                '.w2grid tbody tr'
            ];
            
            for (const sel of rowSelectors) {
                const rows = document.querySelectorAll(sel);
                result.debug[sel] = rows.length;
                
                if (rows.length > 0) {
                    rows.forEach(row => {
                        const cells = row.querySelectorAll('td');
                        const rowData = [];
                        
                        cells.forEach(cell => {
                            const nobr = cell.querySelector('nobr');
                            const div = cell.querySelector('div');
                            let value = '';
                            
                            if (nobr) value = nobr.textContent.trim();
                            else if (div) value = div.textContent.trim();
                            else value = cell.textContent.trim();
                            
                            rowData.push(value);
                        });
                        
                        if (rowData.some(v => v && v.length > 0)) {
                            result.rows.push(rowData);
                        }
                    });
                    
                    if (result.rows.length > 0) break;
                }
            }
            
            result.debug.rowCount = result.rows.length;
            return result;
        }
        """
        
        try:
            result = await frame.evaluate(script)
            
            # None 체크 추가
            if result is None:
                logger.warning("DOM evaluate 결과가 None")
                return None
            
            headers = result.get('headers', [])
            rows = result.get('rows', [])
            debug = result.get('debug', {})
            
            logger.info(f"DOM 추출 결과: headers={len(headers)}, rows={len(rows)}, debug={debug}")
            
            if rows and len(rows) > 0:
                if headers and len(headers) >= len(rows[0]):
                    df = pd.DataFrame(rows, columns=headers[:len(rows[0])])
                else:
                    df = pd.DataFrame(rows)
                
                logger.info(f"DOM 추출 성공: {len(df)}행")
                return df
                
        except Exception as e:
            logger.error(f"DOM 추출 실패: {e}")
            import traceback
            logger.debug(traceback.format_exc())
        
        return None
    
    async def _extract_with_pagination_from_frame(self, frame, data_type: str) -> Optional[pd.DataFrame]:
        """Extract data with pagination from frame"""
        logger.info("페이지네이션 추출 시도 (frame)")
        
        # 현재 페이지 데이터 추출
        df = await self._extract_table_dom_from_frame(frame, data_type)
        
        if df is None or len(df) == 0:
            return None
        
        all_data = [df]
        current_page = 1
        max_pages = 100
        
        while current_page < max_pages:
            # 다음 페이지 클릭 시도
            next_clicked = False
            next_page = current_page + 1
            
            try:
                # 페이지 번호 클릭
                page_link = await frame.query_selector(f'a:has-text("{next_page}")')
                if page_link:
                    await page_link.click()
                    await asyncio.sleep(2)
                    next_clicked = True
                else:
                    # 다음 버튼 클릭
                    next_btn = await frame.query_selector('a[title="다음"], img[alt*="다음"]')
                    if next_btn:
                        await next_btn.click()
                        await asyncio.sleep(2)
                        next_clicked = True
            except:
                pass
            
            if not next_clicked:
                break
            
            # 새 페이지 데이터 추출
            page_df = await self._extract_table_dom_from_frame(frame, data_type)
            
            if page_df is None or len(page_df) == 0:
                break
            
            # 중복 체크 (첫 행 비교)
            if len(all_data) > 0:
                first_row = page_df.iloc[0].tolist()
                last_first_row = all_data[-1].iloc[0].tolist()
                if first_row == last_first_row:
                    break
            
            all_data.append(page_df)
            current_page = next_page
            logger.info(f"페이지 {current_page}: {len(page_df)}행 추가")
        
        if len(all_data) > 1:
            result = pd.concat(all_data, ignore_index=True)
            logger.info(f"페이지네이션 추출 완료: 총 {len(result)}행, {len(all_data)}페이지")
            return result
        
        return df if df is not None and len(df) > 0 else None
    
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
            '#mf_trigger1',
            'input[type="button"][value*="검색"]',
            'input[type="button"][value*="조회"]',
        ]
        
        clicked = False
        for selector in search_selectors:
            try:
                button = await page.query_selector(selector)
                if button:
                    logger.info(f"검색 버튼 발견: {selector}")
                    
                    # 버튼 정보 로깅
                    btn_id = await button.get_attribute('id') or 'N/A'
                    btn_onclick = await button.get_attribute('onclick') or 'N/A'
                    logger.info(f"버튼 정보: id={btn_id}, onclick={btn_onclick[:50] if btn_onclick else 'N/A'}")
                    
                    # ★ 방법 1: Playwright click (force=True로 강제 클릭)
                    try:
                        await button.click(force=True)
                        logger.info("Playwright force click 완료")
                        clicked = True
                    except Exception as e:
                        logger.debug(f"Playwright click 실패: {e}")
                    
                    # ★ 방법 2: JavaScript로 클릭 이벤트 발생
                    if not clicked:
                        try:
                            await button.evaluate('el => el.click()')
                            logger.info("JS el.click() 완료")
                            clicked = True
                        except Exception as e:
                            logger.debug(f"JS click 실패: {e}")
                    
                    # ★ 방법 3: dispatchEvent로 클릭 이벤트 발생
                    if not clicked:
                        try:
                            await button.evaluate('''el => {
                                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            }''')
                            logger.info("JS dispatchEvent click 완료")
                            clicked = True
                        except Exception as e:
                            logger.debug(f"dispatchEvent 실패: {e}")
                    
                    if clicked:
                        break
                        
            except Exception as e:
                logger.debug(f"검색 버튼 처리 실패 ({selector}): {e}")
                continue
        
        # ★ 방법 4: JavaScript로 검색 함수 직접 호출 (WebSquare)
        if not clicked:
            js_search_functions = [
                'scwin.btn_search_onclick()',
                'scwin.fn_search()',
                'fn_search()',
                'doSearch()',
                'gcm.fn_search()',
            ]
            
            for js_func in js_search_functions:
                try:
                    await page.evaluate(js_func)
                    logger.info(f"JS 검색 함수 호출 성공: {js_func}")
                    clicked = True
                    break
                except Exception as e:
                    logger.debug(f"JS 함수 호출 실패 ({js_func}): {e}")
        
        if not clicked:
            logger.warning("검색 버튼을 찾거나 클릭할 수 없습니다")
            return False
        
        # ★★★ 데이터 로딩 완료 대기 ★★★
        logger.info("검색 버튼 클릭 완료, 데이터 로딩 대기...")
        
        # 1. 잠시 대기 (검색 요청이 서버로 전송되는 시간)
        await asyncio.sleep(2)
        
        # 2. 네트워크 idle 대기
        try:
            await page.wait_for_load_state('networkidle', timeout=30000)
            logger.info("네트워크 idle 상태")
        except Exception as e:
            logger.debug(f"networkidle 대기 실패: {e}")
        
        # 3. 그리드에 데이터가 나타날 때까지 대기 (최대 30초)
        data_loaded = await self._wait_for_grid_data(page, timeout=30)
        
        if data_loaded:
            logger.info("데이터 로딩 완료 확인됨")
        else:
            logger.warning("데이터 로딩 확인 실패 - 추가 대기 후 진행")
            await asyncio.sleep(5)  # 추가 5초 대기
        
        return True
    
    async def _wait_for_grid_data(self, page: Page, timeout: int = 30) -> bool:
        """
        Wait for grid data to be loaded
        
        Args:
            page: Page object
            timeout: Maximum wait time in seconds
            
        Returns:
            True if data was loaded
        """
        logger.info(f"그리드 데이터 로딩 대기 시작 (최대 {timeout}초)")
        
        check_script = """
        () => {
            const result = {loaded: false, debug: {}};
            
            // Check 1: WebSquare grid row count
            result.debug.hasWebSquare = typeof WebSquare !== 'undefined';
            
            if (typeof WebSquare !== 'undefined') {
                const gridIds = ['grd1', 'mf_grd1', 'grid1', 'grdList'];
                for (const id of gridIds) {
                    try {
                        const grid = WebSquare.util.getComponentById(id);
                        if (grid && typeof grid.getRowCount === 'function') {
                            const count = grid.getRowCount();
                            result.debug[`grid_${id}`] = count;
                            if (count > 0) {
                                result.loaded = true;
                                result.method = 'websquare';
                                result.gridId = id;
                                result.count = count;
                                return result;
                            }
                        }
                    } catch(e) {
                        result.debug[`grid_${id}_error`] = e.message;
                    }
                }
            }
            
            // Check 2: DOM table rows
            const rowSelectors = [
                '.gridBodyTable tbody tr',
                '.w2grid tbody tr',
                'table[id*="grd"] tbody tr',
                '#mf_grd1_body_tbody tr'
            ];
            
            for (const selector of rowSelectors) {
                const rows = document.querySelectorAll(selector);
                result.debug[`dom_${selector.replace(/[^a-zA-Z0-9]/g, '_')}`] = rows.length;
                
                if (rows.length > 0) {
                    // Check if rows have actual content
                    for (const row of rows) {
                        const cells = row.querySelectorAll('td');
                        for (const cell of cells) {
                            const text = cell.textContent.trim();
                            if (text && text.length > 0 && text !== '-') {
                                result.loaded = true;
                                result.method = 'dom';
                                result.selector = selector;
                                result.count = rows.length;
                                return result;
                            }
                        }
                    }
                }
            }
            
            // Check 3: Loading indicator
            const loadingIndicators = document.querySelectorAll('.loading, .w2loading, [class*="loading"]');
            result.debug.loadingIndicators = loadingIndicators.length;
            
            return result;
        }
        """
        
        start_time = asyncio.get_event_loop().time()
        check_count = 0
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                result = await page.evaluate(check_script)
                check_count += 1
                
                # 10번마다 또는 마지막에 디버그 정보 로깅
                if check_count % 10 == 1 or result.get('loaded'):
                    logger.info(f"데이터 로딩 체크 #{check_count}: loaded={result.get('loaded')}, debug={result.get('debug', {})}")
                
                if result.get('loaded'):
                    logger.info(f"데이터 로딩 감지: method={result.get('method')}, count={result.get('count')}")
                    return True
                
            except Exception as e:
                logger.debug(f"데이터 로딩 확인 실패: {e}")
            
            await asyncio.sleep(1)
        
        logger.warning(f"데이터 로딩 타임아웃 ({timeout}초)")
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
            df = None
            
            # 방법 1: Excel 다운로드 시도
            logger.info("=" * 40)
            logger.info("방법 1: Excel 다운로드 시도")
            logger.info("=" * 40)
            df = await self._download_excel_from_page(data_type)
            
            if df is not None and len(df) > 0:
                logger.info(f"Excel 다운로드 성공: {len(df)}행")
            else:
                logger.warning("Excel 다운로드 실패 또는 데이터 없음")
            
            # 방법 2: 테이블 스크래핑 시도 (Excel 실패 시)
            if df is None or len(df) == 0:
                logger.info("=" * 40)
                logger.info("방법 2: 테이블 스크래핑으로 폴백")
                logger.info("=" * 40)
                df = await self._direct_iframe_scrape(data_type)
                
                if df is not None and len(df) > 0:
                    logger.info(f"테이블 스크래핑 성공: {len(df)}행")
                else:
                    logger.warning("테이블 스크래핑도 실패")
        
        if df is not None and len(df) > 0:
            # Clean the data
            df = clean_excel_data(df)
            
            # Remove any completely empty columns
            df = df.dropna(axis=1, how='all')
            
            logger.info(f"데이터 수집 완료: {len(df)}행, {len(df.columns)}열")
            logger.info(f"컬럼: {list(df.columns)}")
            
            return df
        else:
            logger.error(f"데이터 수집 실패: {data_type} (모든 방법 실패)")
            return None
    
    async def _download_excel_from_page(self, data_type: str) -> Optional[pd.DataFrame]:
        """
        Excel 파일 다운로드 시도 (검색 버튼 없이 바로 다운로드)
        """
        page_url = IFRAME_URLS.get(data_type)
        if not page_url:
            return None
        
        new_page = await self.context.new_page()
        new_page.set_default_timeout(120000)  # 2분 타임아웃
        
        try:
            logger.info(f"Excel 다운로드 URL 접속: {page_url}")
            
            # 페이지 로드
            try:
                await new_page.goto(page_url, wait_until='load', timeout=90000)
            except Exception as e:
                logger.warning(f"load 대기 실패, domcontentloaded로 재시도: {e}")
                await new_page.goto(page_url, wait_until='domcontentloaded', timeout=90000)
            
            # 페이지 완전 로드 대기
            await asyncio.sleep(5)
            
            # 스크린샷 저장
            await self._save_debug(f"excel_01_page_loaded_{data_type}", new_page)
            
            # ★★★ frame_locator 사용 ★★★
            iframe_locator = new_page.frame_locator('iframe[src*="websquare"]')
            
            # ★★★ 검색 버튼 없이 바로 Excel 다운로드 시도 ★★★
            logger.info("Excel 다운로드 버튼 클릭 시도 (검색 없이)...")
            try:
                excel_btn = iframe_locator.locator('input[value="Excel 다운로드"]')
                await excel_btn.wait_for(timeout=10000)
                
                # 다운로드 대기 시작 (버튼 클릭 전에)
                async with new_page.expect_download(timeout=60000) as download_info:
                    # Excel 버튼 클릭
                    await excel_btn.click()
                    logger.info("Excel 버튼 클릭 완료")
                    
                    # ★★★ 확인 대화상자 처리 - Enter 키 사용 ★★★
                    await asyncio.sleep(1)  # 대화상자 표시 대기
                    
                    # Enter 키로 확인
                    await new_page.keyboard.press('Enter')
                    logger.info("Enter 키 입력 (확인 대화상자)")
                    
                    logger.info("다운로드 대기 중...")
                
                download = await download_info.value
                download_path = os.path.join(self.download_dir, download.suggested_filename)
                await download.save_as(download_path)
                logger.info(f"Excel 다운로드 완료: {download_path}")
                
                await self._save_debug(f"excel_02_after_download_{data_type}", new_page)
                
                df = pd.read_excel(download_path)
                logger.info(f"Excel 파일 읽기 성공: {len(df)}행")
                return df
                
            except Exception as e:
                logger.warning(f"Excel 다운로드 실패 (검색 없이): {e}")
                
                # 다운로드 폴더에서 파일 찾기 (이미 다운로드되었을 수 있음)
                import glob
                await asyncio.sleep(3)
                excel_files = glob.glob(os.path.join(self.download_dir, '*.xlsx')) + \
                              glob.glob(os.path.join(self.download_dir, '*.xls'))
                if excel_files:
                    download_path = max(excel_files, key=os.path.getctime)
                    logger.info(f"다운로드 폴더에서 파일 발견: {download_path}")
                    df = pd.read_excel(download_path)
                    logger.info(f"Excel 파일 읽기 성공: {len(df)}행")
                    return df
                
                # ★★★ 검색 버튼 클릭 후 다시 시도 ★★★
                logger.info("검색 버튼 클릭 후 Excel 다운로드 재시도...")
                try:
                    search_btn = iframe_locator.locator('input[value="검색"]')
                    await search_btn.wait_for(timeout=10000)
                    await search_btn.click()
                    logger.info("검색 버튼 클릭 성공")
                    await asyncio.sleep(5)
                    
                    # 스크린샷 저장
                    await self._save_debug(f"excel_03_after_search_{data_type}", new_page)
                    
                    # Excel 다운로드 재시도 (expect_download 먼저)
                    excel_btn = iframe_locator.locator('input[value="Excel 다운로드"]')
                    
                    async with new_page.expect_download(timeout=60000) as download_info:
                        await excel_btn.click()
                        logger.info("Excel 버튼 클릭 완료 (검색 후)")
                        
                        # ★★★ Enter 키로 확인 대화상자 처리 ★★★
                        await asyncio.sleep(1)
                        await new_page.keyboard.press('Enter')
                        logger.info("Enter 키 입력 (확인 대화상자)")
                        
                        logger.info("다운로드 대기 중...")
                    
                    download = await download_info.value
                    download_path = os.path.join(self.download_dir, download.suggested_filename)
                    await download.save_as(download_path)
                    logger.info(f"Excel 다운로드 완료: {download_path}")
                    
                    df = pd.read_excel(download_path)
                    logger.info(f"Excel 파일 읽기 성공: {len(df)}행")
                    return df
                    
                except Exception as e2:
                    logger.warning(f"Excel 다운로드 실패 (검색 후): {e2}")
                    
                    # 다운로드 폴더에서 파일 찾기
                    import glob
                    await asyncio.sleep(3)
                    excel_files = glob.glob(os.path.join(self.download_dir, '*.xlsx')) + \
                                  glob.glob(os.path.join(self.download_dir, '*.xls'))
                    if excel_files:
                        download_path = max(excel_files, key=os.path.getctime)
                        logger.info(f"다운로드 폴더에서 파일 발견: {download_path}")
                        df = pd.read_excel(download_path)
                        logger.info(f"Excel 파일 읽기 성공: {len(df)}행")
                        return df
            
            return None
            
        except Exception as e:
            logger.error(f"Excel 다운로드 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
        finally:
            await new_page.close()
    
    async def _get_content_iframe(self, page: Page, data_type: str):
        """
        iframe element를 찾아서 content_frame 반환
        """
        logger.info("iframe 찾기 (content_frame 방식)...")
        
        # iframe element 찾기
        iframe_selectors = [
            'iframe[src*="websquare"]',
            'iframe[src*="ngms.do"]',
            'iframe[id*="tac_layout"]',
            'iframe.w2iframe',
            'iframe',
        ]
        
        for selector in iframe_selectors:
            try:
                iframe_element = await page.query_selector(selector)
                if iframe_element:
                    frame = await iframe_element.content_frame()
                    if frame:
                        frame_url = frame.url
                        logger.info(f"iframe 발견: selector={selector}, url={frame_url[:80]}...")
                        
                        # frame이 로드될 때까지 대기
                        await asyncio.sleep(2)
                        
                        # frame 내용 확인
                        try:
                            ready_state = await frame.evaluate('document.readyState')
                            logger.info(f"iframe readyState: {ready_state}")
                        except Exception as e:
                            logger.warning(f"readyState 확인 실패: {e}")
                        
                        return frame
            except Exception as e:
                logger.debug(f"iframe 선택자 실패 ({selector}): {e}")
                continue
        
        logger.error("iframe을 찾을 수 없음")
        return None
    
    async def _click_search_in_iframe(self, frame, data_type: str) -> bool:
        """
        iframe 내에서 검색 버튼 클릭
        """
        search_selectors = [
            'input[value="검색"]',
            'input[value="조회"]', 
            'button:has-text("검색")',
            'button:has-text("조회")',
        ]
        
        for selector in search_selectors:
            try:
                button = await frame.query_selector(selector)
                if button:
                    logger.info(f"검색 버튼 발견: {selector}")
                    await button.click(force=True)
                    return True
            except Exception as e:
                logger.debug(f"검색 버튼 클릭 실패 ({selector}): {e}")
                continue
        
        return False
    
    async def _scrape_emission_statistics(self) -> Optional[pd.DataFrame]:
        """
        명세서배출량통계 전용 스크래퍼
        이 페이지는 연도별 다운로드 목록 형태로, 조회 버튼 클릭 후 목록 데이터를 스크래핑
        
        Returns:
            DataFrame with emission statistics
        """
        logger.info("명세서배출량통계 스크래핑 (조회 버튼 클릭 후 데이터 로드)")
        
        page_url = IFRAME_URLS.get("명세서배출량통계")
        new_page = await self.context.new_page()
        new_page.set_default_timeout(120000)  # 2분 타임아웃
        
        try:
            logger.info(f"URL 접속: {page_url}")
            
            # 재시도 로직
            for attempt in range(3):
                try:
                    await new_page.goto(page_url, wait_until='load', timeout=90000)
                    break
                except Exception as e:
                    if attempt < 2:
                        logger.warning(f"접속 시도 {attempt+1} 실패, 재시도: {e}")
                        await asyncio.sleep(5)
                    else:
                        try:
                            await new_page.goto(page_url, wait_until='domcontentloaded', timeout=90000)
                        except:
                            raise
            
            await asyncio.sleep(5)
            
            # 스크린샷
            await self._save_debug("emission_stats_01_loaded", new_page)
            
            # ★★★ frame_locator 사용 ★★★
            iframe_locator = new_page.frame_locator('iframe[src*="websquare"]')
            
            # ★★★ 조회 버튼 클릭 ★★★
            logger.info("조회 버튼 클릭 시도 (frame_locator)...")
            try:
                search_btn = iframe_locator.locator('input[value="조회"]')
                await search_btn.wait_for(timeout=10000)
                await search_btn.click()
                logger.info("조회 버튼 클릭 성공")
                await asyncio.sleep(5)
            except Exception as e:
                logger.warning(f"조회 버튼 클릭 실패: {e}")
            
            await self._save_debug("emission_stats_02_after_search", new_page)
            
            # ★★★ 테이블 데이터 추출 (frame_locator 사용) ★★★
            df = await self._extract_table_with_locator(iframe_locator, "명세서배출량통계")
            
            if df is not None and len(df) > 0:
                logger.info(f"테이블 추출 성공: {len(df)}행")
                return df
            
            return None
            
        except Exception as e:
            logger.error(f"명세서배출량통계 스크래핑 실패: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
