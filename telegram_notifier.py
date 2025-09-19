#!/usr/bin/env python3
"""
Playwright 기반 KRX ETS 데이터 수집 결과 텔레그램 알림 시스템
1. Playwright 멀티 추출 방법별 분석
2. DOM, AJAX, OCR 백업 성공률 추적
3. 동적 테이블 수집 성과 모니터링
4. 거래시간별 맞춤 알림 메시지
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
        """Playwright 기반 텔레그램 알림 시스템 초기화"""
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
                'text': '🔍 Playwright 기반 연결 테스트 - 이 메시지가 보이면 설정이 올바릅니다.',
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
                
                # 일반적인 에러 코드별 해결방법 안내
                if error_code == 400:
                    if 'chat not found' in error_desc.lower():
                        logger.error("💡 해결방법: Chat ID가 잘못되었습니다. 올바른 Chat ID를 확인하세요.")
                    elif 'bot was blocked' in error_desc.lower():
                        logger.error("💡 해결방법: 봇이 차단되었습니다. 봇과의 대화를 시작하고 /start를 보내세요.")
                elif error_code == 401:
                    logger.error("💡 해결방법: Bot 토큰이 잘못되었습니다. @BotFather에서 올바른 토큰을 확인하세요.")
                elif error_code == 403:
                    logger.error("💡 해결방법: 봇에게 메시지 전송 권한이 없습니다. 봇과 먼저 대화를 시작하세요.")
                
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
            
            # 디버깅 정보 출력 (보안상 일부만)
            logger.info(f"Playwright 기반 텔레그램 API 호출 시작")
            logger.info(f"URL: {url}")
            logger.info(f"Chat ID: {self.chat_id}")
            logger.info(f"Bot Token 앞부분: {self.bot_token[:15]}...")
            logger.info(f"메시지 길이: {len(message)} 문자")
            
            response = requests.post(url, json=payload, timeout=30)
            
            # 응답 상태 상세 로깅
            logger.info(f"HTTP 상태 코드: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"응답 내용: {response.text}")
                
            response.raise_for_status()
            
            logger.info("텔레그램 메시지 전송 성공")
            return True
            
        except requests.RequestException as e:
            logger.error(f"텔레그램 메시지 전송 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    logger.error(f"텔레그램 API 오류 상세: {error_data}")
                except:
                    logger.error(f"응답 텍스트: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"텔레그램 메시지 전송 중 예상치 못한 오류: {e}")
            return False

class PlaywrightDataAnalyzer:
    def __init__(self, credentials_json: str, sheet_id: str):
        """Playwright 기반 일일 데이터 분석기 초기화"""
        try:
            creds_dict = json.loads(credentials_json)
            self.gc = gspread.service_account_from_dict(creds_dict)
            self.sheet_id = sheet_id
            self.spreadsheet = self.gc.open_by_key(sheet_id)
            self.worksheet = self.spreadsheet.worksheet('ets.KRX')
            logger.info("Google Sheets 연결 성공 (Playwright 데이터 분석)")
        except Exception as e:
            logger.error(f"Google Sheets 연결 실패: {e}")
            raise
    
    def get_today_data(self) -> Tuple[List[Dict], Dict]:
        """오늘 Playwright 수집 데이터 분석 및 요약 정보 반환"""
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            logger.info(f"오늘 날짜 Playwright 데이터 분석 시작: {today}")
            
            # 모든 데이터 가져오기
            all_data = self.worksheet.get_all_records()
            logger.info(f"총 {len(all_data)}개 레코드 조회")
            
            # 오늘 데이터만 필터링
            today_data = [row for row in all_data if row.get('날짜') == today]
            logger.info(f"오늘 Playwright 데이터: {len(today_data)}개 레코드")
            
            # Playwright 기반 데이터 분석
            analysis = self._analyze_playwright_data(today_data, today)
            
            return today_data, analysis
            
        except Exception as e:
            logger.error(f"Playwright 데이터 분석 중 오류: {e}")
            return [], {}
    
    def _analyze_playwright_data(self, today_data: List[Dict], today: str) -> Dict:
        """Playwright 기반 데이터 분석 및 통계 생성"""
        try:
            analysis = {
                'date': today,
                'total_records': len(today_data),
                'successful_collections': 0,
                'failed_collections': 0,
                'data_sources': {},
                'extraction_methods': {},
                'playwright_performance': {},
                'active_trading_symbols': [],
                'collection_times': [],
                'total_trading_volume': 0,
                'total_trading_value': 0,
                'unique_symbols': set(),
                'missing_data_count': 0,
                'last_update_time': '',
                'data_quality_issues': [],
                'browser_modes': {},
                'ocr_usage': 0,
                'ajax_success': 0,
                'dom_success': 0
            }
            
            if not today_data:
                analysis['status'] = 'NO_DATA'
                analysis['message'] = '오늘 Playwright로 수집된 데이터가 없습니다.'
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
                
                # Playwright 데이터 소스 추적
                data_source = record.get('데이터소스', 'unknown')
                analysis['data_sources'][data_source] = analysis['data_sources'].get(data_source, 0) + 1
                
                # Playwright 추출 방법별 통계
                if 'playwright' in data_source:
                    extraction_method = data_source.split('_')[-1] if '_' in data_source else 'unknown'
                    analysis['extraction_methods'][extraction_method] = analysis['extraction_methods'].get(extraction_method, 0) + 1
                    
                    # 방법별 성공 카운트
                    if extraction_method == 'dom':
                        analysis['dom_success'] += 1
                    elif extraction_method == 'ajax':
                        analysis['ajax_success'] += 1
                    elif extraction_method == 'ocr':
                        analysis['ocr_usage'] += 1
                
                # 거래 단계별 통계
                trading_phase = record.get('거래단계', 'unknown')
                if trading_phase != 'unknown':
                    analysis['browser_modes'][trading_phase] = analysis['browser_modes'].get(trading_phase, 0) + 1
                
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
                            'extraction_method': data_source
                        }
                        analysis['active_trading_symbols'].append(symbol_info)
                except (ValueError, TypeError) as e:
                    analysis['missing_data_count'] += 1
                    logger.debug(f"데이터 파싱 오류: {e}")
            
            # Playwright 성공/실패 분석
            playwright_sources = ['playwright_dom', 'playwright_ajax', 'playwright_ocr', 'playwright_html']
            legacy_sources = ['realistic_sample', 'krx_main_page']  # 레거시
            
            playwright_count = sum(analysis['data_sources'].get(src, 0) for src in playwright_sources)
            legacy_count = sum(analysis['data_sources'].get(src, 0) for src in legacy_sources)
            
            if playwright_count > 0:
                analysis['successful_collections'] = playwright_count
                analysis['failed_collections'] = legacy_count
                
                if legacy_count == 0:
                    analysis['status'] = 'SUCCESS'
                    analysis['message'] = f'Playwright 기반 데이터 수집 완전 성공 ({playwright_count}개)'
                else:
                    analysis['status'] = 'PARTIAL_SUCCESS'
                    analysis['message'] = f'Playwright 일부 성공 ({playwright_count}개), 레거시 {legacy_count}개'
            else:
                analysis['failed_collections'] = analysis['total_records']
                analysis['status'] = 'FAILED'
                analysis['message'] = 'Playwright 데이터 수집 실패 (레거시/샘플 데이터만 존재)'
            
            # Playwright 성능 분석
            total_playwright = analysis['dom_success'] + analysis['ajax_success'] + analysis['ocr_usage']
            if total_playwright > 0:
                analysis['playwright_performance'] = {
                    'dom_ratio': round((analysis['dom_success'] / total_playwright) * 100, 1),
                    'ajax_ratio': round((analysis['ajax_success'] / total_playwright) * 100, 1),
                    'ocr_ratio': round((analysis['ocr_usage'] / total_playwright) * 100, 1),
                    'total_extractions': total_playwright
                }
            
            # 데이터 품질 검사
            analysis['unique_symbols'] = list(analysis['unique_symbols'])
            expected_symbols = ['KAU25', 'KCU25', 'KOC21-26', 'KOC22-27', 'KOC23-28', 'i-KCU25']
            missing_symbols = set(expected_symbols) - set(analysis['unique_symbols'])
            
            if missing_symbols:
                analysis['data_quality_issues'].append(f"누락된 종목: {', '.join(missing_symbols)}")
            
            if analysis['missing_data_count'] > 0:
                analysis['data_quality_issues'].append(f"파싱 오류 데이터: {analysis['missing_data_count']}건")
            
            if analysis['ocr_usage'] > 0:
                analysis['data_quality_issues'].append(f"OCR 백업 사용: {analysis['ocr_usage']}건 (시각적 확인 권장)")
            
            # 활성 거래 종목 정렬 (거래량 기준)
            analysis['active_trading_symbols'].sort(key=lambda x: x['volume'], reverse=True)
            
            logger.info(f"Playwright 데이터 분석 완료: {analysis['status']}")
            logger.info(f"추출 방법별 성과: DOM {analysis['dom_success']}개, AJAX {analysis['ajax_success']}개, OCR {analysis['ocr_usage']}개")
            
            return analysis
            
        except Exception as e:
            logger.error(f"Playwright 데이터 분석 중 오류: {e}")
            return {
                'date': today,
                'status': 'ERROR',
                'message': f'Playwright 분석 중 오류 발생: {str(e)}',
                'total_records': len(today_data)
            }

    def get_recent_trends(self, days: int = 7) -> Dict:
        """최근 N일간의 Playwright 데이터 트렌드 분석"""
        try:
            all_data = self.worksheet.get_all_records()
            
            # 최근 N일 날짜 생성
            recent_dates = []
            for i in range(days):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                recent_dates.append(date)
            
            trend_analysis = {
                'period': f'최근 {days}일 (Playwright)',
                'dates': recent_dates,
                'daily_collection_count': {},
                'daily_playwright_ratio': {},
                'daily_extraction_methods': {},
                'total_collections': 0,
                'total_playwright_success': 0,
                'playwright_method_trends': {
                    'dom': 0, 'ajax': 0, 'ocr': 0, 'html': 0
                }
            }
            
            for date in recent_dates:
                daily_data = [row for row in all_data if row.get('날짜') == date]
                
                if daily_data:
                    # Playwright vs 레거시 분석
                    playwright_count = len([row for row in daily_data if 'playwright' in row.get('데이터소스', '')])
                    total_count = len(daily_data)
                    playwright_ratio = (playwright_count / total_count * 100) if total_count > 0 else 0
                    
                    trend_analysis['daily_collection_count'][date] = total_count
                    trend_analysis['daily_playwright_ratio'][date] = round(playwright_ratio, 1)
                    trend_analysis['total_collections'] += total_count
                    trend_analysis['total_playwright_success'] += playwright_count
                    
                    # 추출 방법별 트렌드
                    methods = {}
                    for row in daily_data:
                        source = row.get('데이터소스', 'unknown')
                        if 'playwright' in source:
                            method = source.split('_')[-1] if '_' in source else 'other'
                            methods[method] = methods.get(method, 0) + 1
                            trend_analysis['playwright_method_trends'][method] = trend_analysis['playwright_method_trends'].get(method, 0) + 1
                    
                    trend_analysis['daily_extraction_methods'][date] = methods
                else:
                    trend_analysis['daily_collection_count'][date] = 0
                    trend_analysis['daily_playwright_ratio'][date] = 0
                    trend_analysis['daily_extraction_methods'][date] = {}
            
            return trend_analysis
            
        except Exception as e:
            logger.error(f"Playwright 트렌드 분석 중 오류: {e}")
            return {}

class PlaywrightMessageFormatter:
    @staticmethod
    def format_daily_summary(analysis: Dict, trends: Dict, collection_status: str) -> str:
        """Playwright 기반 일일 요약 메시지 포맷팅"""
        try:
            status_emoji = {
                'SUCCESS': '✅',
                'PARTIAL_SUCCESS': '⚠️',
                'FAILED': '❌',
                'NO_DATA': '📭',
                'ERROR': '🚨'
            }
            
            emoji = status_emoji.get(analysis.get('status', 'ERROR'), '❓')
            
            # 헤더 (Playwright 기반)
            message = f"{emoji} *KRX ETS Playwright 수집 결과*\n"
            message += f"📅 날짜: `{analysis.get('date', 'Unknown')}`\n"
            message += f"⏰ 마지막 업데이트: `{analysis.get('last_update_time', 'Unknown')}`\n"
            message += f"🌐 수집 방식: `Playwright 멀티 추출`\n\n"
            
            # 수집 현황
            message += f"*📊 수집 현황*\n"
            message += f"• 총 레코드: `{analysis.get('total_records', 0)}개`\n"
            message += f"• Playwright 성공: `{analysis.get('successful_collections', 0)}개`\n"
            message += f"• 레거시/실패: `{analysis.get('failed_collections', 0)}개`\n"
            message += f"• 상태: `{analysis.get('message', 'Unknown')}`\n\n"
            
            # Playwright 추출 방법별 분석
            if analysis.get('data_sources'):
                message += f"*🔍 데이터 추출 방법*\n"
                for source, count in analysis['data_sources'].items():
                    source_name = {
                        'playwright_dom': '🌐 DOM 추출',
                        'playwright_ajax': '📡 AJAX 모니터링',
                        'playwright_ocr': '📷 OCR 백업',
                        'playwright_html': '📄 HTML 파싱',
                        'krx_real_data': '🎯 KRX 실데이터',  # 레거시
                        'realistic_sample': '🔄 샘플데이터',  # 레거시
                        'krx_main_page': '🌐 메인페이지'  # 레거시
                    }.get(source, f'❓ {source}')
                    message += f"• {source_name}: `{count}개`\n"
                message += "\n"
            
            # Playwright 성능 분석
            perf = analysis.get('playwright_performance', {})
            if perf:
                message += f"*⚡ Playwright 성능 분석*\n"
                message += f"• DOM 추출: `{perf.get('dom_ratio', 0)}%`\n"
                message += f"• AJAX 모니터링: `{perf.get('ajax_ratio', 0)}%`\n"
                message += f"• OCR 백업: `{perf.get('ocr_ratio', 0)}%`\n"
                message += f"• 총 추출: `{perf.get('total_extractions', 0)}건`\n\n"
            
            # 거래 현황
            if analysis.get('active_trading_symbols'):
                message += f"*💹 활성 거래 종목*\n"
                for symbol in analysis['active_trading_symbols'][:5]:  # 상위 5개만
                    change_emoji = "📈" if symbol['change'] > 0 else "📉" if symbol['change'] < 0 else "➡️"
                    extraction_emoji = {
                        'playwright_dom': '🌐',
                        'playwright_ajax': '📡', 
                        'playwright_ocr': '📷',
                        'playwright_html': '📄'
                    }.get(symbol.get('extraction_method', ''), '❓')
                    
                    message += f"• `{symbol['symbol']}`: {symbol['price']:,.0f}원 "
                    message += f"({symbol['change']:+.0f}) {change_emoji} `{symbol['volume']:,.0f}톤` {extraction_emoji}\n"
                message += "\n"
            else:
                message += f"*💹 거래 현황*\n"
                message += f"• 오늘 활성 거래 없음\n"
                message += f"• 총 거래량: `{analysis.get('total_trading_volume', 0):,.0f}톤`\n\n"
            
            # 브라우저 모드별 통계
            browser_modes = analysis.get('browser_modes', {})
            if browser_modes:
                message += f"*🌐 브라우저 모드별 수집*\n"
                for mode, count in browser_modes.items():
                    mode_name = {
                        'opening_price_decision': '🔥 시가체결 (Visual)',
                        'closing_price_decision': '🏁 종가체결 (OCR)',
                        'real_time_trading': '⚡ 실시간거래 (Fast)',
                        'order_acceptance': '📝 주문접수',
                        'manual': '🔧 수동실행'
                    }.get(mode, f'❓ {mode}')
                    message += f"• {mode_name}: `{count}건`\n"
                message += "\n"
            
            # 데이터 품질 이슈
            if analysis.get('data_quality_issues'):
                message += f"*⚠️ 데이터 품질 이슈*\n"
                for issue in analysis['data_quality_issues']:
                    message += f"• {issue}\n"
                message += "\n"
            
            # 최근 트렌드 (Playwright 기준)
            if trends:
                total_collections = trends.get('total_collections', 0)
                total_playwright = trends.get('total_playwright_success', 0)
                avg_playwright_ratio = ((total_playwright / total_collections * 100) if total_collections > 0 else 0)
                
                message += f"*📈 최근 7일 Playwright 트렌드*\n"
                message += f"• 총 수집: `{total_collections}건`\n"
                message += f"• Playwright 성공률: `{avg_playwright_ratio:.1f}%`\n"
                
                # 추출 방법별 트렌드
                method_trends = trends.get('playwright_method_trends', {})
                if any(method_trends.values()):
                    message += f"• 방법별: DOM `{method_trends.get('dom', 0)}` | "
                    message += f"AJAX `{method_trends.get('ajax', 0)}` | "
                    message += f"OCR `{method_trends.get('ocr', 0)}`\n"
                
                message += "\n"
            
            # GitHub Action 상태 (Playwright 기반)
            if collection_status:
                action_emoji = "✅" if collection_status == "success" else "❌"
                message += f"*🤖 GitHub Action (Playwright)*\n"
                message += f"• 상태: {action_emoji} `{collection_status}`\n"
                
                # Playwright 추출 방법 정보 추가
                extraction_methods = os.getenv('EXTRACTION_METHODS', 'unknown')
                if extraction_methods != 'unknown':
                    method_names = {
                        'playwright_multi': '🌐 멀티 추출 (DOM+AJAX+OCR)',
                        'playwright_dom': '🌐 DOM 추출',
                        'playwright_ajax': '📡 AJAX 모니터링',
                        'playwright_ocr': '📷 OCR 백업',
                        'failed': '❌ 모든 방법 실패'
                    }.get(extraction_methods, extraction_methods)
                    message += f"• 추출 방법: `{method_names}`\n"
                
                message += "\n"
            
            # 푸터
            message += f"---\n"
            message += f"🕒 리포트 생성: `{datetime.now().strftime('%H:%M:%S')} KST`\n"
            message += f"🌐 수집 방식: `Playwright 기반 멀티 추출`\n"
            message += f"🔗 [GitHub Repository](https://github.com/your-repo/krx-playwright-collector)"
            
            return message
            
        except Exception as e:
            logger.error(f"Playwright 메시지 포맷팅 중 오류: {e}")
            return f"❌ *Playwright 일일 요약 생성 실패*\n\n오류: {str(e)}\n\n📅 날짜: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    @staticmethod
    def format_error_alert(error_message: str) -> str:
        """Playwright 오류 알림 메시지 포맷팅"""
        message = f"🚨 *Playwright KRX 데이터 수집 오류*\n\n"
        message += f"⏰ 시간: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST`\n"
        message += f"❌ 오류: `{error_message}`\n"
        message += f"🌐 수집 방식: `Playwright 멀티 추출`\n\n"
        message += f"🔧 확인 필요:\n"
        message += f"• GitHub Actions 로그\n"
        message += f"• Playwright 브라우저 상태\n"
        message += f"• OCR 백업 스크린샷\n"
        message += f"• 네트워크 연결 상태"
        return message

def main():
    """Playwright 기반 메인 실행 함수"""
    try:
        logger.info("=== Playwright 기반 텔레그램 일일 요약 및 알림 시작 ===")
        
        # 환경변수 확인
        telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        telegram_chat_id = os.getenv('TELEGRAM_CHAT_ID')
        creds_json = os.getenv('GOOGLE_SHEETS_CREDS')
        sheet_id = os.getenv('KAU_SHEET_ID')
        collection_status = os.getenv('COLLECTION_STATUS', '')
        collection_message = os.getenv('COLLECTION_MESSAGE', '')
        extraction_methods = os.getenv('EXTRACTION_METHODS', '')
        force_telegram = os.getenv('FORCE_TELEGRAM', 'false').lower() == 'true'
        
        if not telegram_bot_token or not telegram_chat_id:
            logger.error("텔레그램 인증 정보가 없습니다")
            return
            
        if not creds_json or not sheet_id:
            logger.error("Google Sheets 인증 정보가 없습니다")
            return
        
        logger.info(f"Playwright 수집 상태: {collection_status}")
        logger.info(f"추출 방법: {extraction_methods}")
        logger.info(f"수집 메시지: {collection_message}")
        logger.info(f"강제 텔레그램 전송: {force_telegram}")
        
        # 텔레그램 알림 시스템 초기화
        telegram = TelegramNotifier(telegram_bot_token, telegram_chat_id)
        
        # 텔레그램 연결 테스트 실행
        logger.info("=== 텔레그램 연결 테스트 시작 ===")
        bot_ok = telegram.test_bot_connection()
        chat_ok = telegram.test_chat_access()
        
        if not bot_ok:
            logger.error("❌ Bot 토큰에 문제가 있습니다")
            print("❌ Bot 토큰 오류 - GitHub Secrets CARBON_TOKEN 확인 필요")
            return
            
        if not chat_ok:
            logger.error("❌ Chat ID에 문제가 있습니다")  
            print("❌ Chat ID 오류 - GitHub Secrets ESG_TESTER 확인 필요")
            print("💡 해결방법:")
            print("   1. 텔레그램에서 봇과 대화 시작")
            print("   2. /start 명령어 전송") 
            print("   3. Chat ID 재확인")
            return
        
        # Playwright 데이터 분석기 초기화
        analyzer = PlaywrightDataAnalyzer(creds_json, sheet_id)
        
        # 오늘 데이터 분석
        today_data, analysis = analyzer.get_today_data()
        logger.info(f"Playwright 데이터 분석 완료: {analysis.get('status', 'Unknown')}")
        
        # 최근 트렌드 분석
        trends = analyzer.get_recent_trends(7)
        logger.info(f"Playwright 트렌드 분석 완료: {len(trends)} 항목")
        
        # 알림 조건 확인 (Playwright 기준)
        should_alert = (
            analysis.get('status') in ['FAILED', 'ERROR', 'NO_DATA'] or  # 심각한 오류
            analysis.get('failed_collections', 0) > 0 or  # 부분 실패 (레거시 데이터 혼재)
            collection_status == 'failed' or  # GitHub Action 실패
            analysis.get('ocr_usage', 0) > 0 or  # OCR 백업 사용 (시각적 확인 필요)
            force_telegram  # 강제 전송 플래그
        )
        
        # 메시지 생성
        if should_alert:
            if force_telegram:
                logger.info("🚀 강제 텔레그램 전송 요청 - Playwright 요약 전송")
            else:
                logger.info("⚠️ 알림 조건 충족 - Playwright 상세 요약 전송")
            message = PlaywrightMessageFormatter.format_daily_summary(analysis, trends, collection_status)
        else:
            # 성공시에도 간단한 요약 전송 (15시에만)
            current_hour = datetime.now().hour
            if current_hour == 15:  # 15:00 KST 정기 요약
                logger.info("📊 정기 Playwright 일일 요약 전송")
                message = PlaywrightMessageFormatter.format_daily_summary(analysis, trends, collection_status)
            else:
                logger.info("✅ Playwright 정상 상태 - 알림 건너뜀")
                return
        
        # 텔레그램 메시지 전송
        success = telegram.send_message(message)
        
        if success:
            logger.info("✅ Playwright 기반 텔레그램 알림 전송 성공")
            print("✅ Playwright 텔레그램 일일 요약 전송 완료!")
            print(f"📊 분석 결과: {analysis.get('status', 'Unknown')}")
            print(f"🌐 추출 방법: {', '.join(analysis.get('extraction_methods', {}).keys())}")
            print(f"📱 메시지 길이: {len(message)} 문자")
            
            # Playwright 성능 정보
            perf = analysis.get('playwright_performance', {})
            if perf:
                print(f"⚡ DOM: {perf.get('dom_ratio', 0)}% | AJAX: {perf.get('ajax_ratio', 0)}% | OCR: {perf.get('ocr_ratio', 0)}%")
        else:
            logger.error("❌ Playwright 텔레그램 알림 전송 실패")
            print("❌ 텔레그램 알림 전송 실패")
            
    except Exception as e:
        logger.error(f"❌ Playwright 텔레그램 알림 시스템 오류: {e}")
        print(f"❌ 오류: {e}")
        
        # 긴급 오류 알림 시도
        try:
            if 'telegram' in locals():
                error_message = PlaywrightMessageFormatter.format_error_alert(str(e))
                telegram.send_message(error_message)
        except:
            pass  # 긴급 알림도 실패하면 조용히 넘어감

if __name__ == "__main__":
    main()
