"""
ETRS Data Update Main Script
배출권등록부시스템 데이터 업데이트 메인 스크립트
"""

import os
import sys
from datetime import datetime

# Add parent directory to path for imports
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
        Dictionary with results
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
        
        # 수집할 데이터셋 (현재 확인된 것만)
        active_datasets = ["사전할당량"]  # 다른 데이터셋은 엔드포인트 확인 후 추가
        
        # 계획기간별 수집
        for period in PLAN_PERIODS.keys():
            logger.info(f"\n--- 계획기간 {period}차 ---")
            
            for dataset_name in active_datasets:
                try:
                    # Download
                    df = scraper.download_excel(dataset_name, period)
                    
                    if df is None or len(df) == 0:
                        logger.warning(f"⚠️ {dataset_name} {period}차: 데이터 없음")
                        continue
                    
                    # Update Google Sheets
                    sheet_name = f"{DATASETS[dataset_name]['sheet_prefix']}_{period}차"
                    sheets_handler.update_sheet(sheet_name, df)
                    
                    # Record result
                    key = f"{dataset_name}_{period}차"
                    results['datasets'][key] = {
                        'success': True,
                        'rows': len(df),
                    }
                    
                except Exception as e:
                    error_msg = f"{dataset_name} {period}차: {str(e)}"
                    logger.error(f"❌ {error_msg}")
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
    logger.info("실행 결과 요약")
    logger.info("=" * 60)
    
    for key, info in results['datasets'].items():
        status = "✅ 성공" if info.get('success') else "❌ 실패"
        if info.get('success'):
            logger.info(f"{key}: {status} ({info.get('rows', 0)}행)")
        else:
            logger.info(f"{key}: {status} - {info.get('error', 'Unknown')}")
    
    if results['errors']:
        logger.warning(f"\n총 {len(results['errors'])}개의 오류 발생")
    
    logger.info(f"\n전체 결과: {'성공' if results['success'] else '실패'}")
    logger.info("=" * 60)
    
    return results


if __name__ == "__main__":
    result = main()
    
    # Exit with appropriate code
    if not result['success']:
        sys.exit(1)
    sys.exit(0)
