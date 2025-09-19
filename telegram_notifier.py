#!/usr/bin/env python3
"""
최적화된 텔레그램 알림 시스템 v3.0
1. 12:30 KST 최종 세션 전용 알림
2. KRX 시트 요약 정보 포함
3. 15:00 일일요약 제거
4. 중복 제거 통계 포함
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
                'text': '🔍 KRX v3.0 연결 테스트 - 12:30 전용 알림 시스템',
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
        """최적화된 데이터 분석기 초기화 (v3.0)"""
        try:
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            
            # 두 개의 워크시트 접근
            self.worksheet = self.spreadsheet.worksheet('ets.KRX')
            try:
                self.krx_worksheet = self.spreadsheet.worksheet('KRX')
                self.krx_available = True
            except:
                self.krx_available = False
                logger.warning("KRX 시트를 찾을 수 없음")
                
            logger.info("Google Sheets 연결 성공 (v3.0 - 이중 시트)")
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def get_today_final_analysis(self) -> Tuple[List[Dict], Dict]:
        """12:30 최종 세션 데이터 분석"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"12:30 최종 세션 데이터 분석 시작: {today}")
            
            # ets.KRX 시트에서 오늘 데이터 조회
            all_data = self.worksheet.get_all_records()
            today_data = [row for row in all_data if row.get('날짜') == today]
            logger.info(f"ets.KRX 오늘 데이터: {len(today_data)}개 레코드")
            
            # KRX 시트에서 최종 요약 확인
            krx_summary = self._get_krx_summary(today)
            
            # 최종 분석 생성
            analysis = self._analyze_final_session(today_data, krx_summary, today)
            
            return today_data, analysis
            
        except Exception as e:
            logger.error(f"최종 세션 데이터 분석 중 오류: {e}")
            return [], {}
    
    def _get_krx_summary(self, target_date: str) -> Dict:
        """KRX 시트에서 최종 요약 데이터 조회"""
        try:
            if not self.krx_available:
                return {}
            
            krx_data = self.krx_worksheet.get_all_records()
            today_krx = [row for row in krx_data if row.get('날짜') == target_date]
            
            if not today_krx:
                return {}
            
            # 최신 KRX 요약 데이터 분석
            summary = {
                'total_symbols': len(today_krx),
                'active_symbols': len([row for row in today_krx if float(str(row.get('거래량', 0)).replace(',', '')) > 0]),
                'total_volume': sum(float(str(row.get('거래량', 0)).replace(',', '')) for row in today_krx),
                'total_value': sum(float(str(row.get('거래대금', 0)).replace(',', '')) for row in today_krx),
                'symbols_with_change': len([row for row in today_krx if float(str(row.get('대비', 0)).replace(',', '')) != 0]),
                'krx_updated': True
            }
            
            logger.info(f"KRX 시트 요약: {summary['total_symbols']}개 종목, {summary['active_symbols']}개 활성")
            return summary
            
        except Exception as e:
            logger.warning(f"KRX 시트 요약 조회 오류: {e}")
            return {'krx_updated': False}
    
    def _analyze_final_session(self, today_data: List[Dict], krx_summary: Dict, today: str) -> Dict:
        """12:30 최종 세션 분석"""
        try:
            analysis = {
                'date': today,
                'session_type': 'final_12_30',
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
                'last_update_time': '',
                'data_quality_issues': [],
                'skipped_duplicates': 0,
                'storage_efficiency': 0,
                'unique_collection_sessions': set(),
                'peak_trading_periods': [],
                'final_session_confirmed': False,
                'krx_summary': krx_summary,
                'session_coverage': {},
                'daily_statistics': {}
            }
            
            if not today_data:
                analysis['status'] = 'NO_DATA'
                analysis['message'] = '12:30 최종 세션 데이터가 없습니다.'
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
                
                # 거래 단계별 통계
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
                            'change_rate': float(str(record.get('등락률', 0)).replace(',', '')),
                            'collection_time': collection_time
                        }
                        analysis['active_trading_symbols'].append(symbol_info)
                        
                except (ValueError, TypeError) as e:
                    logger.debug(f"데이터 파싱 오류: {e}")
            
            # 12:30 최종 세션 확인
            twelve_thirty_sessions = [time for time in analysis['collection_times'] if time.startswith('12:3')]
            if twelve_thirty_sessions:
                analysis['final_session_confirmed'] = True
                analysis['final_session_time'] = max(twelve_thirty_sessions)
            
            # 세션 커버리지 분석
            analysis['session_coverage'] = self._analyze_session_coverage(analysis['collection_times'])
            
            # 일일 통계
            analysis['daily_statistics'] = {
                'total_sessions': len(analysis['unique_collection_sessions']),
                'data_points': len(today_data),
                'unique_symbols_count': len(analysis['unique_symbols']),
                'active_trading_count': len(analysis['active_trading_symbols'])
            }
            
            # 최적화 성능 분석
            optimization_stats = self._parse_optimization_context()
            if optimization_stats:
                analysis['optimization_performance'] = optimization_stats
                analysis['skipped_duplicates'] = optimization_stats.get('skipped', 0)
                total_potential = optimization_stats.get('total', analysis['total_records'])
                if total_potential > 0:
                    analysis['storage_efficiency'] = round((optimization_stats.get('skipped', 0) / total_potential * 100), 1)
            
            # 성공/실패 분석
            playwright_sources = ['playwright_dom', 'playwright_ajax', 'playwright_ocr', 'playwright_html']
            playwright_count = sum(analysis['data_sources'].get(src, 0) for src in playwright_sources)
            
            if playwright_count > 0 and analysis['final_session_confirmed']:
                analysis['successful_collections'] = playwright_count
                analysis['status'] = 'SUCCESS'
                analysis['message'] = f'12:30 최종 세션 성공 ({playwright_count}개)'
            elif analysis['final_session_confirmed']:
                analysis['status'] = 'PARTIAL_SUCCESS'
                analysis['message'] = '12:30 최종 세션 부분 성공'
            else:
                analysis['failed_collections'] = analysis['total_records']
                analysis['status'] = 'FAILED'
                analysis['message'] = '12:30 최종 세션 실패'
            
            # 활성 거래 종목 정렬 (거래량 기준)
            analysis['active_trading_symbols'].sort(key=lambda x: x['volume'], reverse=True)
            analysis['unique_symbols'] = list(analysis['unique_symbols'])
            
            logger.info(f"12:30 최종 세션 분석 완료: {analysis['status']}")
            logger.info(f"KRX 시트 업데이트: {krx_summary.get('krx_updated', False)}")
            
            return analysis
            
        except Exception as e:
            logger.error(f"최종 세션 분석 중 오류: {e}")
            return {
                'date': today,
                'status': 'ERROR',
                'session_type': 'final_12_30',
                'message': f'최종 세션 분석 중 오류: {str(e)}',
                'total_records': len(today_data)
            }
    
    def _analyze_session_coverage(self, collection_times: List[str]) -> Dict:
        """세션 커버리지 분석"""
        try:
            sessions = {}
            for time_str in collection_times:
                hour_min = time_str[:5]  # HH:MM
                hour = int(hour_min.split(':')[0])
                
                if 9 <= hour <= 10:
                    phase = "주문접수 (09:00-10:00)"
                elif 10 <= hour < 12:
                    phase = "실시간거래 (10:00-12:00)"
                elif hour == 12:
                    phase = "종가체결 (12:00-12:30)"
                else:
                    phase = "기타시간"
                
                sessions[phase] = sessions.get(phase, 0) + 1
            
            return sessions
        except Exception as e:
            logger.debug(f"세션 커버리지 분석 오류: {e}")
            return {}
    
    def _parse_optimization_context(self) -> Dict:
        """환경변수에서 최적화 통계 파싱"""
        try:
            optimization_stats = os.getenv('OPTIMIZATION_STATS', '{}')
            
            if optimization_stats and optimization_stats != '{}':
                stats = json.loads(optimization_stats)
                logger.info(f"최적화 통계 파싱 성공: {stats}")
                return stats
            
            return {}
            
        except Exception as e:
            logger.debug(f"최적화 통계 파싱 오류: {e}")
            return {}

