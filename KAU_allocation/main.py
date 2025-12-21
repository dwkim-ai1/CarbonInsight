"""
ETRS Data Update Main Script
배출권등록부시스템 데이터 업데이트 메인 스크립트

This script:
1. Downloads data from ETRS website
2. Updates Google Sheets with latest data
3. Tracks changes in history sheets
"""

import os
import sys

# Add current directory to path for absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DATASETS, PLAN_PERIODS
from etrs_scraper import ETRSScraper
from google_sheets_handler import create_handler_from_env
from utils import setup_logging, get_current_timestamp

logger = setup_logging()


def main() -> dict:
    """
    Main execution function
    
    Returns:
        Dictionary with results for each dataset/period
    """
    logger.info("=" * 60)
    logger.info("ETRS 데이터 업데이트 시작")
    logger.info(f"실행 시간: {get_current_timestamp()}")
    logger.info("=" * 60)
    
    results = {
        'success': False,
        'datasets': {},
        'errors': [],
    }
    
    try:
        # Step 1: Initialize Google Sheets handler
        logger.info("\n[1/2] Google Sheets 연결 중...")
        sheets_handler = create_handler_from_env()
        logger.info("✅ Google Sheets 연결 완료")
        
        # Step 2: Download and update data
        logger.info("\n[2/2] ETRS 데이터 수집 및 업데이트 중...")
        
        scraper = ETRSScraper()
        
        # 계획기간별 수집
        for period in PLAN_PERIODS.keys():
            logger.info(f"\n{'='*50}")
            logger.info(f"📅 계획기간 {period}차 ({PLAN_PERIODS[period]})")
            logger.info(f"{'='*50}")
            
            for dataset_name, dataset_config in DATASETS.items():
                try:
                    logger.info(f"\n--- {dataset_name} ---")
                    
                    # Download
                    df = scraper.download_excel(dataset_name, period)
                    
                    if df is None or len(df) == 0:
                        logger.warning(f"⚠️ {dataset_name} {period}차: 데이터 없음")
                        continue
                    
                    logger.info(f"📊 데이터 크기: {df.shape}")
                    logger.info(f"📋 컬럼: {list(df.columns)[:5]}...")
                    
                    # Process update (latest + stack)
                    result = sheets_handler.process_update(dataset_name, df, period)
                    
                    # Record result
                    key = f"{dataset_name}_{period}차"
                    results['datasets'][key] = {
                        'success': True,
                        'rows': len(df),
                        'has_changes': result.get('has_changes', False)
                    }
                    
                except Exception as e:
                    error_msg = f"{dataset_name} {period}차: {str(e)}"
                    logger.error(f"❌ {error_msg}")
                    import traceback
                    logger.debug(traceback.format_exc())
                    results['errors'].append(error_msg)
                    results['datasets'][f"{dataset_name}_{period}차"] = {
                        'success': False,
                        'error': str(e),
                    }
        
        # Determine overall success
        successful = [k for k, v in results['datasets'].items() if v.get('success')]
        results['success'] = len(successful) > 0
        
    except Exception as e:
        logger.error(f"실행 중 오류 발생: {e}")
        import traceback
        logger.error(traceback.format_exc())
        results['errors'].append(str(e))
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("📈 실행 결과 요약")
    logger.info("=" * 60)
    
    for key, info in results['datasets'].items():
        status = "✅ 성공" if info.get('success') else "❌ 실패"
        if info.get('success'):
            changes = "📝 변경있음" if info.get('has_changes') else "➖ 변경없음"
            logger.info(f"  {key}: {status} ({info.get('rows', 0)}행, {changes})")
        else:
            logger.info(f"  {key}: {status} - {info.get('error', 'Unknown')}")
    
    if results['errors']:
        logger.warning(f"\n⚠️ 총 {len(results['errors'])}개의 오류 발생")
    
    logger.info(f"\n{'='*60}")
    logger.info(f"🏁 전체 결과: {'✅ 성공' if results['success'] else '❌ 실패'}")
    logger.info("=" * 60)
    
    return results


if __name__ == "__main__":
    result = main()
    
    # Exit with appropriate code
    if not result['success']:
        sys.exit(1)
    sys.exit(0)
