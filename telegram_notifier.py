#!/usr/bin/env python3
"""
최적화된 텔레그램 알림 시스템
1. 중복 제거 통계 포함
2. 12:30 KST 최종 요약
3. 맞춤형 스케줄 정보
4. 스프레드시트 용량 절약 성과
"""

import requests
import gspread
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        """최적화된 텔레그램 알림 시스템 초기화"""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.telegram_api_url = f"https://api.telegram.org/bot{bot_token}"
        
    def test_bot_connection(self) -> bool:
        """봇 연결 상태 테스트"""
        try:
            url = f"{self.telegram_api_url}/getMe"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                bot_info = response.json()
                logger.info(f"✅ Bot 연결 성공: {bot_info['result']['username']}")
                return True
            else:
                logger.error(f"❌ Bot 토큰 오류: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Bot 연결 테스트 실패: {e}")
            return False
    
    def test_chat_access(self) -> bool:
        """채팅 접근 권한 테스트"""
        try:
            url = f"{self.telegram_api_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': '🔍 최적화된 연결 테스트 - 중복 제거 시스템 정상 작동',
                'disable_notification': True
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info("✅ 채팅 접근 테스트 성공")
                return True
            else:
                error_data = response.json() if response.headers.get('content-type') == 'application/json' else {'description': response.text}
                error_code = error_data.get('error_code', response.status_code)
                error_desc = error_data.get('description', 'Unknown error')
                
                logger.error(f"❌ 채팅 접근 테스트 실패: {error_code} - {error_desc}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 채팅 접근 테스트 중 오류: {e}")
            return False
        
    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """텔레그램 메시지 전송"""
        try:
            url = f"{self.telegram_api_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': parse_mode,
                'disable_web_page_preview': True
            }
            
            logger.info(f"최적화된 텔레그램 API 호출 시작")
            logger.info(f"메시지 길이: {len(message)} 문자")
            
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            
            logger.info("텔레그램 메시지 전송 성공")
            return True
            
        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 실패: {e}")
            return False

