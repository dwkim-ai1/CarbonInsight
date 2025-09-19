#!/usr/bin/env python3
"""
KRX ETS 데이터 수집 결과 텔레그램 알림 시스템
일일 수집 결과를 분석하고 실패 사례가 있을 경우 텔레그램으로 알림을 전송
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
        """텔레그램 알림 시스템 초기화"""
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.telegram_api_url = f"https://api.telegram.org/bot{bot_token}"
        
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
            
            response = requests.post(url, json=payload, timeout=30)
            response.raise_for_status()
            
            logger.info("텔레그램 메시지 전송 성공")
            return True
            
        except requests.RequestException as e:
            logger.error(f"텔레그램 메시지 전송 실패: {e}")
            return False
        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 중 예상치 못한 오류: {e}")
            return False

class DailyAnalyzer:
    def __init__(self, credentials_json: str, sheet_id: str):
        """일일 데이터 분석기 초기화"""
        try:
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            self.worksheet = self.spreadsheet.worksheet('ets.KRX')
            logger.info("Google Sheets 연결 성공")
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def get_today_data(self) -> Tuple[List[Dict], Dict]:
        """오늘 데이터 분석 및 요약 정보 반환"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"오늘 날짜 데이터 분석 시작: {today}")
            
            # 모든 데이터 가져오기
            all_data = self.worksheet.get_all_records()
            logger.info(f"총 {len(all_data)}개 레코드 조회")
            
            # 오늘 데이터만 필터링
            today_data = [row for row in all_data if row.get('날짜') == today]
            logger.info(f"오늘 데이터: {len(today_data)}개 레코드")
            
            # 데이터 분석
            analysis = self._analyze_data(today_data, today)
            
            return today_data, analysis
            
        except Exception as e:
            logger.error(f"데이터 분석 중 오류: {e}")
            return [], {}
    
    def _analyze_data(self, today_data: List[Dict], today: str) -> Dict:
        """데이터 분석 및 통계 생성"""
        try:
            analysis = {
                'date': today,
                'total_records': len(today_data),
                'successful_collections': 0,
                'failed_collections': 0,
                'data_sources': {},
                'active_trading_symbols': [],
                'collection_times': [],
                'total_trading_volume': 0,
                'total_trading_value': 0,
                'unique_symbols': set(),
                'missing_data_count': 0,
                'last_update_time': '',
                'data_quality_issues': []
            }
            
            if not today_data:
                analysis['status'] = 'NO_DATA'
                analysis['message'] = '오늘 수집된 데이터가 없습니다.'
                return analysis
            
            for record in today_data:
                # 기본 통계
                analysis['unique_symbols'].add(record.get('종목명', ''))
                
                # 수집 시간 추적
                collection_time = record.get('수집시간', '')
                if collection_time and collection_time not in analysis['collection_times']:
                    analysis['collection_times'].append(collection_time)
                    if collection_time > analysis['last_update_time']:
                        analysis['last_update_time'] = collection_time
                
                # 데이터 소스 추적
                data_source = record.get('데이터소스', 'unknown')
                analysis['data_sources'][data_source] = analysis['data_sources'].get(data_source, 0) + 1
                
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
                            'change': float(str(record.get('대비', 0)).replace(',', ''))
                        }
                        analysis['active_trading_symbols'].append(symbol_info)
                except (ValueError, TypeError) as e:
                    analysis['missing_data_count'] += 1
                    logger.debug(f"데이터 파싱 오류: {e}")
            
            # 성공/실패 분석
            if 'realistic_sample' in analysis['data_sources']:
                sample_count = analysis['data_sources']['realistic_sample']
                real_data_count = analysis['total_records'] - sample_count
                
                if real_data_count > 0:
                    analysis['successful_collections'] = real_data_count
                    analysis['failed_collections'] = sample_count
                    analysis['status'] = 'PARTIAL_SUCCESS'
                    analysis['message'] = f'일부 실데이터 수집 성공 ({real_data_count}/{analysis["total_records"]})'
                else:
                    analysis['failed_collections'] = analysis['total_records']
                    analysis['status'] = 'FAILED'
                    analysis['message'] = '모든 데이터가 샘플 데이터입니다.'
            else:
                analysis['successful_collections'] = analysis['total_records']
                analysis['status'] = 'SUCCESS'
                analysis['message'] = '모든 데이터 수집 성공'
            
            # 데이터 품질 검사
            analysis['unique_symbols'] = list(analysis['unique_symbols'])
            expected_symbols = ['KAU25', 'KCU25', 'KOC21-26', 'KOC22-27', 'KOC23-28', 'i-KCU25']
            missing_symbols = set(expected_symbols) - set(analysis['unique_symbols'])
            
            if missing_symbols:
                analysis['data_quality_issues'].append(f"누락된 종목: {', '.join(missing_symbols)}")
            
            if analysis['missing_data_count'] > 0:
                analysis['data_quality_issues'].append(f"파싱 오류 데이터: {analysis['missing_data_count']}건")
            
            # 활성 거래 종목 정렬 (거래량 기준)
            analysis['active_trading_symbols'].sort(key=lambda x: x['volume'], reverse=True)
            
            logger.info(f"데이터 분석 완료: {analysis['status']}")
            return analysis
            
        except Exception as e:
            logger.error(f"데이터 분석 중 오류: {e}")
            return {
                'date': today,
                'status': 'ERROR',
                'message': f'분석 중 오류 발생: {str(e)}',
                'total_records': len(today_data)
            }

    def get_recent_trends(self, days: int = 7) -> Dict:
        """최근 N일간의 데이터 트렌드 분석"""
        try:
            all_data = self.worksheet.get_all_records()
            
            # 최근 N일 날짜 생성
            recent_dates = []
            for i in range(days):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                recent_dates.append(date)
            
            trend_analysis = {
                'period': f'최근 {days}일',
                'dates': recent_dates,
                'daily_collection_count': {},
                'daily_success_rate': {},
                'total_collections': 0,
                'total_failures': 0
            }
            
            for date in recent_dates:
                daily_data = [row for row in all_data if row.get('날짜') == date]
                
                if daily_data:
                    sample_data_count = sum(1 for row in daily_data if row.get('데이터소스') == 'realistic_sample')
                    real_data_count = len(daily_data) - sample_data_count
                    success_rate = (real_data_count / len(daily_data)) * 100 if daily_data else 0
                    
                    trend_analysis['daily_collection_count'][date] = len(daily_data)
                    trend_analysis['daily_success_rate'][date] = round(success_rate, 1)
                    trend_analysis['total_collections'] += len(daily_data)
                    trend_analysis['total_failures'] += sample_data_count
                else:
                    trend_analysis['daily_collection_count'][date] = 0
                    trend_analysis['daily_success_rate'][date] = 0
            
            return trend_analysis
            
        except Exception as e:
            logger.error(f"트렌드 분석 중 오류: {e}")
            return {}

