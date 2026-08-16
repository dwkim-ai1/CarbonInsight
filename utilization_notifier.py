"""Smart Telegram notification for utilization workflow summaries."""
import os, sys
from datetime import datetime, timezone, timedelta
import requests

def main():
    status = os.getenv("COLLECT_STATUS", "unknown")
    new_rows, failed = int(os.getenv("NEW_ROWS", "0")), int(os.getenv("FAILED", "0"))
    total = max(1, int(os.getenv("TOTAL", "200")))
    forced = os.getenv("SEND_TELEGRAM", "false").lower() == "true"
    if not (forced or status == "failure" or failed / total > .1 or new_rows): return 0
    icon = "✅" if status == "success" else "🚨"
    text = (f"{icon} DART 가동률 수집 {'완료' if status == 'success' else '실패'}\n━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {datetime.now(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M} KST\n📊 대상: {total}개 기업\n"
            f"🆕 신규 데이터: {new_rows}행\n❌ 실패: {failed}개\n🤖 LLM 호출: {os.getenv('LLM_CALLS','0')}회\n🔗 로그: {os.getenv('GITHUB_RUN_URL','')}")
    token = os.environ["CARBON_TOKEN"].strip()
    bot_path = token if token.startswith("bot") else f"bot{token}"
    response = requests.post(f"https://api.telegram.org/{bot_path}/sendMessage",
                             json={"chat_id": os.environ["ESG_TESTER"], "text": text}, timeout=15)
    response.raise_for_status(); return 0

if __name__ == "__main__": sys.exit(main())
