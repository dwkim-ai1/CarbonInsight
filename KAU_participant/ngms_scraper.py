"""
NGMS Web Scraper
국가온실가스종합관리시스템 웹 스크래퍼
Uses Playwright to download Excel files from NGMS website
"""

import os
import asyncio
import tempfile
from pathlib import Path
from typing import Dict, Optional, List, Any
import pandas as pd
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

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
        
        playwright = await async_playwright().start()
        
        self.browser = await playwright.chromium.launch(
            headless=BROWSER_SETTINGS['headless'],
            slow_mo=BROWSER_SETTINGS['slow_mo']
        )
        
        self.context = await self.browser.new_context(
            accept_downloads=True,
            viewport={'width': 1920, 'height': 1080}
        )
        
        self.page = await self.context.new_page()
        self.page.set_default_timeout(BROWSER_SETTINGS['timeout'])
        
        logger.info("브라우저 초기화 완료")
    
    async def close(self) -> None:
        """Close browser and cleanup"""
        if self.browser:
            await self.browser.close()
            logger.info("브라우저 종료")
    
    async def navigate_to_main(self) -> None:
        """Navigate to NGMS main page"""
        logger.info(f"메인 페이지 접속: {NGMS_MAIN_URL}")
        
        await self.page.goto(NGMS_MAIN_URL, wait_until='networkidle')
        await asyncio.sleep(3)  # Wait for page to fully load
        
        await self._save_debug_screenshot("01_main_page")
        await self._save_debug_html("01_main_page")
        
        logger.info("메인 페이지 로드 완료")
    
    async def _find_iframe(self) -> Optional[Page]:
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
            'iframe[src*="OGCMBBS"]',
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
            if 'OGCMBBS' in url or 'websquare' in url:
                logger.info(f"iframe 발견 (frame {i}): {url}")
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
        frame: Page, 
        data_type: str
    ) -> Optional[str]:
        """
        Download Excel file from within a frame
        
        Args:
            frame: Frame containing the download button
            data_type: Type of data
            
        Returns:
            Path to downloaded file or None
        """
        logger.info(f"Excel 다운로드 시도 (프레임 내): {data_type}")
        
        # Different selectors for different data types
        if data_type == "명세서배출량통계":
            # For 명세서배출량통계, we need to download the first "다운" button (업체배출량)
            selectors = [
                'a:has-text("다운"):first-of-type',
                'button:has-text("다운"):first-of-type',
                'a.btn:has-text("다운")',
                'input[value="다운"]',
                '[onclick*="download"]',
            ]
        else:
            selectors = [
                'a:has-text("Excel 다운로드")',
                'button:has-text("Excel 다운로드")',
                'a:has-text("Excel")',
                'input[value*="Excel"]',
                '[onclick*="excel"]',
                '[onclick*="Excel"]',
            ]
        
        for selector in selectors:
            try:
                # Check if element exists in frame
                element = await frame.query_selector(selector)
                if element:
                    logger.info(f"다운로드 버튼 발견: {selector}")
                    
                    # Setup download handler
                    async with self.page.expect_download(timeout=BROWSER_SETTINGS['download_timeout']) as download_info:
                        await element.click()
                    
                    download = await download_info.value
                    
                    # Save downloaded file
                    filename = download.suggested_filename or f"{data_type}.xlsx"
                    save_path = os.path.join(self.download_dir, filename)
                    await download.save_as(save_path)
                    
                    logger.info(f"다운로드 완료: {save_path}")
                    return save_path
                    
            except Exception as e:
                logger.debug(f"다운로드 시도 실패 ({selector}): {e}")
        
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
        
        # Create new page for direct access
        new_page = await self.context.new_page()
        
        try:
            await new_page.goto(iframe_url, wait_until='networkidle')
            await asyncio.sleep(5)
            
            await self._save_debug_html(f"03_direct_{data_type}", new_page)
            
            # Try to find and click download button
            selectors = [
                'a:has-text("Excel 다운로드")',
                'button:has-text("Excel 다운로드")',
                'a:has-text("다운")',
            ]
            
            for selector in selectors:
                try:
                    element = await new_page.query_selector(selector)
                    if element:
                        async with new_page.expect_download(timeout=BROWSER_SETTINGS['download_timeout']) as download_info:
                            await element.click()
                        
                        download = await download_info.value
                        filename = download.suggested_filename or f"{data_type}.xlsx"
                        save_path = os.path.join(self.download_dir, filename)
                        await download.save_as(save_path)
                        
                        logger.info(f"직접 다운로드 성공: {save_path}")
                        return save_path
                        
                except Exception as e:
                    logger.debug(f"직접 다운로드 실패 ({selector}): {e}")
            
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
        
        # Strategy 1: Navigate and use iframe
        try:
            await self.navigate_to_main()
            
            # Click on the appropriate tab
            await self._click_tab(data_type)
            
            # Find iframe
            frame = await self._find_iframe()
            
            if frame:
                downloaded_path = await self._download_excel_in_frame(frame, data_type)
            
        except Exception as e:
            logger.warning(f"iframe 방식 실패: {e}")
        
        # Strategy 2: Direct page access
        if not downloaded_path:
            try:
                downloaded_path = await self._download_excel_direct(data_type)
            except Exception as e:
                logger.warning(f"직접 접속 방식 실패: {e}")
        
        # Parse downloaded file
        if downloaded_path and os.path.exists(downloaded_path):
            try:
                df = pd.read_excel(downloaded_path)
                df = clean_excel_data(df)
                
                logger.info(f"데이터 파싱 완료: {len(df)}행")
                
                # Log column information for debugging
                logger.info(f"컬럼: {list(df.columns)}")
                
                return df
                
            except Exception as e:
                logger.error(f"Excel 파싱 실패: {e}")
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