class MessageFormatter:
    @staticmethod
    def format_daily_summary(analysis: Dict, trends: Dict, collection_status: str) -> str:
        """일일 요약 메시지 포맷팅"""
        try:
            status_emoji = {
                'SUCCESS': '✅',
                'PARTIAL_SUCCESS': '⚠️',
                'FAILED': '❌',
                'NO_DATA': '📭',
                'ERROR': '🚨'
            }
            
            emoji = status_emoji.get(analysis.get('status', 'ERROR'), '❓')
            
            # 헤더
            message = f"{emoji} *KRX ETS 일일 수집 결과*\n"
            message += f"📅 날짜: `{analysis.get('date', 'Unknown')}`\n"
            message += f"⏰ 마지막 업데이트: `{analysis.get('last_update_time', 'Unknown')}`\n\n"
            
            # 수집 상태
            message += f"*📊 수집 현황*\n"
            message += f"• 총 레코드: `{analysis.get('total_records', 0)}개`\n"
            message += f"• 성공: `{analysis.get('successful_collections', 0)}개`\n"
            message += f"• 실패: `{analysis.get('failed_collections', 0)}개`\n"
            message += f"• 상태: `{analysis.get('message', 'Unknown')}`\n\n"
            
            # 데이터 소스별 분석
            if analysis.get('data_sources'):
                message += f"*🔍 데이터 소스*\n"
                for source, count in analysis['data_sources'].items():
                    source_name = {
                        'krx_main_page': '🌐 KRX 메인페이지',
                        'krx_json': '📡 KRX API',
                        'krx_html': '📄 KRX HTML',
                        'realistic_sample': '🔄 샘플데이터'
                    }.get(source, f'❓ {source}')
                    message += f"• {source_name}: `{count}개`\n"
                message += "\n"
            
            # 거래 현황
            if analysis.get('active_trading_symbols'):
                message += f"*💹 활성 거래 종목*\n"
                for symbol in analysis['active_trading_symbols'][:5]:  # 상위 5개만
                    change_emoji = "📈" if symbol['change'] > 0 else "📉" if symbol['change'] < 0 else "➡️"
                    message += f"• `{symbol['symbol']}`: {symbol['price']:,.0f}원 "
                    message += f"({symbol['change']:+.0f}) {change_emoji} `{symbol['volume']:,.0f}톤`\n"
                message += "\n"
            else:
                message += f"*💹 거래 현황*\n"
                message += f"• 오늘 활성 거래 없음\n"
                message += f"• 총 거래량: `{analysis.get('total_trading_volume', 0):,.0f}톤`\n\n"
            
            # 데이터 품질 이슈
            if analysis.get('data_quality_issues'):
                message += f"*⚠️ 데이터 품질 이슈*\n"
                for issue in analysis['data_quality_issues']:
                    message += f"• {issue}\n"
                message += "\n"
            
            # 최근 트렌드 (요약)
            if trends:
                total_collections = trends.get('total_collections', 0)
                total_failures = trends.get('total_failures', 0)
                avg_success_rate = ((total_collections - total_failures) / total_collections * 100) if total_collections > 0 else 0
                
                message += f"*📈 최근 7일 트렌드*\n"
                message += f"• 총 수집: `{total_collections}건`\n"
                message += f"• 평균 성공률: `{avg_success_rate:.1f}%`\n\n"
            
            # GitHub Action 상태
            if collection_status:
                action_emoji = "✅" if collection_status == "success" else "❌"
                message += f"*🤖 GitHub Action*\n"
                message += f"• 상태: {action_emoji} `{collection_status}`\n\n"
            
            # 푸터
            message += f"---\n"
            message += f"🕒 리포트 생성 시간: `{datetime.now().strftime('%H:%M:%S')} KST`\n"
            message += f"🔗 [GitHub Repository](https://github.com/your-repo/krx-data-collector)"
            
            return message
            
        except Exception as e:
            logger.error(f"메시지 포맷팅 중 오류: {e}")
            return f"❌ *일일 요약 생성 실패*\n\n오류: {str(e)}\n\n📅 날짜: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    @staticmethod
    def format_error_alert(error_message: str) -> str:
        """오류 알림 메시지 포맷팅"""
        message = f"🚨 *KRX 데이터 수집 오류 발생*\n\n"
        message += f"⏰ 시간: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST`\n"
        message += f"❌ 오류: `{error_message}`\n\n"
        message += f"🔧 확인 필요: GitHub Actions 로그를 확인하세요."
        return message

