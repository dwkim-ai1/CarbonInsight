"""
ETRS/ORS Web Scraper
배출권등록부/상쇄등록부 시스템 웹 스크래퍼

Playwright 기반 테이블 스크래핑 + Excel 다운로드
"""

import os
import sys
import asyncio
import tempfile
from typing import Dict, Optional, List, Any
from pathlib import Path
from datetime import datetime
import pandas as pd
from io import BytesIO

# Absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ETRS_BASE_URL,
    ORS_BASE_URL,
    ETRS_DATASETS,
    ORS_DATASETS,
    ALL_DATASETS,
    PLAN_PERIODS,
    SECTOR_CODES,
    BROWSER_SETTINGS,
    REQUEST_SETTINGS,
    RETRY_SETTINGS,
    DEBUG,
)
from utils import (
    setup_logging,
    clean_excel_data,
    read_excel_auto,
    ensure_directory,
    convert_period_year_columns,
    normalize_year_columns,
)

logger = setup_logging()


class AllocationScraper:
    """ETRS/ORS 통합 웹 스크래퍼 클래스 (Playwright 기반)"""
    
    def __init__(self, download_dir: Optional[str] = None, debug_mode: bool = False):
        """
        Initialize scraper
        
        Args:
            download_dir: Directory for downloaded files
            debug_mode: Enable debug mode (screenshots, etc.)
        """
        self.download_dir = Path(download_dir) if download_dir else Path(tempfile.mkdtemp())
        self.debug_mode = debug_mode or DEBUG.get('save_screenshots', False)
        self.debug_dir = self.download_dir / 'debug'
        
        ensure_directory(str(self.download_dir))
        if self.debug_mode:
            ensure_directory(str(self.debug_dir))
        
        self.browser = None
        self.context = None
        self.playwright = None
        
        logger.info(f"AllocationScraper 초기화 - 저장 경로: {self.download_dir}")
    
    async def initialize(self) -> None:
        """Initialize Playwright browser"""
        from playwright.async_api import async_playwright
        
        logger.info("🚀 브라우저 초기화 중...")
        
        self.playwright = await async_playwright().start()
        
        self.browser = await self.playwright.chromium.launch(
            headless=BROWSER_SETTINGS['headless'],
            slow_mo=BROWSER_SETTINGS.get('slow_mo', 100)
        )
        
        self.context = await self.browser.new_context(
            viewport=BROWSER_SETTINGS.get('viewport', {'width': 1920, 'height': 1080}),
            accept_downloads=True
        )
        
        logger.info("✅ 브라우저 초기화 완료")
    
    async def close(self) -> None:
        """Close browser and cleanup"""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("🔒 브라우저 종료")
    
    async def _save_debug(self, name: str, page) -> None:
        """Save debug screenshot and HTML"""
        if not self.debug_mode:
            return
        
        try:
            await page.screenshot(path=str(self.debug_dir / f"{name}.png"))
            html = await page.content()
            with open(self.debug_dir / f"{name}.html", 'w', encoding='utf-8') as f:
                f.write(html)
            logger.debug(f"📸 디버그 저장: {name}")
        except Exception as e:
            logger.debug(f"디버그 저장 실패 ({name}): {e}")
    
    # =========================================================
    # ETRS 스크래핑 (배출권등록부)
    # =========================================================
    
    async def scrape_etrs_table(
        self,
        dataset_name: str,
        plan_period: int,
        sector: str = "전체"
    ) -> Optional[pd.DataFrame]:
        """
        Scrape ETRS table data using Playwright
        
        Args:
            dataset_name: Dataset name (사전할당량, etc.)
            plan_period: Plan period (1, 2, 3)
            sector: Sector filter
            
        Returns:
            DataFrame with scraped data
        """
        if dataset_name not in ETRS_DATASETS:
            logger.error(f"알 수 없는 ETRS 데이터셋: {dataset_name}")
            return None
        
        dataset = ETRS_DATASETS[dataset_name]
        page = await self.context.new_page()
        page.set_default_timeout(BROWSER_SETTINGS['timeout'])
        
        try:
            # Build URL
            url = f"{ETRS_BASE_URL}{dataset['url_path']}"
            logger.info(f"📥 스크래핑 중: {dataset_name} ({plan_period}차)")
            logger.debug(f"URL: {url}")
            
            # Navigate to page
            await page.goto(url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_01_loaded", page)
            
            # Select plan period
            if dataset.get('has_plan_period'):
                await self._select_plan_period(page, plan_period)
            
            # Select sector (if specified)
            if sector != "전체":
                await self._select_sector(page, sector)
            
            # Click search button
            await self._click_search_button(page)
            await asyncio.sleep(3)
            
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_02_searched", page)
            
            # Try Excel download first (more reliable for full data)
            df = await self._try_excel_download(page, dataset_name, plan_period)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ Excel 다운로드 성공: {len(df)}행")
                # ★★★ N차년도 → 실제연도 컬럼 변환 ★★★
                df = convert_period_year_columns(df, plan_period)
                df = normalize_year_columns(df)
                return df
            
            # Fallback: Scrape HTML table with pagination
            logger.info("📋 테이블 스크래핑으로 폴백...")
            df = await self._scrape_html_table_with_pagination(page, dataset_name)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ 테이블 스크래핑 성공: {len(df)}행")
                # ★★★ N차년도 → 실제연도 컬럼 변환 ★★★
                df = convert_period_year_columns(df, plan_period)
                df = normalize_year_columns(df)
                return df
            
            logger.warning(f"⚠️ 데이터 수집 실패: {dataset_name} {plan_period}차")
            return None
            
        except Exception as e:
            logger.error(f"❌ 스크래핑 오류: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_error", page)
            return None
        finally:
            await page.close()
    
    async def _select_plan_period(self, page, plan_period: int) -> None:
        """Select plan period from dropdown"""
        try:
            # Find and select plan period
            select = page.locator('select[id*="plPeriDgr"], select[name*="plPeriDgr"]').first
            await select.select_option(value=str(plan_period))
            logger.info(f"계획기간 선택: {plan_period}차")
            await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f"계획기간 선택 실패: {e}")
    
    async def _select_sector(self, page, sector: str) -> None:
        """Select sector from dropdown"""
        try:
            sector_code = SECTOR_CODES.get(sector, "")
            if sector_code:
                select = page.locator('select[id*="sectCd"], select[name*="sectCd"]').first
                await select.select_option(value=sector_code)
                logger.info(f"부문 선택: {sector}")
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug(f"부문 선택 실패: {e}")
    
    async def _click_search_button(self, page) -> None:
        """Click search button"""
        try:
            # Try various selectors
            search_selectors = [
                'input[type="submit"][value="검색"]',
                'button:has-text("검색")',
                'input[value="검색"]',
                '.btn30.btnNavy',
            ]
            
            for selector in search_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    logger.info("검색 버튼 클릭")
                    return
            
            logger.debug("검색 버튼을 찾을 수 없음")
        except Exception as e:
            logger.debug(f"검색 버튼 클릭 실패: {e}")
    
    async def _try_excel_download(
        self,
        page,
        dataset_name: str,
        plan_period: int
    ) -> Optional[pd.DataFrame]:
        """Try to download Excel file"""
        try:
            # Find Excel download button
            excel_selectors = [
                'a:has-text("엑셀다운로드")',
                'a:has-text("Excel다운로드")',
                'a[href*="Excel"]',
                '.btn40.btnGreen',
            ]
            
            for selector in excel_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    logger.info("Excel 다운로드 버튼 발견")
                    
                    # Start download
                    async with page.expect_download(timeout=60000) as download_info:
                        await btn.click()
                    
                    download = await download_info.value
                    
                    # Save file
                    filename = f"etrs_{dataset_name}_{plan_period}차_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    filepath = self.download_dir / filename
                    await download.save_as(str(filepath))
                    
                    logger.info(f"💾 Excel 저장: {filepath}")
                    
                    # Read Excel
                    df = pd.read_excel(filepath)
                    df = clean_excel_data(df)
                    
                    return df
            
            logger.debug("Excel 다운로드 버튼 없음")
            return None
            
        except Exception as e:
            logger.debug(f"Excel 다운로드 실패: {e}")
            return None
    
    async def _scrape_html_table_with_pagination(
        self,
        page,
        dataset_name: str
    ) -> Optional[pd.DataFrame]:
        """Scrape HTML table with pagination support"""
        all_data = []
        headers = []
        current_page = 1
        max_pages = 200
        seen_first_rows = set()
        
        # Extract headers
        try:
            header_cells = await page.locator('table.boardList thead th').all_text_contents()
            headers = [h.strip() for h in header_cells if h.strip()]
            logger.info(f"헤더 추출: {headers[:5]}...")
        except Exception as e:
            logger.debug(f"헤더 추출 실패: {e}")
        
        while current_page <= max_pages:
            # Extract current page data
            try:
                rows = page.locator('table.boardList tbody tr')
                row_count = await rows.count()
                
                if row_count == 0:
                    logger.info(f"페이지 {current_page}: 데이터 없음")
                    break
                
                page_data = []
                for i in range(row_count):
                    row = rows.nth(i)
                    cells = await row.locator('td').all_text_contents()
                    cells = [c.strip().replace('\n', ' ').replace('\t', '') for c in cells]
                    
                    if any(c for c in cells):
                        page_data.append(cells)
                
                if not page_data:
                    break
                
                # Check for duplicate page
                first_row_key = '|'.join(str(v) for v in page_data[0][:3])
                if first_row_key in seen_first_rows:
                    logger.info(f"페이지 {current_page}: 중복 데이터 감지, 종료")
                    break
                
                seen_first_rows.add(first_row_key)
                all_data.extend(page_data)
                logger.info(f"페이지 {current_page}: {len(page_data)}행 추출 (누적: {len(all_data)}행)")
                
                # Click next page
                next_clicked = await self._click_next_page(page, current_page)
                if not next_clicked:
                    logger.info("마지막 페이지")
                    break
                
                current_page += 1
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.debug(f"페이지 {current_page} 추출 실패: {e}")
                break
        
        if all_data:
            # Create DataFrame
            if headers and len(headers) >= len(all_data[0]):
                df = pd.DataFrame(all_data, columns=headers[:len(all_data[0])])
            else:
                df = pd.DataFrame(all_data)
            
            df = clean_excel_data(df)
            return df
        
        return None
    
    async def _click_next_page(self, page, current_page: int) -> bool:
        """Click next page button"""
        try:
            next_page = current_page + 1
            
            # Try clicking page number
            page_link = page.locator(f'.pagination a:has-text("{next_page}")').first
            if await page_link.count() > 0:
                await page_link.click()
                await asyncio.sleep(1)
                return True
            
            # Try next button
            next_btn = page.locator('.pagination a.btnNextPage, a[title="다음페이지"]').first
            if await next_btn.count() > 0:
                await next_btn.click()
                await asyncio.sleep(1)
                return True
            
            return False
        except:
            return False
    
    # =========================================================
    # ORS 스크래핑 (상쇄등록부)
    # =========================================================
    
    async def scrape_ors_table(self, dataset_name: str) -> Optional[pd.DataFrame]:
        """
        Scrape ORS table data
        
        Args:
            dataset_name: Dataset name (상쇄배출권발행량, etc.)
            
        Returns:
            DataFrame with scraped data
        """
        if dataset_name not in ORS_DATASETS:
            logger.error(f"알 수 없는 ORS 데이터셋: {dataset_name}")
            return None
        
        dataset = ORS_DATASETS[dataset_name]
        page = await self.context.new_page()
        page.set_default_timeout(BROWSER_SETTINGS['timeout'])
        
        try:
            url = f"{ORS_BASE_URL}{dataset['url_path']}"
            logger.info(f"📥 ORS 스크래핑 중: {dataset_name}")
            logger.debug(f"URL: {url}")
            
            await page.goto(url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            
            await self._save_debug(f"ors_{dataset_name}_01_loaded", page)
            
            # Try Excel download
            df = await self._try_ors_excel_download(page, dataset_name)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ ORS Excel 다운로드 성공: {len(df)}행")
                return df
            
            # Fallback: Scrape HTML table
            logger.info("📋 ORS 테이블 스크래핑으로 폴백...")
            df = await self._scrape_ors_html_table(page, dataset_name)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ ORS 테이블 스크래핑 성공: {len(df)}행")
                return df
            
            logger.warning(f"⚠️ ORS 데이터 수집 실패: {dataset_name}")
            return None
            
        except Exception as e:
            logger.error(f"❌ ORS 스크래핑 오류: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return None
        finally:
            await page.close()
    
    async def _try_ors_excel_download(
        self,
        page,
        dataset_name: str
    ) -> Optional[pd.DataFrame]:
        """Try to download Excel from ORS"""
        try:
            excel_selectors = [
                'a:has-text("엑셀")',
                'a:has-text("Excel")',
                'button:has-text("엑셀")',
                '.btn_excel',
            ]
            
            for selector in excel_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    logger.info("ORS Excel 버튼 발견")
                    
                    async with page.expect_download(timeout=60000) as download_info:
                        await btn.click()
                    
                    download = await download_info.value
                    filename = f"ors_{dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
                    filepath = self.download_dir / filename
                    await download.save_as(str(filepath))
                    
                    df = pd.read_excel(filepath)
                    df = clean_excel_data(df)
                    return df
            
            return None
        except Exception as e:
            logger.debug(f"ORS Excel 다운로드 실패: {e}")
            return None
    
    async def _scrape_ors_html_table(
        self,
        page,
        dataset_name: str
    ) -> Optional[pd.DataFrame]:
        """Scrape ORS HTML table"""
        try:
            # Extract headers
            headers = []
            header_cells = await page.locator('table thead th, table.list th').all_text_contents()
            headers = [h.strip() for h in header_cells if h.strip()]
            
            # Extract data
            rows = page.locator('table tbody tr, table.list tbody tr')
            row_count = await rows.count()
            
            data = []
            for i in range(row_count):
                row = rows.nth(i)
                cells = await row.locator('td').all_text_contents()
                cells = [c.strip() for c in cells]
                if any(c for c in cells):
                    data.append(cells)
            
            if data:
                if headers and len(headers) >= len(data[0]):
                    df = pd.DataFrame(data, columns=headers[:len(data[0])])
                else:
                    df = pd.DataFrame(data)
                
                df = clean_excel_data(df)
                return df
            
            return None
        except Exception as e:
            logger.debug(f"ORS 테이블 스크래핑 실패: {e}")
            return None
    
    # =========================================================
    # 통합 다운로드 메서드
    # =========================================================
    
    async def download_etrs_dataset(
        self,
        dataset_name: str,
        periods: List[int] = None
    ) -> Dict[int, pd.DataFrame]:
        """
        Download ETRS dataset for all plan periods
        
        Args:
            dataset_name: Dataset name
            periods: List of periods (default: all)
            
        Returns:
            Dictionary: period -> DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        results = {}
        
        for period in periods:
            logger.info(f"\n--- {dataset_name} {period}차 ---")
            df = await self.scrape_etrs_table(dataset_name, period)
            
            if df is not None and len(df) > 0:
                # Add plan period column
                df['_계획기간'] = f"{period}차"
                results[period] = df
                logger.info(f"✓ {period}차: {len(df)}행")
            else:
                logger.warning(f"✗ {period}차: 데이터 없음")
        
        return results
    
    async def download_all_etrs(
        self,
        periods: List[int] = None
    ) -> Dict[str, Dict[int, pd.DataFrame]]:
        """
        Download all ETRS datasets
        
        Returns:
            Nested dict: dataset_name -> period -> DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        all_results = {}
        
        for dataset_name in ETRS_DATASETS.keys():
            logger.info(f"\n{'='*50}")
            logger.info(f"📊 데이터셋: {dataset_name}")
            logger.info(f"{'='*50}")
            
            results = await self.download_etrs_dataset(dataset_name, periods)
            if results:
                all_results[dataset_name] = results
        
        return all_results
    
    async def download_all_ors(self) -> Dict[str, pd.DataFrame]:
        """
        Download all ORS datasets
        
        Returns:
            Dictionary: dataset_name -> DataFrame
        """
        results = {}
        
        for dataset_name in ORS_DATASETS.keys():
            logger.info(f"\n{'='*50}")
            logger.info(f"📊 ORS 데이터셋: {dataset_name}")
            logger.info(f"{'='*50}")
            
            df = await self.scrape_ors_table(dataset_name)
            if df is not None and len(df) > 0:
                results[dataset_name] = df
                logger.info(f"✓ {dataset_name}: {len(df)}행")
            else:
                logger.warning(f"✗ {dataset_name}: 데이터 없음")
        
        return results
    
    async def download_all(
        self,
        etrs_periods: List[int] = None,
        include_ors: bool = True
    ) -> Dict[str, Any]:
        """
        Download all data from ETRS and ORS
        
        Args:
            etrs_periods: ETRS plan periods to download
            include_ors: Whether to include ORS data
            
        Returns:
            Dictionary with 'etrs' and 'ors' keys
        """
        results = {
            'etrs': {},
            'ors': {}
        }
        
        # Download ETRS
        logger.info("\n" + "="*60)
        logger.info("🏢 ETRS (배출권등록부) 데이터 수집")
        logger.info("="*60)
        results['etrs'] = await self.download_all_etrs(etrs_periods)
        
        # Download ORS
        if include_ors:
            logger.info("\n" + "="*60)
            logger.info("🌿 ORS (상쇄등록부) 데이터 수집")
            logger.info("="*60)
            results['ors'] = await self.download_all_ors()
        
        return results


async def run_scraper(
    etrs_only: bool = False,
    ors_only: bool = False,
    periods: List[int] = None,
    debug_mode: bool = False
) -> Dict[str, Any]:
    """
    Run the scraper
    
    Args:
        etrs_only: Only download ETRS data
        ors_only: Only download ORS data
        periods: ETRS plan periods
        debug_mode: Enable debug mode
        
    Returns:
        Downloaded data
    """
    scraper = AllocationScraper(debug_mode=debug_mode)
    
    try:
        await scraper.initialize()
        
        if etrs_only:
            return {'etrs': await scraper.download_all_etrs(periods), 'ors': {}}
        elif ors_only:
            return {'etrs': {}, 'ors': await scraper.download_all_ors()}
        else:
            return await scraper.download_all(periods, include_ors=True)
    finally:
        await scraper.close()


if __name__ == "__main__":
    async def main():
        results = await run_scraper(debug_mode=True)
        
        print("\n" + "="*60)
        print("📊 수집 결과 요약")
        print("="*60)
        
        print("\n[ETRS]")
        for dataset, period_data in results['etrs'].items():
            print(f"  {dataset}:")
            for period, df in period_data.items():
                print(f"    - {period}차: {len(df)}행")
        
        print("\n[ORS]")
        for dataset, df in results['ors'].items():
            print(f"  {dataset}: {len(df)}행")
    
    asyncio.run(main())
