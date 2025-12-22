"""
ETRS/ORS Data Update Main Script
배출권등록부/상쇄등록부 데이터 업데이트 메인 스크립트

총 32개 시트 관리:
- ETRS 8개 × 2(최신+이력) = 16개 시트
- ORS 8개 × 2(최신+이력) = 16개 시트

★★★ 변경사항 (v1.1) ★★★
1. CURRENT_YEAR_ONLY 환경변수 지원 (자동 수집 모드)
2. 인증배출량 등 이행연도 데이터셋: 기존 데이터에 새 연도 데이터 merge
3. 이력 시트: 변경분만 append (기존과 동일)
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etrs_scraper import AllocationScraper
from google_sheets_handler import create_handler_from_env
from utils import setup_logging, get_current_timestamp, ensure_directory

logger = setup_logging()

# Debug directory
DEBUG_DIR = os.environ.get('DEBUG_DIR', '/tmp/allocation_debug')


async def main() -> dict:
    """
    Main execution function
    
    Returns:
        Dictionary with results
    """
    logger.info("=" * 60)
    logger.info("🚀 ETRS/ORS 데이터 업데이트 시작")
    logger.info(f"⏰ 실행 시간: {get_current_timestamp()}")
    logger.info("=" * 60)
    
    # Check options from environment
    etrs_only = os.environ.get('ETRS_ONLY', 'false').lower() == 'true'
    ors_only = os.environ.get('ORS_ONLY', 'false').lower() == 'true'
    debug_mode = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
    
    # ★★★ 자동 수집 모드: 현재 연도만 수집 ★★★
    current_year_only = os.environ.get('CURRENT_YEAR_ONLY', 'false').lower() == 'true'
    
    # Parse periods
    periods_str = os.environ.get('ETRS_PERIODS', '1,2,3')
    try:
        periods = [int(p.strip()) for p in periods_str.split(',')]
    except:
        periods = [1, 2, 3]
    
    logger.info(f"설정:")
    logger.info(f"  - ETRS: {'활성화' if not ors_only else '비활성화'}")
    logger.info(f"  - ORS: {'활성화' if not etrs_only else '비활성화'}")
    logger.info(f"  - 계획기간: {periods}")
    logger.info(f"  - 현재 연도만 수집: {'예' if current_year_only else '아니오'}")
    logger.info(f"  - 디버그 모드: {'활성화' if debug_mode else '비활성화'}")
    
    if current_year_only:
        current_year = datetime.now().year
        logger.info(f"  - 대상 연도: {current_year}년")
    
    results = {
        'success': False,
        'etrs': {},
        'ors': {},
        'errors': []
    }
    
    # Setup debug directory
    if debug_mode:
        ensure_directory(DEBUG_DIR)
        logger.info(f"📁 디버그 디렉토리: {DEBUG_DIR}")
    
    try:
        # Step 1: Initialize Google Sheets handler
        logger.info("\n[1/3] 📊 Google Sheets 연결 중...")
        sheets_handler = create_handler_from_env()
        logger.info("✅ Google Sheets 연결 완료")
        
        # Step 2: Download data
        logger.info("\n[2/3] 📥 데이터 다운로드 중...")
        
        download_dir = tempfile.mkdtemp()
        scraper = AllocationScraper(
            download_dir=download_dir,
            debug_mode=debug_mode
        )
        
        if debug_mode:
            scraper.debug_dir = DEBUG_DIR
            ensure_directory(str(scraper.debug_dir))
        
        try:
            await scraper.initialize()
            
            # Download data
            if etrs_only:
                downloaded = {
                    'etrs': await scraper.download_all_etrs(
                        periods, 
                        current_year_only=current_year_only
                    ),
                    'ors': {}
                }
            elif ors_only:
                downloaded = {
                    'etrs': {},
                    'ors': await scraper.download_all_ors()
                }
            else:
                downloaded = await scraper.download_all(
                    periods, 
                    include_ors=True,
                    current_year_only=current_year_only
                )
                
        finally:
            await scraper.close()
        
        # Check if we have any data
        etrs_count = sum(len(pd) for pd in downloaded.get('etrs', {}).values())
        ors_count = len(downloaded.get('ors', {}))
        
        if etrs_count == 0 and ors_count == 0:
            raise Exception("다운로드된 데이터가 없습니다")
        
        logger.info(f"✅ 다운로드 완료: ETRS {etrs_count}개, ORS {ors_count}개 데이터셋")
        
        # Step 3: Update Google Sheets
        logger.info("\n[3/3] 📤 Google Sheets 업데이트 중...")
        
        # Update ETRS
        for dataset_name, period_data in downloaded.get('etrs', {}).items():
            try:
                # ★★★ 자동 수집 모드에서는 incremental update 사용 ★★★
                if current_year_only:
                    result = sheets_handler.process_etrs_update_incremental(
                        dataset_name, 
                        period_data
                    )
                else:
                    result = sheets_handler.process_etrs_update(
                        dataset_name, 
                        period_data
                    )
                
                results['etrs'][dataset_name] = {
                    'success': result.get('success', False),
                    'rows': sum(len(df) for df in period_data.values())
                }
                
                if result.get('success'):
                    logger.info(f"✓ ETRS {dataset_name}: 큐 추가 완료")
                else:
                    logger.warning(f"✗ ETRS {dataset_name}: 큐 추가 실패")
                    
            except Exception as e:
                error_msg = f"ETRS {dataset_name}: {str(e)}"
                logger.error(f"❌ {error_msg}")
                results['errors'].append(error_msg)
                results['etrs'][dataset_name] = {'success': False, 'error': str(e)}
        
        # Update ORS (ORS는 항상 전체 교체)
        for dataset_name, df in downloaded.get('ors', {}).items():
            try:
                result = sheets_handler.process_ors_update(dataset_name, df)
                results['ors'][dataset_name] = {
                    'success': result.get('success', False),
                    'rows': len(df)
                }
                
                if result.get('success'):
                    logger.info(f"✓ ORS {dataset_name}: 큐 추가 완료")
                else:
                    logger.warning(f"✗ ORS {dataset_name}: 큐 추가 실패")
                    
            except Exception as e:
                error_msg = f"ORS {dataset_name}: {str(e)}"
                logger.error(f"❌ {error_msg}")
                results['errors'].append(error_msg)
                results['ors'][dataset_name] = {'success': False, 'error': str(e)}
        
        # ★★★ 모든 업데이트를 한 번에 플러시 (API 쿼터 보호) ★★★
        logger.info("\n[3-2/3] 📝 Google Sheets 플러시 중...")
        flush_result = sheets_handler.flush_all_updates()
        
        if flush_result.get('failed', 0) > 0:
            results['errors'].append(f"플러시 실패: {flush_result['failed']}개 시트")
        
        logger.info(f"✅ 플러시 완료: {flush_result.get('updated', 0)}개 시트 업데이트")
        
        # Determine success
        etrs_success = sum(1 for r in results['etrs'].values() if r.get('success'))
        ors_success = sum(1 for r in results['ors'].values() if r.get('success'))
        results['success'] = (etrs_success + ors_success) > 0
        
    except Exception as e:
        logger.error(f"❌ 실행 중 오류: {e}")
        import traceback
        logger.error(traceback.format_exc())
        results['errors'].append(str(e))
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("📋 실행 결과 요약")
    logger.info("=" * 60)
    
    logger.info("\n[ETRS - 배출권등록부]")
    for name, info in results['etrs'].items():
        status = "✅" if info.get('success') else "❌"
        rows = info.get('rows', 0)
        logger.info(f"  {status} {name}: {rows}행")
    
    logger.info("\n[ORS - 상쇄등록부]")
    for name, info in results['ors'].items():
        status = "✅" if info.get('success') else "❌"
        rows = info.get('rows', 0)
        logger.info(f"  {status} {name}: {rows}행")
    
    if results['errors']:
        logger.warning(f"\n⚠️ 오류 {len(results['errors'])}건:")
        for err in results['errors']:
            logger.warning(f"  - {err}")
    
    overall = "✅ 성공" if results['success'] else "❌ 실패"
    logger.info(f"\n{'='*60}")
    logger.info(f"전체 결과: {overall}")
    logger.info("=" * 60)
    
    return results


def run():
    """Synchronous entry point"""
    return asyncio.run(main())


if __name__ == "__main__":
    result = run()
    sys.exit(0 if result['success'] else 1)