def main():
    """메인 실행 함수"""
    try:
        logger.info("=== 텔레그램 일일 요약 및 알림 시작 ===")
        
        # 환경변수 확인
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        collection_status = os.getenv('COLLECTION_STATUS', '')
        collection_message = os.getenv('COLLECTION_MESSAGE', '')
        force_telegram = os.getenv('FORCE_TELEGRAM', 'false').lower() == 'true'
        
        if not telegram_bot_token or not telegram_chat_id:
            logger.error("텔레그램 인증 정보가 없습니다")
            return
            
        if not creds_json or not sheet_id:
            logger.error("Google Sheets 인증 정보가 없습니다")
            return
        
        logger.info(f"수집 상태: {collection_status}")
        logger.info(f"수집 메시지: {collection_message}")
        logger.info(f"강제 텔레그램 전송: {force_telegram}")
        
        # 텔레그램 알림 시스템 초기화
        telegram = TelegramNotifier(telegram_bot_token, telegram_chat_id)
        
        # 데이터 분석기 초기화
        analyzer = DailyAnalyzer(creds_json, sheet_id)
        
        # 오늘 데이터 분석
        today_data, analysis = analyzer.get_today_data()
        logger.info(f"데이터 분석 완료: {analysis.get('status', 'Unknown')}")
        
        # 최근 트렌드 분석
        trends = analyzer.get_recent_trends(7)
        logger.info(f"트렌드 분석 완료: {len(trends)} 항목")
        
        # 알림 조건 확인
        should_alert = (
            analysis.get('status') in ['FAILED', 'ERROR', 'NO_DATA'] or  # 심각한 오류
            analysis.get('failed_collections', 0) > 0 or  # 부분 실패
            collection_status == 'failed' or  # GitHub Action 실패
            force_telegram  # 강제 전송 플래그
        )
        
        # 메시지 생성
        if should_alert:
            if force_telegram:
                logger.info("🚀 강제 텔레그램 전송 요청 - 요약 전송")
            else:
                logger.info("⚠️ 알림 조건 충족 - 상세 요약 전송")
            message = MessageFormatter.format_daily_summary(analysis, trends, collection_status)
        else:
            # 성공시에도 간단한 요약 전송 (15시에만)
            current_hour = datetime.now().hour
            if current_hour == 15:  # 15:00 KST 정기 요약
                logger.info("📊 정기 일일 요약 전송")
                message = MessageFormatter.format_daily_summary(analysis, trends, collection_status)
            else:
                logger.info("✅ 정상 상태 - 알림 건너뜀")
                return
        
        # 텔레그램 메시지 전송
        success = telegram.send_message(message)
        
        if success:
            logger.info("✅ 텔레그램 알림 전송 성공")
            print("✅ 텔레그램 일일 요약 전송 완료!")
            print(f"📊 분석 결과: {analysis.get('status', 'Unknown')}")
            print(f"📱 메시지 길이: {len(message)} 문자")
        else:
            logger.error("❌ 텔레그램 알림 전송 실패")
            print("❌ 텔레그램 알림 전송 실패")
            
    except Exception as e:
        logger.error(f"❌ 텔레그램 알림 시스템 오류: {e}")
        print(f"❌ 오류: {e}")
        
        # 긴급 오류 알림 시도
        try:
            if 'telegram' in locals():
                error_message = MessageFormatter.format_error_alert(str(e))
                telegram.send_message(error_message)
        except:
            pass  # 긴급 알림도 실패하면 조용히 넘어감

if __name__ == "__main__":
    main()