class FinalSessionMessageFormatter:
    @staticmethod
    def format_final_session_summary(analysis: Dict, collection_status: str) -> str:
        """12:30 최종 세션 전용 메시지 포맷팅"""
        try:
            status_emoji = {
                'SUCCESS': '✅',
                'PARTIAL_SUCCESS': '⚠️',
                'FAILED': '❌',
                'NO_DATA': '📭',
                'ERROR': '🚨'
            }
            
            emoji = status_emoji.get(analysis.get('status', 'ERROR'), '❓')
            
            # 헤더 (12:30 최종 세션)
            message = f"{emoji} *KRX ETS 12:30 최종 세션*\n"
            message += f"📅 날짜: `{analysis.get('date', 'Unknown')}`\n"
            message += f"🎯 시간: `12:30 KST 최종 마감`\n"
            message += f"⏰ 마지막 업데이트: `{analysis.get('last_update_time', 'Unknown')}`\n"
            message += f"🚀 시스템: `최적화 v3.0 (KRX 시트 추가)`\n\n"
            
            # 최종 세션 확인
            if analysis.get('final_session_confirmed', False):
                message += f"*🎯 최종 마감 확인*\n"
                message += f"• 12:30 세션: `✅ 완료`\n"
                message += f"• 최종 시간: `{analysis.get('final_session_time', 'Unknown')}`\n"
                message += f"• 상태: `{analysis.get('message', 'Unknown')}`\n\n"
            else:
                message += f"*⚠️ 최종 마감 미확인*\n"
                message += f"• 12:30 세션: `❌ 누락`\n"
                message += f"• 상태: `{analysis.get('message', 'Unknown')}`\n\n"
            
            # KRX 시트 요약 (신규)
            krx_summary = analysis.get('krx_summary', {})
            if krx_summary.get('krx_updated', False):
                message += f"*📋 KRX 시트 최종 요약*\n"
                message += f"• 업데이트: `✅ 완료 (A~K열)`\n"
                message += f"• 총 종목: `{krx_summary.get('total_symbols', 0)}개`\n"
                message += f"• 활성 종목: `{krx_summary.get('active_symbols', 0)}개`\n"
                message += f"• 총 거래량: `{krx_summary.get('total_volume', 0):,.0f}톤`\n\n"
            else:
                message += f"*📋 KRX 시트 상태*\n"
                message += f"• 업데이트: `❌ 실패`\n"
                message += f"• 원인: KRX 시트 접근 오류\n\n"
            
            # 일일 세션 커버리지
            session_coverage = analysis.get('session_coverage', {})
            if session_coverage:
                message += f"*📊 일일 세션 커버리지*\n"
                for phase, count in session_coverage.items():
                    message += f"• {phase}: `{count}회`\n"
                message += "\n"
            
            # 최적화 성과
            storage_efficiency = analysis.get('storage_efficiency', 0)
            skipped_duplicates = analysis.get('skipped_duplicates', 0)
            
            if storage_efficiency > 0:
                message += f"*💾 최적화 성과*\n"
                message += f"• 용량 절약: `{storage_efficiency}%`\n"
                message += f"• 중복 제거: `{skipped_duplicates}개`\n"
                message += f"• 실제 업데이트: `{analysis.get('total_records', 0)}개`\n\n"
            
            # 활성 거래 종목 (상위 5개)
            active_symbols = analysis.get('active_trading_symbols', [])
            if active_symbols:
                message += f"*💹 활성 거래 종목 (Top 5)*\n"
                for symbol in active_symbols[:5]:
                    change_emoji = "📈" if symbol['change'] > 0 else "📉" if symbol['change'] < 0 else "➡️"
                    message += f"• `{symbol['symbol']}`: {symbol['price']:,.0f}원 "
                    message += f"({symbol['change']:+.0f}, {symbol['change_rate']:+.1f}%) "
                    message += f"{change_emoji} `{symbol['volume']:,.0f}톤`\n"
                message += "\n"
            else:
                message += f"*💹 거래 현황*\n"
                message += f"• 오늘 활성 거래 없음\n"
                message += f"• 총 거래량: `{analysis.get('total_trading_volume', 0):,.0f}톤`\n\n"
            
            # 일일 통계 요약
            daily_stats = analysis.get('daily_statistics', {})
            if daily_stats:
                message += f"*📈 일일 통계 요약*\n"
                message += f"• 총 세션: `{daily_stats.get('total_sessions', 0)}회`\n"
                message += f"• 데이터 포인트: `{daily_stats.get('data_points', 0)}개`\n"
                message += f"• 종목 수: `{daily_stats.get('unique_symbols_count', 0)}개`\n"
                message += f"• 거래 종목: `{daily_stats.get('active_trading_count', 0)}개`\n\n"
            
            # GitHub Action 상태
            if collection_status:
                action_emoji = "✅" if collection_status == "success" else "❌"
                message += f"*🤖 GitHub Action*\n"
                message += f"• 상태: {action_emoji} `{collection_status}`\n"
                message += f"• 시스템: `최적화 v3.0`\n\n"
            
            # 다음 업데이트 안내
            message += f"*📅 다음 업데이트*\n"
            message += f"• 시간: `내일 09:00 KST`\n"
            message += f"• 주기: `09:00-10:00 (30분), 10:00-11:30 (10분), 11:30-12:30 (30분)`\n"
            message += f"• 알림: `내일 12:30 KST`\n\n"
            
            # 푸터
            message += f"---\n"
            message += f"🕒 리포트 생성: `{datetime.now().strftime('%H:%M:%S')} KST`\n"
            message += f"🚀 시스템: `최적화 v3.0 (KRX 시트 추가)`\n"
            message += f"💾 용량 절약: `{storage_efficiency}%`\n"
            message += f"📋 KRX 시트: `{'✅ 완료' if krx_summary.get('krx_updated', False) else '❌ 실패'}`\n"
            message += f"🔗 [GitHub Repository](https://github.com/your-repo/krx-optimized-collector)"
            
            return message
            
        except Exception as e:
            logger.error(f"최종 세션 메시지 포맷팅 중 오류: {e}")
            return f"❌ *12:30 최종 세션 요약 생성 실패*\n\n오류: {str(e)}\n\n📅 날짜: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    @staticmethod
    def format_final_session_alert(error_message: str) -> str:
        """12:30 최종 세션 오류 알림 메시지"""
        message = f"🚨 *12:30 KRX 최종 세션 오류*\n\n"
        message += f"⏰ 시간: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST`\n"
        message += f"❌ 오류: `{error_message}`\n"
        message += f"🚀 시스템: `최적화 v3.0 (KRX 시트)`\n\n"
        message += f"🔧 확인 필요:\n"
        message += f"• GitHub Actions 로그\n"
        message += f"• KRX 시트 업데이트 상태\n"
        message += f"• 12:30 세션 실행 여부\n"
        message += f"• Google Sheets 접근 권한\n\n"
        message += f"📱 *중요*: 12:30 최종 세션이므로 즉시 확인 필요"
        return message