class OptimizedDataAnalyzer:
    def __init__(self, credentials_json: str, sheet_id: str):
        """최적화된 일일 데이터 분석기 초기화"""
        try:
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            self.worksheet = self.spreadsheet.worksheet('ets.KRX')
            logger.info("Google Sheets 연결 성공 (최적화 데이터 분석)")
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def get_today_optimized_data(self) -> Tuple[List[Dict], Dict]:
        """오늘 최적화된 수집 데이터 분석"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"오늘 날짜 최적화 데이터 분석 시작: {today}")
            
            # 모든 데이터 가져오기
            all_data = self.worksheet.get_all_records()
            logger.info(f"총 {len(all_data)}개 레코드 조회")
            
            # 오늘 데이터만 필터링
            today_data = [row for row in all_data if row.get('날짜') == today]
            logger.info(f"오늘 최적화 데이터: {len(today_data)}개 레코드")
            
            # 최적화 데이터 분석
            analysis = self._analyze_optimized_data(today_data, today)
            
            return today_data, analysis
            
        except Exception as e:
            logger.error(f"최적화 데이터 분석 중 오류: {e}")
            return [], {}
    
    def _analyze_optimized_data(self, today_data: List[Dict], today: str) -> Dict:
        """최적화된 데이터 분석 및 통계 생성"""
        try:
            analysis = {
                'date': today,
                'total_records': len(today_data),
                'successful_collections': 0,
                'failed_collections': 0,
                'data_sources': {},
                'extraction_methods': {},
                'optimization_performance': {},
                'collection_frequency_stats': {},
                'active_trading_symbols': [],
                'collection_times': [],
                'total_trading_volume': 0,
                'total_trading_value': 0,
                'unique_symbols': set(),
                'missing_data_count': 0,
                'last_update_time': '',
                'data_quality_issues': [],
                'skipped_duplicates': 0,
                'storage_efficiency': 0,
                'unique_collection_sessions': set(),
                'peak_trading_periods': [],
                'final_session_time': ''
            }
            
            if not today_data:
                analysis['status'] = 'NO_DATA'
                analysis['message'] = '오늘 최적화 시스템으로 수집된 데이터가 없습니다.'
                return analysis
            
            # 수집 세션별 분석
            for record in today_data:
                # 기본 통계
                analysis['unique_symbols'].add(record.get('종목명', ''))
                
                # 수집 시간 추적
                collection_time = record.get('수집시간', '')
                if collection_time:
                    analysis['collection_times'].append(collection_time)
                    analysis['unique_collection_sessions'].add(collection_time[:5])  # HH:MM 형태
                    if collection_time > analysis['last_update_time']:
                        analysis['last_update_time'] = collection_time
                
                # 데이터 소스 추적
                data_source = record.get('데이터소스', 'unknown')
                analysis['data_sources'][data_source] = analysis['data_sources'].get(data_source, 0) + 1
                
                # 추출 방법별 통계
                if 'playwright' in data_source:
                    extraction_method = data_source.split('_')[-1] if '_' in data_source else 'unknown'
                    analysis['extraction_methods'][extraction_method] = analysis['extraction_methods'].get(extraction_method, 0) + 1
                
                # 거래 단계별 통계 (최적화 스케줄 반영)
                trading_phase = record.get('거래단계', 'unknown')
                if trading_phase != 'unknown':
                    analysis['collection_frequency_stats'][trading_phase] = analysis['collection_frequency_stats'].get(trading_phase, 0) + 1
                
                # 거래량/거래대금 집계
                try:
                    volume = float(str(record.get('거래량', 0)).replace(',', ''))
                    trading_value = float(str(record.get('거래대금', 0)).replace(',', ''))
                    
                    analysis['total_trading_volume'] += volume
                    analysis['total_trading_value'] += trading_value
                    
                    # 활성 거래 종목 (거래량 > 0)
                    if volume > 0:
                        symbol_info = {
                            'symbol': record.get('종목명', ''),
                            'volume': volume,
                            'price': float(str(record.get('현재가', 0)).replace(',', '')),
                            'change': float(str(record.get('대비', 0)).replace(',', '')),
                            'collection_time': collection_time
                        }
                        analysis['active_trading_symbols'].append(symbol_info)
                        
                        # 피크 거래 시간대 분석
                        if volume > 1000:  # 1000톤 이상
                            analysis['peak_trading_periods'].append({
                                'time': collection_time,
                                'symbol': record.get('종목명', ''),
                                'volume': volume
                            })
                        
                except (ValueError, TypeError) as e:
                    analysis['missing_data_count'] += 1
                    logger.debug(f"데이터 파싱 오류: {e}")
            
            # 최적화 성능 분석
            optimization_stats = self._parse_optimization_context()
            if optimization_stats:
                analysis['optimization_performance'] = optimization_stats
                analysis['skipped_duplicates'] = optimization_stats.get('skipped', 0)
                total_potential = optimization_stats.get('total', analysis['total_records'])
                if total_potential > 0:
                    analysis['storage_efficiency'] = round((optimization_stats.get('skipped', 0) / total_potential * 100), 1)
            
            # 수집 세션 분석
            session_count = len(analysis['unique_collection_sessions'])
            if session_count > 0:
                analysis['collection_session_count'] = session_count
                
                # 12:30 최종 세션 확인
                if any('12:3' in time for time in analysis['collection_times']):
                    analysis['final_session_time'] = '12:30'
                    analysis['is_final_session'] = True
                else:
                    analysis['is_final_session'] = False
            
            # 성공/실패 분석 (최적화 기준)
            playwright_sources = ['playwright_dom', 'playwright_ajax', 'playwright_ocr', 'playwright_html']
            
            playwright_count = sum(analysis['data_sources'].get(src, 0) for src in playwright_sources)
            
            if playwright_count > 0:
                analysis['successful_collections'] = playwright_count
                analysis['status'] = 'SUCCESS'
                analysis['message'] = f'최적화 시스템 데이터 수집 성공 ({playwright_count}개)'
            else:
                analysis['failed_collections'] = analysis['total_records']
                analysis['status'] = 'FAILED'
                analysis['message'] = '최적화 시스템 데이터 수집 실패'
            
            # 데이터 품질 검사
            analysis['unique_symbols'] = list(analysis['unique_symbols'])
            expected_symbols = ['KAU25', 'KCU25', 'KOC21-26', 'KOC22-27', 'KOC23-28', 'i-KCU25']
            missing_symbols = set(expected_symbols) - set(analysis['unique_symbols'])
            
            if missing_symbols:
                analysis['data_quality_issues'].append(f"누락된 종목: {', '.join(missing_symbols)}")
            
            if analysis['storage_efficiency'] > 0:
                analysis['data_quality_issues'].append(f"중복 제거: {analysis['storage_efficiency']}% 용량 절약")
            
            # 활성 거래 종목 정렬 (거래량 기준)
            analysis['active_trading_symbols'].sort(key=lambda x: x['volume'], reverse=True)
            
            logger.info(f"최적화 데이터 분석 완료: {analysis['status']}")
            logger.info(f"용량 절약: {analysis['storage_efficiency']}% ({analysis['skipped_duplicates']}개 중복 제거)")
            
            return analysis
            
        except Exception as e:
            logger.error(f"최적화 데이터 분석 중 오류: {e}")
            return {
                'date': today,
                'status': 'ERROR',
                'message': f'최적화 분석 중 오류 발생: {str(e)}',
                'total_records': len(today_data)
            }
    
    def _parse_optimization_context(self) -> Dict:
        """환경변수에서 최적화 통계 파싱"""
        try:
            optimization_context = os.getenv('OPTIMIZATION_CONTEXT', '')
            optimization_stats = os.getenv('OPTIMIZATION_STATS', '{}')
            
            if optimization_stats and optimization_stats != '{}':
                stats = json.loads(optimization_stats)
                logger.info(f"최적화 통계 파싱 성공: {stats}")
                return stats
            
            return {}
            
        except Exception as e:
            logger.debug(f"최적화 통계 파싱 오류: {e}")
            return {}

class OptimizedMessageFormatter:
    @staticmethod
    def format_optimized_daily_summary(analysis: Dict, collection_status: str) -> str:
        """최적화된 일일 요약 메시지 포맷팅"""
        try:
            status_emoji = {
                'SUCCESS': '✅',
                'PARTIAL_SUCCESS': '⚠️',
                'FAILED': '❌',
                'NO_DATA': '📭',
                'ERROR': '🚨'
            }
            
            emoji = status_emoji.get(analysis.get('status', 'ERROR'), '❓')
            
            # 헤더 (최적화 시스템)
            message = f"{emoji} *KRX ETS 최적화 수집 결과*\n"
            message += f"📅 날짜: `{analysis.get('date', 'Unknown')}`\n"
            message += f"⏰ 마지막 업데이트: `{analysis.get('last_update_time', 'Unknown')}`\n"
            message += f"🚀 수집 방식: `최적화 시스템 (중복 제거)`\n\n"
            
            # 최적화 성과
            optimization = analysis.get('optimization_performance', {})
            storage_efficiency = analysis.get('storage_efficiency', 0)
            skipped_duplicates = analysis.get('skipped_duplicates', 0)
            
            if storage_efficiency > 0:
                message += f"*💾 최적화 성과*\n"
                message += f"• 용량 절약: `{storage_efficiency}%`\n"
                message += f"• 중복 제거: `{skipped_duplicates}개`\n"
                message += f"• 실제 업데이트: `{analysis.get('total_records', 0)}개`\n\n"
            
            # 수집 현황
            message += f"*📊 수집 현황*\n"
            message += f"• 총 레코드: `{analysis.get('total_records', 0)}개`\n"
            message += f"• 성공 수집: `{analysis.get('successful_collections', 0)}개`\n"
            message += f"• 수집 세션: `{analysis.get('collection_session_count', 0)}회`\n"
            message += f"• 상태: `{analysis.get('message', 'Unknown')}`\n\n"
            
            # 수집 주기별 통계
            frequency_stats = analysis.get('collection_frequency_stats', {})
            if frequency_stats:
                message += f"*⏰ 수집 주기별 통계*\n"
                for phase, count in frequency_stats.items():
                    phase_name = {
                        'order_acceptance': '📝 주문접수 (30분)',
                        'real_time_trading_intensive': '⚡ 실시간거래 (10분)',
                        'closing_preparation': '📊 종가준비 (30분)',
                        'closing_price_decision': '🏁 종가체결',
                        'final_settlement': '🎯 최종마감',
                        'manual': '🔧 수동실행'
                    }.get(phase, f'❓ {phase}')
                    message += f"• {phase_name}: `{count}건`\n"
                message += "\n"
            
            # 활성 거래 종목
            active_symbols = analysis.get('active_trading_symbols', [])
            if active_symbols:
                message += f"*💹 활성 거래 종목*\n"
                for symbol in active_symbols[:5]:  # 상위 5개만
                    change_emoji = "📈" if symbol['change'] > 0 else "📉" if symbol['change'] < 0 else "➡️"
                    time_str = symbol.get('collection_time', '')[:5]  # HH:MM
                    
                    message += f"• `{symbol['symbol']}`: {symbol['price']:,.0f}원 "
                    message += f"({symbol['change']:+.0f}) {change_emoji} `{symbol['volume']:,.0f}톤` "
                    message += f"({time_str})\n"
                message += "\n"
            else:
                message += f"*💹 거래 현황*\n"
                message += f"• 오늘 활성 거래 없음\n"
                message += f"• 총 거래량: `{analysis.get('total_trading_volume', 0):,.0f}톤`\n\n"
            
            # 피크 거래 시간대
            peak_periods = analysis.get('peak_trading_periods', [])
            if peak_periods:
                message += f"*🔥 피크 거래 시간대*\n"
                for peak in peak_periods[:3]:  # 상위 3개만
                    time_str = peak.get('time', '')[:5]
                    message += f"• `{time_str}`: {peak['symbol']} `{peak['volume']:,.0f}톤`\n"
                message += "\n"
            
            # 12:30 최종 세션 확인
            if analysis.get('is_final_session', False):
                message += f"*🎯 최종 마감*\n"
                message += f"• 12:30 KST 최종 수집 완료\n"
                message += f"• 일일 거래 데이터 확정\n"
                message += f"• 다음 업데이트: 내일 09:00 KST\n\n"
            
            # 데이터 품질 이슈
            quality_issues = analysis.get('data_quality_issues', [])
            if quality_issues:
                message += f"*⚠️ 데이터 품질 정보*\n"
                for issue in quality_issues:
                    message += f"• {issue}\n"
                message += "\n"
            
            # GitHub Action 상태
            if collection_status:
                action_emoji = "✅" if collection_status == "success" else "❌"
                message += f"*🤖 GitHub Action*\n"
                message += f"• 상태: {action_emoji} `{collection_status}`\n"
                message += f"• 시스템: `최적화 시스템`\n\n"
            
            # 푸터
            message += f"---\n"
            message += f"🕒 리포트 생성: `{datetime.now().strftime('%H:%M:%S')} KST`\n"
            message += f"🚀 수집 방식: `최적화 시스템 (중복 제거)`\n"
            message += f"💾 용량 절약: `{storage_efficiency}%`\n"
            message += f"🔗 [GitHub Repository](https://github.com/your-repo/krx-optimized-collector)"
            
            return message
            
        except Exception as e:
            logger.error(f"최적화 메시지 포맷팅 중 오류: {e}")
            return f"❌ *최적화 일일 요약 생성 실패*\n\n오류: {str(e)}\n\n📅 날짜: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    @staticmethod
    def format_optimization_alert(error_message: str) -> str:
        """최적화 시스템 오류 알림 메시지"""
        message = f"🚨 *최적화 KRX 데이터 수집 오류*\n\n"
        message += f"⏰ 시간: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST`\n"
        message += f"❌ 오류: `{error_message}`\n"
        message += f"🚀 시스템: `최적화 시스템 (중복 제거)`\n\n"
        message += f"🔧 확인 필요:\n"
        message += f"• GitHub Actions 로그\n"
        message += f"• 최적화 알고리즘 상태\n"
        message += f"• 중복 제거 로직\n"
        message += f"• Google Sheets 접근 권한"
        return message

def main():
    """최적화된 메인 실행 함수"""
    try:
        logger.info("=== 최적화된 텔레그램 일일 요약 및 알림 시작 ===")
        
        # 환경변수 확인
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        collection_status = os.getenv('COLLECTION_STATUS', '')
        collection_frequency = os.getenv('COLLECTION_FREQUENCY', 'unknown')
        is_final_session = os.getenv('IS_FINAL_SESSION', 'false').lower() == 'true'
        force_telegram = os.getenv('FORCE_TELEGRAM', 'false').lower() == 'true'
        
        if not telegram_bot_token or not telegram_chat_id:
            logger.error("텔레그램 인증 정보가 없습니다")
            return
            
        if not creds_json or not sheet_id:
            logger.error("Google Sheets 인증 정보가 없습니다")
            return
        
        logger.info(f"수집 상태: {collection_status}")
        logger.info(f"수집 주기: {collection_frequency}")
        logger.info(f"최종 세션: {is_final_session}")
        logger.info(f"강제 전송: {force_telegram}")
        
        # 텔레그램 알림 시스템 초기화
        telegram = TelegramNotifier(telegram_bot_token, telegram_chat_id)
        
        # 연결 테스트
        bot_ok = telegram.test_bot_connection()
        chat_ok = telegram.test_chat_access()
        
        if not bot_ok or not chat_ok:
            logger.error("텔레그램 연결 실패")
            return
        
        # 최적화된 데이터 분석기 초기화
        analyzer = OptimizedDataAnalyzer(creds_json, sheet_id)
        
        # 오늘 데이터 분석
        today_data, analysis = analyzer.get_today_optimized_data()
        logger.info(f"최적화 데이터 분석 완료: {analysis.get('status', 'Unknown')}")
        
        # 알림 조건 확인 (최적화 기준)
        should_alert = (
            analysis.get('status') in ['FAILED', 'ERROR', 'NO_DATA'] or  # 심각한 오류
            collection_status == 'failed' or  # GitHub Action 실패
            is_final_session or  # 12:30 최종 세션
            force_telegram  # 강제 전송
        )
        
        # 메시지 생성 및 전송
        if should_alert:
            if is_final_session:
                logger.info("🎯 12:30 KST 최종 일일 요약 전송")
            elif force_telegram:
                logger.info("🚀 강제 텔레그램 전송")
            else:
                logger.info("⚠️ 알림 조건 충족 - 최적화 상세 요약 전송")
            
            message = OptimizedMessageFormatter.format_optimized_daily_summary(analysis, collection_status)
        else:
            logger.info("✅ 최적화 시스템 정상 상태 - 알림 건너뜀")
            return
        
        # 텔레그램 메시지 전송
        success = telegram.send_message(message)
        
        if success:
            logger.info("✅ 최적화 텔레그램 알림 전송 성공")
            print("✅ 최적화 텔레그램 일일 요약 전송 완료!")
            print(f"📊 분석 결과: {analysis.get('status', 'Unknown')}")
            print(f"🚀 시스템: 최적화 (중복 제거)")
            print(f"💾 용량 절약: {analysis.get('storage_efficiency', 0)}%")
            print(f"📱 메시지 길이: {len(message)} 문자")
            
            if analysis.get('storage_efficiency', 0) > 0:
                print(f"⚡ 중복 제거: {analysis.get('skipped_duplicates', 0)}개")
                
        else:
            logger.error("❌ 최적화 텔레그램 알림 전송 실패")
            print("❌ 텔레그램 알림 전송 실패")
            
    except Exception as e:
        logger.error(f"❌ 최적화 텔레그램 알림 시스템 오류: {e}")
        print(f"❌ 오류: {e}")
        
        # 긴급 오류 알림 시도
        try:
            if 'telegram' in locals():
                error_message = OptimizedMessageFormatter.format_optimization_alert(str(e))
                telegram.send_message(error_message)
        except:
            pass

if __name__ == "__main__":
    main()
