"""
NGMS Data Update Main Script
국가온실가스종합관리시스템 데이터 업데이트 메인 스크립트

This script:
1. Downloads data from NGMS website
2. Updates Google Sheets with latest data
3. Stacks changes to history sheets
"""

import asyncio
import os
import sys
from typing import Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ngms_scraper import run_scraper
from google_sheets_handler import create_handler_from_env
from utils import setup_logging, get_current_timestamp

logger = setup_logging()


async def main() -> Dict[str, Any]:
    """
    Main execution function
    
    Returns:
        Dictionary with results for each data type
    """
    logger.info("=" * 60)
    logger.info("NGMS 데이터 업데이트 시작")
    logger.info(f"실행 시간: {get_current_timestamp()}")
    logger.info("=" * 60)
    
    results = {
        'success': False,
        'data_types': {},
        'errors': []
    }
    
    try:
        # Step 1: Initialize Google Sheets handler
        logger.info("\n[1/3] Google Sheets 연결 중...")
        sheets_handler = create_handler_from_env()
        logger.info("Google Sheets 연결 완료")
        
        # Step 2: Download data from NGMS
        logger.info("\n[2/3] NGMS 데이터 다운로드 중...")
        debug_mode = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
        downloaded_data = await run_scraper(debug_mode=debug_mode)
        
        if not downloaded_data:
            raise Exception("다운로드된 데이터가 없습니다")
        
        logger.info(f"다운로드 완료: {list(downloaded_data.keys())}")
        
        # Step 3: Update Google Sheets
        logger.info("\n[3/3] Google Sheets 업데이트 중...")
        
        for data_type, df in downloaded_data.items():
            try:
                logger.info(f"\n--- {data_type} 처리 중 ---")
                logger.info(f"데이터 크기: {df.shape}")
                
                result = sheets_handler.process_update(data_type, df)
                results['data_types'][data_type] = {
                    'success': True,
                    'rows': len(df),
                    'has_changes': result['has_changes'],
                    'summary': result['summary']
                }
                
            except Exception as e:
                error_msg = f"{data_type} 업데이트 실패: {str(e)}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                results['data_types'][data_type] = {
                    'success': False,
                    'error': str(e)
                }
        
        results['success'] = len(results['errors']) == 0
        
    except Exception as e:
        error_msg = f"전체 프로세스 실패: {str(e)}"
        logger.error(error_msg)
        results['errors'].append(error_msg)
    
    # Print summary
    logger.info("\n" + "=" * 60)
    logger.info("실행 결과 요약")
    logger.info("=" * 60)
    
    for data_type, result in results['data_types'].items():
        status = "✅ 성공" if result.get('success') else "❌ 실패"
        logger.info(f"\n{data_type}: {status}")
        if result.get('success'):
            logger.info(f"  - 레코드 수: {result.get('rows', 0)}건")
            logger.info(f"  - 변경 여부: {'있음' if result.get('has_changes') else '없음'}")
        else:
            logger.info(f"  - 오류: {result.get('error', 'Unknown')}")
    
    if results['errors']:
        logger.warning(f"\n총 {len(results['errors'])}개의 오류 발생")
    
    logger.info("\n" + "=" * 60)
    logger.info(f"전체 결과: {'성공' if results['success'] else '실패'}")
    logger.info("=" * 60)
    
    return results


def run():
    """Entry point for script execution"""
    try:
        results = asyncio.run(main())
        
        # Exit with appropriate code
        if results['success']:
            sys.exit(0)
        else:
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중단됨")
        sys.exit(130)
    except Exception as e:
        logger.error(f"예상치 못한 오류: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()