def main():
    """12:30 최종 세션 전용 메인 실행 함수"""
    try:
        logger.info("=== 12:30 KST 최종 세션 텔레그램 알림 시작 ===")
        
        # 환경변수 확인
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        collection_status = os.getenv('COLLECTION_STATUS', '')
        is_final_session = os.getenv('IS_FINAL_SESSION', 'false').lower() == 'true'
        force_telegram = os.getenv('FORCE_TELEGRAM', 'false').lower() == 'true'
        
        if not telegram_bot_token or not telegram_chat_id:
            logger.error("텔레그램 인증 정보가 없습니다")
            return
            
        if not creds_json or not sheet_id:
            logger.error("Google Sheets 인증 정보가 없습니다")
            return
        
        logger.info(f"수집 상태: {collection_status}")
        logger.info(f"최종 세션: {is_final_session}")
        logger.info(f"강제 전송: {force_telegram}")
        
        # 12:30 최종 세션 확인
        current_time = datetime.now()
        is_twelve_thirty = (current_time.hour == 12 and 25 <= current_time.minute <= 35)
        
        logger.info(f"현재 시간: {current_time.strftime('%H:%M')}")
        logger.info(f"12:30 시간대: {is_twelve_thirty}")
        
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
        
        # 12:30 최종 세션 데이터 분석
        today_data, analysis = analyzer.get_today_final_analysis()
        logger.info(f"최종 세션 분석 완료: {analysis.get('status', 'Unknown')}")
        
        # 알림 조건 확인 (12:30 최종 세션 전용)
        should_alert = (
            is_final_session or  # 환경변수로 최종 세션 확인
            is_twelve_thirty or  # 12:30 시간대 확인
            force_telegram or    # 강제 전송
            analysis.get('status') in ['FAILED', 'ERROR', 'NO_DATA'] or  # 심각한 오류
            collection_status == 'failed'  # GitHub Action 실패
        )
        
        # 메시지 생성 및 전송
        if should_alert:
            if is_final_session or is_twelve_thirty:
                logger.info("🎯 12:30 KST 최종 세션 일일 요약 전송")
            elif force_telegram:
                logger.info("🚀 강제 텔레그램 전송")
            else:
                logger.info("⚠️ 긴급 상황 - 12:30 세션 오류 알림")
            
            message = FinalSessionMessageFormatter.format_final_session_summary(analysis, collection_status)
        else:
            logger.info("⏸️ 12:30 최종 세션이 아님 - 알림 건너뜀")
            print("⏸️ 12:30 최종 세션이 아닙니다.")
            print(f"⏰ 현재 시간: {current_time.strftime('%H:%M')}")
            print(f"📅 다음 알림: 내일 12:30 KST")
            return
        
        # 텔레그램 메시지 전송
        success = telegram.send_message(message)
        
        if success:
            logger.info("✅ 12:30 최종 세션 텔레그램 알림 전송 성공")
            print("✅ 12:30 KST 최종 세션 텔레그램 알림 전송 완료!")
            print(f"📊 분석 결과: {analysis.get('status', 'Unknown')}")
            print(f"🚀 시스템: 최적화 v3.0 (KRX 시트)")
            print(f"📋 KRX 업데이트: {analysis.get('krx_summary', {}).get('krx_updated', False)}")
            print(f"📱 메시지 길이: {len(message)} 문자")
            
            if analysis.get('storage_efficiency', 0) > 0:
                print(f"⚡ 중복 제거: {analysis.get('skipped_duplicates', 0)}개")
                print(f"💾 용량 절약: {analysis.get('storage_efficiency', 0)}%")
                
        else:
            logger.error("❌ 12:30 최종 세션 텔레그램 알림 전송 실패")
            print("❌ 12:30 최종 세션 텔레그램 알림 전송 실패")
            
    except Exception as e:
        logger.error(f"❌ 12:30 최종 세션 알림 시스템 오류: {e}")
        print(f"❌ 오류: {e}")
        
        # 긴급 오류 알림 시도
        try:
            if 'telegram' in locals():
                error_message = FinalSessionMessageFormatter.format_final_session_alert(str(e))
                telegram.send_message(error_message)
        except:
            pass

if __name__ == "__main__":
    main()
