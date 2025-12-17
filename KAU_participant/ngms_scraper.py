"""
NGMS Web Scraper
국가온실가스종합관리시스템 웹 스크래퍼
Uses Playwright to download Excel files from NGMS website

Note: NGMS uses WebSquare framework which requires JavaScript function calls
for downloads instead of simple button clicks.
"""

import os
import asyncio
import tempfile
import glob
from pathlib import Path
from typing import Dict, Optional, List, Any
import pandas as pd
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Frame

from config import (
    NGMS_BASE_URL,
    NGMS_MAIN_URL,
    IFRAME_URLS,
    TAB_SELECTORS,
    DOWNLOAD_SELECTORS,
    BROWSER_SETTINGS,
    RETRY_SETTINGS,
    DEBUG,
    COLUMNS
)
from utils import setup_logging, clean_excel_data, save_debug_info, ensure_directory

logger = setup_logging()


class NGMSScraper:
    """NGMS 웹사이트 스크래퍼 클래스"""
    
    def __init__(self, download_dir: Optional[str] = None, debug_mode: bool = False):
        """
        Initialize scraper
        
        Args:
            download_dir: Directory for downloaded files (temp if None)
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
        
        logger.info(f"Scraper 초기화 - 다운로드 경로: {self.download_dir}")
    
    async def _save_debug_screenshot(self, name: str) -> None:
        """Save debug screenshot if debug mode is enabled"""
        if self.debug_mode and self.page:
            try:
                path = os.path.join(self.debug_dir, f"{name}.png")
                await self.page.screenshot(path=path, full_page=True)
                logger.debug(f"스크린샷 저장: {path}")
            except Exception as e:
                logger.warning(f"스크린샷 저장 실패: {e}")
    
    async def _save_debug_html(self, name: str, page: Optional[Page] = None) -> None:
        """Save debug HTML if debug mode is enabled"""
        if self.debug_mode:
            target_page = page or self.page
            if target_page:
                try:
                    html = await target_page.content()
                    path = save_debug_info(html, f"{name}.html", self.debug_dir)
                    logger.debug(f"HTML 저장: {path}")
                except Exception as e:
                    logger.warning(f"HTML 저장 실패: {e}")
    
    async def initialize(self) -> None:
        """Initialize browser and page"""
        logger.info("브라우저 초기화 중...")
        
        self.playwright = await async_playwright().start()
        
        self.browser = await self.playwright.chromium.launch(
            headless=BROWSER_SETTINGS['headless'],
            slow_mo=BROWSER_SETTINGS['slow_mo']
        )
        
        self.context = await self.browser.new_context(
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        
        # Set download path
        self.context.set_default_timeout(BROWSER_SETTINGS['timeout'])
        
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
    
    async def navigate_to_main(self) -> None:
        """Navigate to NGMS main page"""
        logger.info(f"메인 페이지 접속: {NGMS_MAIN_URL}")
        
        await self.page.goto(NGMS_MAIN_URL, wait_until='networkidle')
        await asyncio.sleep(3)  # Wait for page to fully load
        
        await self._save_debug_screenshot("01_main_page")
        await self._save_debug_html("01_main_page")
        
        logger.info("메인 페이지 로드 완료")
    
    async def _find_iframe(self) -> Optional[Frame]:
        """
        Find and return the content iframe
        
        Returns:
            Frame object or None if not found
        """
        # Wait for iframe to be available
        await asyncio.sleep(2)
        
        # Try different iframe selectors
        iframe_selectors = [
            'iframe[class*="w2tabcontrol"]',
            'iframe[class*="w2iframe"]',
            'iframe[src*="OGCMBBS"]',
            'iframe[src*="websquare"]',
            'iframe#mf_tac_layout_contents_50900501_body',
            'iframe#mf_tac_layout_contents_50900502_body',
            'iframe#mf_tac_layout_contents_50900503_body',
        ]
        
        for selector in iframe_selectors:
            try:
                iframe_element = await self.page.query_selector(selector)
                if iframe_element:
                    frame = await iframe_element.content_frame()
                    if frame:
                        logger.info(f"iframe 발견: {selector}")
                        return frame
            except Exception as e:
                logger.debug(f"iframe 선택자 시도 실패 ({selector}): {e}")
        
        # Try to get frames directly
        frames = self.page.frames
        logger.info(f"전체 프레임 수: {len(frames)}")
        
        for i, frame in enumerate(frames):
            url = frame.url
            logger.debug(f"Frame {i}: {url}")
            if 'OGCMBBS' in url or ('websquare' in url and 'ngms' in url):
                logger.info(f"iframe 발견 (frame {i}): {url}")
                return frame
        
        # Return the first non-main frame if available
        if len(frames) > 1:
            for frame in frames[1:]:
                if frame.url and 'ngms' in frame.url:
                    logger.info(f"iframe 발견 (fallback): {frame.url}")
                    return frame
        
        logger.warning("iframe을 찾을 수 없습니다")
        return None
    
    async def _click_tab(self, data_type: str) -> bool:
        """
        Click on a specific tab
        
        Args:
            data_type: Type of data tab to click
            
        Returns:
            True if successful
        """
        logger.info(f"탭 클릭 시도: {data_type}")
        
        # Tab selectors to try
        tab_selectors = [
            f'a[menuno*="{data_type[:4]}"]',
            f'a:has-text("{data_type}")',
            f'li:has-text("{data_type}") a',
            TAB_SELECTORS.get(data_type, ''),
        ]
        
        # Menu number mapping
        menu_numbers = {
            "할당대상업체": "50900501",
            "목표관리대상업체": "50900502",
            "명세서배출량통계": "50900503"
        }
        
        menu_no = menu_numbers.get(data_type)
        if menu_no:
            tab_selectors.insert(0, f'#mf_tac_layout_tab_{menu_no} a')
            tab_selectors.insert(1, f'a[href*="{menu_no}"]')
        
        for selector in tab_selectors:
            if not selector:
                continue
            try:
                element = await self.page.query_selector(selector)
                if element:
                    await element.click()
                    await asyncio.sleep(3)
                    await self._save_debug_screenshot(f"02_tab_clicked_{data_type}")
                    logger.info(f"탭 클릭 성공: {selector}")
                    return True
            except Exception as e:
                logger.debug(f"탭 클릭 실패 ({selector}): {e}")
        
        logger.warning(f"탭 클릭 실패: {data_type}")
        return False
    
    async def _download_excel_in_frame(
        self, 
        frame: Frame, 
        data_type: str
    ) -> Optional[str]:
        """
        Download Excel file from within a frame using JavaScript
        
        Args:
            frame: Frame containing the download button
            data_type: Type of data
            
        Returns:
            Path to downloaded file or None
        """
        logger.info(f"Excel 다운로드 시도 (프레임 내): {data_type}")
        
        # List existing files before download
        existing_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
        
        # Different selectors for different data types
        if data_type == "명세서배출량통계":
            # For 명세서배출량통계, click the first "다운" button (업체배출량)
            button_selectors = [
                'input[value="다운"]',
                'a:has-text("다운")',
                'button:has-text("다운")',
            ]
        else:
            button_selectors = [
                'input[value="Excel 다운로드"]',
                'input[value*="Excel"]',
                'a:has-text("Excel 다운로드")',
                'button:has-text("Excel 다운로드")',
            ]
        
        for selector in button_selectors:
            try:
                element = await frame.query_selector(selector)
                if element:
                    logger.info(f"다운로드 버튼 발견: {selector}")
                    
                    # Get button info for debugging
                    tag_name = await element.evaluate('el => el.tagName')
                    onclick = await element.get_attribute('onclick') or ''
                    element_id = await element.get_attribute('id') or ''
                    logger.info(f"버튼 정보: tag={tag_name}, id={element_id}, onclick={onclick[:100]}")
                    
                    # Method 1: Try using JavaScript click and dispatch events
                    try:
                        # Setup download handler with longer timeout
                        async with self.page.expect_download(timeout=30000) as download_info:
                            # Try JavaScript click which might trigger WebSquare handlers
                            await element.evaluate('el => { el.click(); }')
                            await asyncio.sleep(1)
                            
                            # If button has onclick, try executing it
                            if onclick:
                                try:
                                    await frame.evaluate(f'() => {{ {onclick} }}')
                                except Exception as e:
                                    logger.debug(f"onclick 실행 실패: {e}")
                        
                        download = await download_info.value
                        filename = download.suggested_filename or f"{data_type}.xlsx"
                        save_path = os.path.join(self.download_dir, filename)
                        await download.save_as(save_path)
                        logger.info(f"다운로드 완료 (Method 1): {save_path}")
                        return save_path
                        
                    except Exception as e:
                        logger.debug(f"Method 1 (expect_download) 실패: {e}")
                    
                    # Method 2: Direct click with download monitoring
                    try:
                        await element.click(force=True)
                        logger.info("버튼 직접 클릭 완료, 다운로드 대기 중...")
                        
                        # Wait and check for new files
                        for _ in range(30):  # Wait up to 30 seconds
                            await asyncio.sleep(1)
                            current_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
                            new_files = current_files - existing_files
                            if new_files:
                                new_file = list(new_files)[0]
                                logger.info(f"다운로드 완료 (Method 2): {new_file}")
                                return new_file
                        
                    except Exception as e:
                        logger.debug(f"Method 2 (direct click) 실패: {e}")
                    
                    # Method 3: Try to find and call WebSquare export function
                    try:
                        # Common WebSquare Excel export function patterns
                        js_functions = [
                            'scwin.btn_excel_onclick()',
                            'scwin.btn_excelDown_onclick()',
                            'gcm.downloadExcel()',
                            'WebSquare.uiplugin.grid.downloadExcel()',
                        ]
                        
                        for js_func in js_functions:
                            try:
                                await frame.evaluate(js_func)
                                logger.info(f"JS 함수 호출: {js_func}")
                                
                                # Wait for download
                                for _ in range(15):
                                    await asyncio.sleep(1)
                                    current_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
                                    new_files = current_files - existing_files
                                    if new_files:
                                        new_file = list(new_files)[0]
                                        logger.info(f"다운로드 완료 (Method 3): {new_file}")
                                        return new_file
                            except:
                                continue
                                
                    except Exception as e:
                        logger.debug(f"Method 3 (WebSquare function) 실패: {e}")
                        
            except Exception as e:
                logger.debug(f"다운로드 시도 실패 ({selector}): {e}")
        
        # Method 4: Try to intercept network request for Excel download
        try:
            logger.info("네트워크 요청 가로채기 시도...")
            
            # Find all buttons and try clicking each
            all_buttons = await frame.query_selector_all('input[type="button"], button, a.btn')
            
            for btn in all_buttons:
                try:
                    btn_value = await btn.get_attribute('value') or ''
                    btn_text = await btn.text_content() or ''
                    
                    if 'excel' in btn_value.lower() or 'excel' in btn_text.lower() or \
                       '다운' in btn_value or '다운' in btn_text:
                        
                        logger.info(f"추가 버튼 시도: value='{btn_value}', text='{btn_text}'")
                        
                        # Try clicking with download context
                        async with self.context.expect_event('download', timeout=20000) as download_info:
                            await btn.dispatch_event('click')
                        
                        download = await download_info.value
                        filename = download.suggested_filename or f"{data_type}.xlsx"
                        save_path = os.path.join(self.download_dir, filename)
                        await download.save_as(save_path)
                        logger.info(f"다운로드 완료 (Method 4): {save_path}")
                        return save_path
                        
                except Exception as e:
                    continue
                    
        except Exception as e:
            logger.debug(f"Method 4 실패: {e}")
        
        return None
    
    async def _download_excel_direct(self, data_type: str) -> Optional[str]:
        """
        Try direct page navigation to download Excel
        
        Args:
            data_type: Type of data
            
        Returns:
            Path to downloaded file or None
        """
        logger.info(f"직접 페이지 방식 다운로드 시도: {data_type}")
        
        iframe_url = IFRAME_URLS.get(data_type)
        if not iframe_url:
            return None
        
        existing_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
        
        # Create new page for direct access
        new_page = await self.context.new_page()
        
        try:
            await new_page.goto(iframe_url, wait_until='networkidle')
            await asyncio.sleep(5)
            
            if self.debug_mode:
                safe_name = data_type.replace(' ', '_')
                await new_page.screenshot(path=os.path.join(self.debug_dir, f"direct_{safe_name}.png"))
                html = await new_page.content()
                with open(os.path.join(self.debug_dir, f"direct_{safe_name}.html"), 'w', encoding='utf-8') as f:
                    f.write(html)
            
            # Search for download button with various selectors
            if data_type == "명세서배출량통계":
                selectors = [
                    'input[value="다운"]',
                    'a:has-text("다운")',
                    'button:has-text("다운")',
                ]
            else:
                selectors = [
                    'input[value="Excel 다운로드"]',
                    'input[value*="Excel"]',
                    'a:has-text("Excel 다운로드")',
                    'button:has-text("Excel 다운로드")',
                    'a:has-text("Excel")',
                ]
            
            for selector in selectors:
                try:
                    element = await new_page.query_selector(selector)
                    if element:
                        logger.info(f"직접 페이지에서 버튼 발견: {selector}")
                        
                        # Try with download handler
                        try:
                            async with new_page.expect_download(timeout=30000) as download_info:
                                await element.click(force=True)
                            
                            download = await download_info.value
                            filename = download.suggested_filename or f"{data_type}.xlsx"
                            save_path = os.path.join(self.download_dir, filename)
                            await download.save_as(save_path)
                            logger.info(f"직접 다운로드 성공: {save_path}")
                            return save_path
                            
                        except Exception as e:
                            logger.debug(f"expect_download 실패: {e}")
                        
                        # Try click and wait for file
                        await element.click(force=True)
                        for _ in range(20):
                            await asyncio.sleep(1)
                            current_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
                            new_files = current_files - existing_files
                            if new_files:
                                return list(new_files)[0]
                        
                except Exception as e:
                    logger.debug(f"직접 다운로드 실패 ({selector}): {e}")
            
            # Try to find download URLs in page source
            try:
                page_content = await new_page.content()
                
                # Look for Excel download URLs
                import re
                excel_urls = re.findall(r'["\']([^"\']*\.xlsx?[^"\']*)["\']', page_content)
                excel_urls += re.findall(r'["\']([^"\']*excel[^"\']*)["\']', page_content, re.IGNORECASE)
                excel_urls += re.findall(r'["\']([^"\']*download[^"\']*)["\']', page_content, re.IGNORECASE)
                
                for url in excel_urls[:5]:
                    if url.startswith('/'):
                        full_url = f"{NGMS_BASE_URL}{url}"
                    elif url.startswith('http'):
                        full_url = url
                    else:
                        continue
                    
                    logger.info(f"다운로드 URL 시도: {full_url}")
                    try:
                        async with new_page.expect_download(timeout=15000) as download_info:
                            await new_page.goto(full_url)
                        
                        download = await download_info.value
                        filename = download.suggested_filename or f"{data_type}.xlsx"
                        save_path = os.path.join(self.download_dir, filename)
                        await download.save_as(save_path)
                        return save_path
                    except:
                        continue
                        
            except Exception as e:
                logger.debug(f"URL 추출 실패: {e}")
            
            return None
            
        finally:
            await new_page.close()
    
    async def download_data(self, data_type: str) -> Optional[pd.DataFrame]:
        """
        Download and parse Excel data for a specific data type
        
        Args:
            data_type: Type of data to download
            
        Returns:
            DataFrame with downloaded data or None
        """
        logger.info(f"=== {data_type} 데이터 다운로드 시작 ===")
        
        downloaded_path = None
        existing_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
        
        # Setup network request monitoring for download URLs
        download_urls = []
        
        async def handle_response(response):
            """Monitor responses for Excel files"""
            try:
                content_type = response.headers.get('content-type', '')
                content_disp = response.headers.get('content-disposition', '')
                
                if ('excel' in content_type.lower() or 
                    'spreadsheet' in content_type.lower() or
                    'octet-stream' in content_type.lower() or
                    '.xls' in content_disp.lower()):
                    download_urls.append(response.url)
                    logger.info(f"Excel 응답 감지: {response.url[:100]}")
            except:
                pass
        
        self.page.on('response', handle_response)
        
        try:
            # Strategy 1: Navigate and use iframe
            try:
                await self.navigate_to_main()
                
                # Click on the appropriate tab
                await self._click_tab(data_type)
                
                # Wait for content to load
                await asyncio.sleep(3)
                
                # Find iframe
                frame = await self._find_iframe()
                
                if frame:
                    # Save debug info
                    if self.debug_mode:
                        try:
                            iframe_html = await frame.content()
                            with open(os.path.join(self.debug_dir, f"iframe_{data_type}.html"), 'w', encoding='utf-8') as f:
                                f.write(iframe_html)
                            await self.page.screenshot(path=os.path.join(self.debug_dir, f"before_download_{data_type}.png"))
                        except Exception as e:
                            logger.debug(f"디버그 저장 실패: {e}")
                    
                    downloaded_path = await self._download_excel_in_frame(frame, data_type)
                
            except Exception as e:
                logger.warning(f"iframe 방식 실패: {e}")
            
            # Strategy 2: Direct page access
            if not downloaded_path:
                try:
                    downloaded_path = await self._download_excel_direct(data_type)
                except Exception as e:
                    logger.warning(f"직접 접속 방식 실패: {e}")
            
            # Strategy 3: Check for any new downloaded files
            if not downloaded_path:
                current_files = set(glob.glob(os.path.join(self.download_dir, "*.xls*")))
                new_files = current_files - existing_files
                if new_files:
                    downloaded_path = list(new_files)[0]
                    logger.info(f"새 파일 발견: {downloaded_path}")
            
            # Strategy 4: Try downloading from captured URLs
            if not downloaded_path and download_urls:
                for url in download_urls:
                    try:
                        logger.info(f"캡처된 URL에서 다운로드 시도: {url[:100]}")
                        new_page = await self.context.new_page()
                        async with new_page.expect_download(timeout=30000) as download_info:
                            await new_page.goto(url)
                        download = await download_info.value
                        filename = download.suggested_filename or f"{data_type}.xlsx"
                        downloaded_path = os.path.join(self.download_dir, filename)
                        await download.save_as(downloaded_path)
                        await new_page.close()
                        logger.info(f"URL 다운로드 성공: {downloaded_path}")
                        break
                    except Exception as e:
                        logger.debug(f"URL 다운로드 실패: {e}")
                        try:
                            await new_page.close()
                        except:
                            pass
        
        finally:
            # Remove listener
            try:
                self.page.remove_listener('response', handle_response)
            except:
                pass
        
        # Parse downloaded file
        if downloaded_path and os.path.exists(downloaded_path):
            try:
                # Try different Excel engines
                try:
                    df = pd.read_excel(downloaded_path, engine='openpyxl')
                except:
                    df = pd.read_excel(downloaded_path)
                
                df = clean_excel_data(df)
                
                logger.info(f"데이터 파싱 완료: {len(df)}행")
                logger.info(f"컬럼: {list(df.columns)}")
                
                return df
                
            except Exception as e:
                logger.error(f"Excel 파싱 실패: {e}")
                # Try to save the file content for debugging
                if self.debug_mode:
                    try:
                        import shutil
                        shutil.copy(downloaded_path, os.path.join(self.debug_dir, f"failed_{data_type}.xlsx"))
                    except:
                        pass
                return None
        else:
            logger.error(f"다운로드된 파일 없음: {data_type}")
            return None
    
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
                if df is not None:
                    results[data_type] = df
                else:
                    logger.warning(f"{data_type} 다운로드 실패")
            except Exception as e:
                logger.error(f"{data_type} 처리 중 오류: {e}")
        
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


# For testing/debugging
if __name__ == "__main__":
    async def main():
        results = await run_scraper(debug_mode=True)
        for data_type, df in results.items():
            print(f"\n=== {data_type} ===")
            print(f"Shape: {df.shape}")
            print(df.head())
    
    asyncio.run(main())
