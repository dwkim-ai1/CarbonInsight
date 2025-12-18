"""
NGMS Data Update Main Script
국가온실가스종합관리시스템 데이터 업데이트 메인 스크립트

This script:
1. Downloads data from NGMS website
2. Updates Google Sheets with latest data
3. Stacks changes to history sheets

Historical Mode:
- When HISTORICAL_MODE=true, collects past data with specified filters
- Only appends to stack sheets, does not update NGMS_ sheets
"""

import asyncio
import os
import sys
import tempfile

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ngms_scraper import NGMSScraper
from google_sheets_handler import create_handler_from_env
from utils import setup_logging, get_current_timestamp, ensure_directory

logger = setup_logging()

# Debug directory for GitHub Actions
DEBUG_DIR = os.environ.get('DEBUG_DIR', '/tmp/ngms_debug')


def get_historical_params() -> dict:
    """
    Get historical data collection parameters from environment variables
    
    Returns:
        Dictionary with filter parameters
    """
    return {
        'historical_mode': os.environ.get('HISTORICAL_MODE', 'false').lower() == 'true',
        'plan_period': os.environ.get('PLAN_PERIOD', ''),
        'designation_year': os.environ.get('DESIGNATION_YEAR', ''),
        'emission_year': os.environ.get('EMISSION_YEAR', ''),
    }


async def main() -> dict:
    """
    Main execution function
    
    Returns:
        Dictionary with results for each data type
    """
    logger.info("=" * 60)
    logger.info("NGMS 데이터 업데이트 시작")
    logger.info(f"실행 시간: {get_current_timestamp()}")
    logger.info("=" * 60)
    
    # Check historical mode
    hist_params = get_historical_params()
    historical_mode = hist_params['historical_mode']
    
    if historical_mode:
        logger.info("★★★ 과거 데이터 수집 모드 ★★★")
        logger.info(f"  계획기간: {hist_params['plan_period'] or '전체'}")
        logger.info(f"  지정연도: {hist_params['designation_year'] or '전체'}")
        logger.info(f"  배출년도: {hist_params['emission_year'] or '전체'}")
    
    results = {
        'success': False,
        'data_types': {},
        'errors': [],
        'historical_mode': historical_mode
    }
    
    # Ensure debug directory exists
    debug_mode = os.environ.get('DEBUG_MODE', 'true').lower() == 'true'
    if debug_mode:
        ensure_directory(DEBUG_DIR)
        logger.info(f"디버그 모드 활성화 - 저장 위치: {DEBUG_DIR}")
    
    try:
        # Step 1: Initialize Google Sheets handler
        logger.info("\n[1/3] Google Sheets 연결 중...")
        sheets_handler = create_handler_from_env()
        logger.info("Google Sheets 연결 완료")
        
        # Step 2: Download data from NGMS
        logger.info("\n[2/3] NGMS 데이터 다운로드 중...")
        
        # Create scraper with debug directory
        download_dir = tempfile.mkdtemp()
        scraper = NGMSScraper(
            download_dir=download_dir,
            debug_mode=debug_mode
        )
        
        # Override debug directory to use the shared location
        if debug_mode:
            scraper.debug_dir = DEBUG_DIR
            ensure_directory(scraper.debug_dir)
        
        try:
            await scraper.initialize()
            
            # 과거 데이터 수집 모드면 필터 적용
            if historical_mode:
                downloaded_data = await scraper.download_all(
                    plan_period=hist_params['plan_period'],
                    designation_year=hist_params['designation_year'],
                    emission_year=hist_params['emission_year']
                )
            else:
                downloaded_data = await scraper.download_all()
        finally:
            await scraper.close()
        
        if not downloaded_data:
            raise Exception("다운로드된 데이터가 없습니다")
        
        logger.info(f"다운로드 완료: {list(downloaded_data.keys())}")
        
        # 최소 1개 이상의 데이터가 있으면 진행
        if len(downloaded_data) == 0:
            raise Exception("수집된 데이터가 없습니다")
        
        # Step 3: Update Google Sheets
        logger.info("\n[3/3] Google Sheets 업데이트 중...")
        
        for data_type, df in downloaded_data.items():
            try:
                logger.info(f"\n--- {data_type} 처리 중 ---")
                logger.info(f"데이터 크기: {df.shape}")
                
                # 과거 데이터 수집 모드: 스택 시트에만 추가
                if historical_mode:
                    result = sheets_handler.append_historical_data(data_type, df)
                    results['data_types'][data_type] = {
                        'success': True,
                        'rows': len(df),
                        'mode': 'historical',
                        'appended': result.get('rows_added', 0)
                    }
                    logger.info(f"✓ {data_type}: {len(df)}행 → 스택 시트에 {result.get('rows_added', 0)}행 추가")
                else:
                    # 일반 모드: NGMS_ 시트 업데이트 + 변경사항 스택
                    result = sheets_handler.process_update(data_type, df)
                    results['data_types'][data_type] = {
                        'success': True,
                        'rows': len(df),
                        'mode': 'normal',
                        'changes': result.get('has_changes', False)
                    }
                    logger.info(f"✓ {data_type}: {len(df)}행 수집됨")
                    
            except Exception as e:
                error_msg = f"{data_type}: {str(e)}"
                logger.error(f"✗ {error_msg}")
                results['errors'].append(error_msg)
                results['data_types'][data_type] = {
                    'success': False,
                    'error': str(e)
                }
        
        # Determine overall success
        successful_types = [k for k, v in results['data_types'].items() if v.get('success')]
        results['success'] = len(successful_types) > 0
        
    except Exception as e:
        logger.error(f"실행 중 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())
        results['errors'].append(str(e))
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("실행 결과 요약")
    logger.info("=" * 60)
    
    for data_type, info in results['data_types'].items():
        status = "✅ 성공" if info.get('success') else "❌ 실패"
        logger.info(f"\n{data_type}: {status}")
        if info.get('success'):
            if info.get('mode') == 'historical':
                logger.info(f"  - 추가된 행 수: {info.get('appended', 0)}건")
            else:
                logger.info(f"  - 레코드 수: {info.get('rows', 0)}건")
                logger.info(f"  - 변경 여부: {'있음' if info.get('changes') else '없음'}")
        else:
            logger.info(f"  - 오류: {info.get('error', 'Unknown')}")
    
    if results['errors']:
        logger.warning(f"\n총 {len(results['errors'])}개의 오류 발생")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"전체 결과: {'성공' if results['success'] else '실패'}")
    logger.info("=" * 60)
    
    return results


if __name__ == "__main__":
    result = asyncio.run(main())
    
    # Exit with appropriate code
    if not result['success']:
        sys.exit(1)
    sys.exit(0)
