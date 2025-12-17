"""
NGMS Debug Script
iframe 구조 분석 및 디버깅용 스크립트

이 스크립트는 NGMS 웹사이트의 구조를 분석하고
다운로드 버튼을 찾는 과정을 단계별로 확인합니다.

사용법:
    python debug_scraper.py [--headed]
    
옵션:
    --headed: 브라우저를 표시 모드로 실행 (기본: headless)
"""

import asyncio
import os
import sys
import argparse
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright

from config import NGMS_MAIN_URL, IFRAME_URLS


async def analyze_page_structure(headed: bool = False):
    """
    Analyze NGMS page structure for debugging
    
    Args:
        headed: Run browser in headed mode
    """
    print("=" * 60)
    print("NGMS 페이지 구조 분석")
    print(f"시작 시간: {datetime.now()}")
    print("=" * 60)
    
    # Create debug directory
    debug_dir = "debug_output"
    os.makedirs(debug_dir, exist_ok=True)
    
    async with async_playwright() as p:
        # Launch browser
        browser = await p.chromium.launch(
            headless=not headed,
            slow_mo=500 if headed else 100
        )
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            accept_downloads=True
        )
        
        page = await context.new_page()
        page.set_default_timeout(60000)
        
        try:
            # Step 1: Navigate to main page
            print("\n[Step 1] 메인 페이지 접속 중...")
            await page.goto(NGMS_MAIN_URL, wait_until='networkidle')
            await asyncio.sleep(3)
            
            # Save screenshot
            await page.screenshot(path=f"{debug_dir}/01_main_page.png", full_page=True)
            print(f"  - 스크린샷 저장: {debug_dir}/01_main_page.png")
            
            # Save HTML
            html = await page.content()
            with open(f"{debug_dir}/01_main_page.html", 'w', encoding='utf-8') as f:
                f.write(html)
            print(f"  - HTML 저장: {debug_dir}/01_main_page.html")
            
            # Step 2: Analyze frames
            print("\n[Step 2] 프레임 구조 분석...")
            frames = page.frames
            print(f"  - 전체 프레임 수: {len(frames)}")
            
            for i, frame in enumerate(frames):
                url = frame.url
                name = frame.name or "(no name)"
                print(f"  - Frame {i}: name='{name}', url={url[:80]}...")
            
            # Step 3: Find iframe elements
            print("\n[Step 3] iframe 요소 검색...")
            iframe_elements = await page.query_selector_all('iframe')
            print(f"  - iframe 요소 수: {len(iframe_elements)}")
            
            for i, iframe in enumerate(iframe_elements):
                src = await iframe.get_attribute('src') or ''
                id_attr = await iframe.get_attribute('id') or ''
                class_attr = await iframe.get_attribute('class') or ''
                print(f"  - iframe {i}: id='{id_attr}', class='{class_attr}', src={src[:60]}...")
            
            # Step 4: Try to find tab elements
            print("\n[Step 4] 탭 요소 검색...")
            tab_selectors = [
                '#mf_tac_layout_tab_50900501',
                '#mf_tac_layout_tab_50900502', 
                '#mf_tac_layout_tab_50900503',
                'li[id*="50900501"]',
                'li[id*="50900502"]',
                'li[id*="50900503"]',
                'a[menuno="50900501"]',
                'a[menuno="50900502"]',
                'a[menuno="50900503"]',
            ]
            
            for selector in tab_selectors:
                element = await page.query_selector(selector)
                if element:
                    text = await element.text_content() or ''
                    print(f"  - 발견: {selector} -> '{text.strip()}'")
                else:
                    print(f"  - 없음: {selector}")
            
            # Step 5: Check for specific iframe and analyze its content
            print("\n[Step 5] 할당대상업체 탭 iframe 분석...")
            
            # Try to click on 할당대상업체 tab first
            tab = await page.query_selector('#mf_tac_layout_tab_50900501 a')
            if tab:
                await tab.click()
                await asyncio.sleep(3)
                print("  - 할당대상업체 탭 클릭 완료")
            
            # Find the content iframe
            iframe_selector = '#mf_tac_layout_contents_50900501_body'
            iframe_element = await page.query_selector(iframe_selector)
            
            if iframe_element:
                print(f"  - iframe 발견: {iframe_selector}")
                
                # Get frame content
                frame = await iframe_element.content_frame()
                if frame:
                    print("  - iframe 내부 접근 성공")
                    
                    # Save iframe content
                    iframe_html = await frame.content()
                    with open(f"{debug_dir}/02_iframe_content.html", 'w', encoding='utf-8') as f:
                        f.write(iframe_html)
                    print(f"  - iframe HTML 저장: {debug_dir}/02_iframe_content.html")
                    
                    # Search for download buttons
                    print("\n[Step 6] 다운로드 버튼 검색 (iframe 내부)...")
                    button_selectors = [
                        'a:has-text("Excel 다운로드")',
                        'button:has-text("Excel 다운로드")',
                        'a:has-text("Excel")',
                        'input[value*="Excel"]',
                        'a:has-text("다운로드")',
                        'button:has-text("다운로드")',
                        '[onclick*="excel"]',
                        '[onclick*="Excel"]',
                        '[onclick*="download"]',
                        '.btn_excel',
                        '#excelBtn',
                    ]
                    
                    for selector in button_selectors:
                        try:
                            element = await frame.query_selector(selector)
                            if element:
                                text = await element.text_content() or ''
                                tag = await element.evaluate('el => el.tagName')
                                print(f"  - 발견: {selector} -> <{tag}> '{text.strip()}'")
                            else:
                                print(f"  - 없음: {selector}")
                        except Exception as e:
                            print(f"  - 오류 ({selector}): {e}")
                    
                    # List all buttons and links in iframe
                    print("\n[Step 7] iframe 내 모든 버튼/링크 목록...")
                    all_buttons = await frame.query_selector_all('button, a.btn, input[type="button"], input[type="submit"]')
                    for i, btn in enumerate(all_buttons[:20]):  # Limit to first 20
                        try:
                            text = await btn.text_content() or ''
                            tag = await btn.evaluate('el => el.tagName')
                            onclick = await btn.get_attribute('onclick') or ''
                            print(f"  - {i}: <{tag}> '{text.strip()[:30]}' onclick='{onclick[:40]}'")
                        except:
                            pass
            else:
                print(f"  - iframe 없음: {iframe_selector}")
            
            # Step 8: Try direct iframe URL access
            print("\n[Step 8] 직접 iframe URL 접속 테스트...")
            for name, url in IFRAME_URLS.items():
                print(f"\n  === {name} ===")
                print(f"  URL: {url}")
                
                new_page = await context.new_page()
                try:
                    await new_page.goto(url, wait_until='networkidle')
                    await asyncio.sleep(3)
                    
                    # Save screenshot
                    safe_name = name.replace(' ', '_')
                    await new_page.screenshot(path=f"{debug_dir}/direct_{safe_name}.png", full_page=True)
                    print(f"  - 스크린샷: {debug_dir}/direct_{safe_name}.png")
                    
                    # Save HTML
                    direct_html = await new_page.content()
                    with open(f"{debug_dir}/direct_{safe_name}.html", 'w', encoding='utf-8') as f:
                        f.write(direct_html)
                    print(f"  - HTML: {debug_dir}/direct_{safe_name}.html")
                    
                    # Search for download button
                    excel_btn = await new_page.query_selector('a:has-text("Excel"), button:has-text("Excel")')
                    if excel_btn:
                        text = await excel_btn.text_content() or ''
                        print(f"  - Excel 버튼 발견: '{text.strip()}'")
                    else:
                        print("  - Excel 버튼 없음")
                        
                except Exception as e:
                    print(f"  - 오류: {e}")
                finally:
                    await new_page.close()
            
            # Final screenshot
            await page.screenshot(path=f"{debug_dir}/99_final_state.png", full_page=True)
            
            print("\n" + "=" * 60)
            print("분석 완료!")
            print(f"디버그 파일 위치: {os.path.abspath(debug_dir)}")
            print("=" * 60)
            
            if headed:
                print("\n브라우저를 닫으려면 Enter를 누르세요...")
                input()
            
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description='NGMS 디버깅 스크립트')
    parser.add_argument('--headed', action='store_true', 
                       help='브라우저를 표시 모드로 실행')
    args = parser.parse_args()
    
    asyncio.run(analyze_page_structure(headed=args.headed))


if __name__ == "__main__":
    main()
