"""
ETRS/ORS Web Scraper
배출권등록부/상쇄등록부 시스템 웹 스크래퍼

Playwright 기반 테이블 스크래핑 + Excel 다운로드

★★★ 변경사항 (v1.1) ★★★
1. 이행연도 selector 수정: select[id*="implYear"] → #pfYy
2. 자동 수집 모드 지원: 현재 연도만 수집하는 옵션 추가
3. 기존 데이터에 새 연도 데이터 append 로직 지원
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
        sector: str = "전체",
        implementation_year: int = None  # ★ 이행연도 파라미터 추가
    ) -> Optional[pd.DataFrame]:
        """
        Scrape ETRS table data using Playwright
        
        Args:
            dataset_name: Dataset name (사전할당량, etc.)
            plan_period: Plan period (1, 2, 3)
            sector: Sector filter
            implementation_year: Implementation year (이행연도) for certain datasets
            
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
            year_str = f" {implementation_year}년" if implementation_year else ""
            logger.info(f"📥 스크래핑 중: {dataset_name} ({plan_period}차{year_str})")
            logger.debug(f"URL: {url}")
            
            # Navigate to page
            await page.goto(url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(2)
            
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_{implementation_year or 'all'}_01_loaded", page)
            
            # Select plan period
            if dataset.get('has_plan_period'):
                await self._select_plan_period(page, plan_period)
            
            # ★★★ 이행연도 선택 (해당 데이터셋만) ★★★
            if dataset.get('has_implementation_year') and implementation_year:
                await self._select_implementation_year(page, implementation_year)
            
            # Select sector (if specified)
            if sector != "전체":
                await self._select_sector(page, sector)
            
            # Click search button
            await self._click_search_button(page)
            await asyncio.sleep(3)
            
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_{implementation_year or 'all'}_02_searched", page)
            
            # Try Excel download first (more reliable for full data)
            df = await self._try_excel_download(page, dataset_name, plan_period)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ Excel 다운로드 성공: {len(df)}행")
                # ★★★ 이행연도 컬럼 추가 (해당 데이터셋만) ★★★
                if implementation_year:
                    df['이행연도'] = implementation_year
                # ★★★ N차년도 → 실제연도 컬럼 변환 ★★★
                df = convert_period_year_columns(df, plan_period)
                df = normalize_year_columns(df)
                return df
            
            # Fallback: Scrape HTML table with pagination
            logger.info("📋 테이블 스크래핑으로 폴백...")
            df = await self._scrape_html_table_with_pagination(page, dataset_name)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ 테이블 스크래핑 성공: {len(df)}행")
                # ★★★ 이행연도 컬럼 추가 (해당 데이터셋만) ★★★
                if implementation_year:
                    df['이행연도'] = implementation_year
                # ★★★ N차년도 → 실제연도 컬럼 변환 ★★★
                df = convert_period_year_columns(df, plan_period)
                df = normalize_year_columns(df)
                return df
            
            logger.warning(f"⚠️ 데이터 수집 실패: {dataset_name} {plan_period}차{year_str}")
            return None
            
        except Exception as e:
            logger.error(f"❌ 스크래핑 오류: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            await self._save_debug(f"etrs_{dataset_name}_{plan_period}_{implementation_year or 'all'}_error", page)
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
    
    async def _select_implementation_year(self, page, year: int) -> None:
        """
        Select implementation year (이행연도) from dropdown
        
        ★★★ 수정됨: 실제 ETRS HTML에 맞는 selector 사용 ★★★
        - 인증배출량, 배출권이월량, 배출권차입량 페이지의 이행연도 드롭다운
        - HTML: <select id="pfYy" name="condition.pfYy">
        """
        try:
            # ★★★ 올바른 이행연도 selector (실제 ETRS HTML 기반) ★★★
            selectors = [
                '#pfYy',                              # 직접 ID 선택
                'select#pfYy',                        # select 태그와 ID 조합
                'select[name="condition.pfYy"]',      # name 속성으로 선택
                'select[name*="pfYy"]',               # name에 pfYy 포함
                'select[id*="pfYy"]',                 # id에 pfYy 포함 (안전장치)
            ]
            
            for selector in selectors:
                try:
                    select = page.locator(selector).first
                    count = await select.count()
                    
                    if count > 0:
                        # 옵션 확인
                        options = await select.locator('option').all_text_contents()
                        logger.debug(f"이행연도 옵션 ({selector}): {options}")
                        
                        # 연도로 선택 시도 (value 속성)
                        try:
                            await select.select_option(value=str(year))
                            logger.info(f"✓ 이행연도 선택 성공: {year}년 (selector: {selector}, value)")
                            await asyncio.sleep(1)
                            return
                        except Exception as e1:
                            logger.debug(f"value 선택 실패: {e1}")
                        
                        # label로 시도
                        try:
                            await select.select_option(label=str(year))
                            logger.info(f"✓ 이행연도 선택 성공: {year}년 (selector: {selector}, label)")
                            await asyncio.sleep(1)
                            return
                        except Exception as e2:
                            logger.debug(f"label 선택 실패: {e2}")
                        
                        # 연도 문자열 포함 옵션 찾기 (예: "2024년", "2024")
                        for option_text in options:
                            if str(year) in option_text:
                                try:
                                    await select.select_option(label=option_text)
                                    logger.info(f"✓ 이행연도 선택 성공: {year}년 (옵션: {option_text})")
                                    await asyncio.sleep(1)
                                    return
                                except:
                                    pass
                                    
                except Exception as e:
                    logger.debug(f"selector '{selector}' 시도 실패: {e}")
                    continue
            
            logger.warning(f"⚠️ 이행연도 선택 실패: {year}년 - 모든 selector 시도 실패")
            
        except Exception as e:
            logger.error(f"❌ 이행연도 선택 오류: {e}")
    
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
                'button.btn30.btnNavy',
                'a.btn30.btnNavy',
            ]
            
            for selector in search_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    logger.info("검색 버튼 클릭")
                    return
            
            logger.warning("검색 버튼을 찾지 못함")
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
                'button:has-text("엑셀다운로드")',
                'a[href*="Excel"]',
                '.btnGreen:has-text("엑셀")',
                '#downloadExcelButton',
            ]
            
            excel_btn = None
            for selector in excel_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    excel_btn = btn
                    break
            
            if not excel_btn:
                logger.debug("Excel 다운로드 버튼 없음")
                return None
            
            # Start download
            async with page.expect_download(timeout=90000) as download_info:
                await excel_btn.click()
            
            download = await download_info.value
            
            # Save to temp file
            temp_path = self.download_dir / f"{dataset_name}_{plan_period}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            await download.save_as(str(temp_path))
            
            logger.debug(f"Excel 저장: {temp_path}")
            
            # Read Excel
            df = read_excel_auto(str(temp_path))
            
            if df is not None:
                df = clean_excel_data(df)
            
            return df
            
        except Exception as e:
            logger.debug(f"Excel 다운로드 실패: {e}")
            return None
    
    async def _scrape_html_table_with_pagination(
        self,
        page,
        dataset_name: str
    ) -> Optional[pd.DataFrame]:
        """Scrape HTML table with pagination handling"""
        all_data = []
        page_num = 1
        max_pages = 100  # Safety limit
        
        while page_num <= max_pages:
            try:
                # Extract current page data
                df_page = await self._extract_table_data(page)
                
                if df_page is None or len(df_page) == 0:
                    break
                
                all_data.append(df_page)
                logger.debug(f"페이지 {page_num}: {len(df_page)}행")
                
                # Try to go to next page
                next_btn = page.locator('a.btnNextPage, a[title*="다음"], .btnNextPage').first
                
                if await next_btn.count() == 0:
                    break
                
                # Check if next button is disabled
                is_disabled = await next_btn.get_attribute('class')
                if is_disabled and 'disabled' in is_disabled:
                    break
                
                await next_btn.click()
                await asyncio.sleep(2)
                page_num += 1
                
            except Exception as e:
                logger.debug(f"페이지 {page_num} 처리 오류: {e}")
                break
        
        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            # Remove duplicates
            combined = combined.drop_duplicates()
            return combined
        
        return None
    
    async def _extract_table_data(self, page) -> Optional[pd.DataFrame]:
        """Extract data from current table"""
        try:
            # Get headers
            headers = await page.locator('table thead th, table.list thead th').all_text_contents()
            headers = [h.strip() for h in headers if h.strip()]
            
            # Get rows
            rows = page.locator('table tbody tr, table.list tbody tr')
            row_count = await rows.count()
            
            data = []
            for i in range(row_count):
                row = rows.nth(i)
                cells = await row.locator('td').all_text_contents()
                cells = [c.strip() for c in cells]
                if any(c for c in cells):  # Skip empty rows
                    data.append(cells)
            
            if data:
                # Match headers to data
                if headers and len(headers) >= len(data[0]):
                    df = pd.DataFrame(data, columns=headers[:len(data[0])])
                else:
                    df = pd.DataFrame(data)
                
                return clean_excel_data(df)
            
            return None
            
        except Exception as e:
            logger.debug(f"테이블 추출 오류: {e}")
            return None
    
    # =========================================================
    # ORS 스크래핑 (상쇄등록부)
    # =========================================================
    
    async def scrape_ors_table(self, dataset_name: str) -> Optional[pd.DataFrame]:
        """
        Scrape ORS table data
        
        Args:
            dataset_name: ORS dataset name
            
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
            
            # Fallback: HTML scraping
            logger.info("📋 ORS 테이블 스크래핑으로 폴백...")
            df = await self._scrape_ors_html_table(page, dataset_name)
            
            if df is not None and len(df) > 0:
                logger.info(f"✅ ORS 테이블 스크래핑 성공: {len(df)}행")
                return df
            
            logger.warning(f"⚠️ ORS 데이터 수집 실패: {dataset_name}")
            return None
            
        except Exception as e:
            logger.error(f"❌ ORS 스크래핑 오류: {e}")
            await self._save_debug(f"ors_{dataset_name}_error", page)
            return None
        finally:
            await page.close()
    
    async def _try_ors_excel_download(
        self,
        page,
        dataset_name: str
    ) -> Optional[pd.DataFrame]:
        """Try to download ORS Excel file"""
        try:
            excel_selectors = [
                'a:has-text("엑셀다운로드")',
                'button:has-text("엑셀")',
                'a[href*="Excel"]',
                '.btn_excel',
            ]
            
            excel_btn = None
            for selector in excel_selectors:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    excel_btn = btn
                    break
            
            if not excel_btn:
                return None
            
            async with page.expect_download(timeout=90000) as download_info:
                await excel_btn.click()
            
            download = await download_info.value
            temp_path = self.download_dir / f"ors_{dataset_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            await download.save_as(str(temp_path))
            
            df = read_excel_auto(str(temp_path))
            if df is not None:
                df = clean_excel_data(df)
            
            return df
            
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
            # Get headers
            header_cells = await page.locator('table thead th, table.list thead th').all_text_contents()
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
        periods: List[int] = None,
        current_year_only: bool = False  # ★★★ 새 파라미터: 현재 연도만 수집 ★★★
    ) -> Dict[int, pd.DataFrame]:
        """
        Download ETRS dataset for all plan periods
        
        Args:
            dataset_name: Dataset name
            periods: List of periods (default: all)
            current_year_only: If True, only collect current year data (for auto mode)
            
        Returns:
            Dictionary: period -> DataFrame
        """
        if periods is None:
            periods = list(PLAN_PERIODS.keys())
        
        results = {}
        dataset = ETRS_DATASETS.get(dataset_name, {})
        has_impl_year = dataset.get('has_implementation_year', False)
        
        # ★★★ 현재 연도 계산 ★★★
        current_year = datetime.now().year
        
        for period in periods:
            logger.info(f"\n--- {dataset_name} {period}차 ---")
            
            # ★★★ 이행연도가 필요한 데이터셋: 각 연도별로 수집 후 병합 ★★★
            if has_impl_year:
                year_list = PLAN_PERIODS.get(period, {}).get('year_list', [])
                
                if not year_list:
                    logger.warning(f"{period}차 계획기간 연도 정보 없음")
                    continue
                
                # ★★★ current_year_only 모드: 현재 연도만 수집 ★★★
                if current_year_only:
                    if current_year in year_list:
                        year_list = [current_year]
                        logger.info(f"🎯 자동 수집 모드: {current_year}년만 수집")
                    else:
                        logger.info(f"⏭️ {period}차에 {current_year}년 없음, 스킵")
                        continue
                
                period_dfs = []
                for year in year_list:
                    logger.info(f"  📅 {year}년 이행연도 수집 중...")
                    df = await self.scrape_etrs_table(dataset_name, period, implementation_year=year)
                    
                    if df is not None and len(df) > 0:
                        period_dfs.append(df)
                        logger.info(f"    ✓ {year}년: {len(df)}행")
                    else:
                        logger.debug(f"    ✗ {year}년: 데이터 없음")
                
                # 모든 연도 데이터 병합
                if period_dfs:
                    combined_df = pd.concat(period_dfs, ignore_index=True)
                    combined_df['_계획기간'] = f"{period}차"
                    results[period] = combined_df
                    logger.info(f"✓ {period}차 합계: {len(combined_df)}행 ({len(period_dfs)}개 연도)")
                else:
                    logger.warning(f"✗ {period}차: 모든 연도 데이터 없음")
            
            # ★★★ 이행연도 불필요한 데이터셋: 기존 방식 ★★★
            else:
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
        periods: List[int] = None,
        current_year_only: bool = False  # ★★★ 새 파라미터 ★★★
    ) -> Dict[str, Dict[int, pd.DataFrame]]:
        """
        Download all ETRS datasets
        
        Args:
            periods: List of periods to download
            current_year_only: If True, only collect current year data (for auto mode)
        
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
            
            results = await self.download_etrs_dataset(
                dataset_name, 
                periods,
                current_year_only=current_year_only
            )
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
        include_ors: bool = True,
        current_year_only: bool = False  # ★★★ 새 파라미터 ★★★
    ) -> Dict[str, Any]:
        """
        Download all data from ETRS and ORS
        
        Args:
            etrs_periods: ETRS plan periods to download
            include_ors: Whether to include ORS data
            current_year_only: If True, only collect current year data (for auto mode)
            
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
        results['etrs'] = await self.download_all_etrs(
            etrs_periods, 
            current_year_only=current_year_only
        )
        
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
    debug_mode: bool = False,
    current_year_only: bool = False  # ★★★ 새 파라미터 ★★★
) -> Dict[str, Any]:
    """
    Run the scraper
    
    Args:
        etrs_only: Only download ETRS data
        ors_only: Only download ORS data
        periods: ETRS plan periods
        debug_mode: Enable debug mode
        current_year_only: If True, only collect current year data (for auto mode)
        
    Returns:
        Downloaded data
    """
    scraper = AllocationScraper(debug_mode=debug_mode)
    
    try:
        await scraper.initialize()
        
        if etrs_only:
            return {
                'etrs': await scraper.download_all_etrs(periods, current_year_only=current_year_only), 
                'ors': {}
            }
        elif ors_only:
            return {'etrs': {}, 'ors': await scraper.download_all_ors()}
        else:
            return await scraper.download_all(
                periods, 
                include_ors=True, 
                current_year_only=current_year_only
            )
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
