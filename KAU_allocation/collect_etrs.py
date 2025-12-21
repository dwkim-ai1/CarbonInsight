#!/usr/bin/env python3
"""
ETRS (배출권등록부시스템) 데이터 수집 스크립트
GitHub Actions에서 실행됨
"""

import os
import json
import requests
import pandas as pd
from io import BytesIO
from pathlib import Path
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials


# 스크립트 위치 기준 데이터 디렉토리
SCRIPT_DIR = Path(__file__).parent
DATA_DIR = SCRIPT_DIR / "data"


def get_gspread_client():
    """Google Sheets 클라이언트 생성"""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS 환경변수가 설정되지 않았습니다.")
    
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    creds_dict = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    
    return gspread.authorize(credentials)


def download_etrs_excel(menu_id, plan_period, endpoint="infoOpenList10Excel"):
    """ETRS 엑셀 다운로드"""
    base_url = "https://etrs.gir.go.kr/home/infoOpen"
    url = f"{base_url}/{endpoint}.do"
    
    params = {
        "pagerOffset": 0,
        "maxPageItems": 10,
        "maxIndexPages": 10,
        "menuId": menu_id,
        "condition.plPeriDgr": plan_period,
        "condition.sectCd": "",
        "condition.btCd": "",
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    
    response = requests.get(url, params=params, headers=headers, timeout=120)
    response.raise_for_status()
    
    return pd.read_excel(BytesIO(response.content))


def update_sheet(gc, spreadsheet_id, sheet_name, df):
    """시트 업데이트"""
    spreadsheet = gc.open_by_key(spreadsheet_id)
    
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
        worksheet.clear()
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=sheet_name, 
            rows=len(df) + 100, 
            cols=len(df.columns) + 5
        )
    
    # DataFrame을 리스트로 변환 (NaN 처리)
    df_clean = df.fillna("")
    data = [df_clean.columns.tolist()] + df_clean.values.tolist()
    
    worksheet.update(data, value_input_option='USER_ENTERED')
    print(f"✅ '{sheet_name}' 업데이트 완료: {len(df)}행")


def main():
    print("=" * 60)
    print("ETRS 데이터 수집 시작")
    print(f"시간: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # 환경변수
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("SPREADSHEET_ID 환경변수가 설정되지 않았습니다.")
    
    # 데이터 디렉토리 생성
    DATA_DIR.mkdir(exist_ok=True)
    
    # Google Sheets 클라이언트
    gc = get_gspread_client()
    print("✅ Google Sheets 인증 완료")
    
    # 수집할 데이터 정의
    datasets = [
        {
            "name": "사전할당량",
            "menu_id": 24,
            "endpoint": "infoOpenList10Excel",
            "sheet_name": "ETRS_사전할당량",
        },
        {
            "name": "인증배출량",
            "menu_id": 20,
            "endpoint": "infoOpenList06Excel",
            "sheet_name": "ETRS_인증배출량",
        },
        {
            "name": "추가할당량",
            "menu_id": 14,
            "endpoint": "infoOpenList02Excel",
            "sheet_name": "ETRS_추가할당량",
        },
    ]
    
    # 계획기간별 수집
    for plan_period in [1, 2, 3]:  # 1차, 2차, 3차
        print(f"\n--- 계획기간 {plan_period}차 ---")
        
        for dataset in datasets:
            try:
                print(f"\n📥 {dataset['name']} 다운로드 중...")
                
                df = download_etrs_excel(
                    menu_id=dataset["menu_id"],
                    plan_period=plan_period,
                    endpoint=dataset["endpoint"]
                )
                
                # 로컬 저장
                filename = f"etrs_{dataset['name']}_{plan_period}차_{datetime.now().strftime('%Y%m%d')}.xlsx"
                filepath = DATA_DIR / filename
                df.to_excel(filepath, index=False)
                print(f"💾 저장: {filepath} ({len(df)}행)")
                
                # Google Sheets 업데이트
                sheet_name = f"{dataset['sheet_name']}_{plan_period}차"
                update_sheet(gc, spreadsheet_id, sheet_name, df)
                
            except Exception as e:
                print(f"❌ {dataset['name']} 수집 실패: {e}")
                continue
    
    print("\n" + "=" * 60)
    print("ETRS 데이터 수집 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
