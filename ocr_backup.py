#!/usr/bin/env python3
"""
KRX ETS OCR 백업 데이터 수집기 v3.2 (pytesseract 경량화)
Playwright 스크린샷 + pytesseract를 통한 백업 데이터 수집

변경 사항 (v3.1 → v3.2):
- easyocr (2GB+) → pytesseract (~50KB) 교체
- PyTorch/CUDA 의존성 완전 제거
- 시스템 tesseract-ocr 엔진 사용
- 이미지 전처리 강화 (Pillow 기반)
- 실제 작동하는 OCR 백업 구현
"""

import logging
import re
import io
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

# pytesseract + Pillow (경량 OCR)
try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter
    PYTESSERACT_AVAILABLE = True
except ImportError as e:
    logging.warning(f"pytesseract/Pillow 패키지 누락: {e}")
    PYTESSERACT_AVAILABLE = False

logger = logging.getLogger(__name__)


class PytesseractOCRCollector:
    """pytesseract 기반 경량 OCR 데이터 수집기 (v3.2)"""
    
    def __init__(self):
        """OCR 수집기 초기화"""
        self.base_url = "https://ets.krx.co.kr"
        self.screenshots_dir = Path("screenshots")
        self.screenshots_dir.mkdir(exist_ok=True)
        
        # Tesseract 엔진 확인
        self.tesseract_available = self._check_tesseract()
        
    def _check_tesseract(self) -> bool:
        """Tesseract OCR 엔진 설치 확인"""
        if not PYTESSERACT_AVAILABLE:
            logger.warning("⚠️ pytesseract 패키지가 설치되지 않음")
            return False
            
        try:
            # Tesseract 버전 확인
            version = pytesseract.get_tesseract_version()
            logger.info(f"✅ Tesseract OCR 엔진 확인: v{version}")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Tesseract OCR 엔진 미설치 또는 오류: {e}")
            logger.info("💡 설치 방법: sudo apt-get install tesseract-ocr tesseract-ocr-kor")
            return False
    
    def extract_from_screenshot(self, screenshot_bytes: bytes) -> List[Dict]:
        """
        Playwright 스크린샷에서 OCR로 데이터 추출
        
        Args:
            screenshot_bytes: Playwright에서 캡처한 PNG 이미지 바이트
            
        Returns:
            추출된 종목 데이터 리스트
        """
        if not self.tesseract_available:
            logger.warning("⚠️ Tesseract 엔진 미설치 - OCR 백업 불가")
            return []
            
        try:
            # 바이트에서 이미지 로드
            image = Image.open(io.BytesIO(screenshot_bytes))
            logger.info(f"📸 스크린샷 로드: {image.size[0]}x{image.size[1]} pixels")
            
            # 이미지 전처리
            processed_image = self._preprocess_ocr_image(image)
            
            # OCR 텍스트 추출
            text = self._extract_text(processed_image)
            
            if not text or len(text.strip()) < 50:
                logger.warning("⚠️ OCR 텍스트 추출 실패 또는 내용 부족")
                return []
            
            logger.info(f"📝 OCR 텍스트 추출: {len(text)} 문자")
            
            # 텍스트에서 데이터 파싱
            return self._parse_ocr_text(text)
            
        except Exception as e:
            logger.error(f"❌ OCR 스크린샷 처리 중 오류: {e}")
            return []
    
    def extract_from_file(self, image_path: str) -> List[Dict]:
        """
        이미지 파일에서 OCR로 데이터 추출
        
        Args:
            image_path: 이미지 파일 경로
            
        Returns:
            추출된 종목 데이터 리스트
        """
        if not self.tesseract_available:
            logger.warning("⚠️ Tesseract 엔진 미설치 - OCR 백업 불가")
            return []
            
        try:
            image = Image.open(image_path)
            logger.info(f"📸 이미지 로드: {image_path} ({image.size[0]}x{image.size[1]})")
            
            processed_image = self._preprocess_ocr_image(image)
            text = self._extract_text(processed_image)
            
            if not text or len(text.strip()) < 50:
                logger.warning("⚠️ OCR 텍스트 추출 실패")
                return []
            
            return self._parse_ocr_text(text)
            
        except Exception as e:
            logger.error(f"❌ OCR 파일 처리 중 오류: {e}")
            return []
    
    def _preprocess_ocr_image(self, image: Image.Image) -> Image.Image:
        """
        OCR 정확도 향상을 위한 이미지 전처리
        
        전처리 단계:
        1. RGBA → RGB 변환
        2. 그레이스케일 변환
        3. 대비 강화 (+50%)
        4. 샤프닝 적용
        5. 크기 조정 (너무 작은 경우)
        """
        try:
            # RGBA → RGB 변환 (투명도 제거)
            if image.mode == 'RGBA':
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            elif image.mode != 'RGB':
                image = image.convert('RGB')
            
            # 그레이스케일 변환
            gray_image = image.convert('L')
            
            # 대비 강화 (1.5 = 50% 증가)
            enhancer = ImageEnhance.Contrast(gray_image)
            contrast_image = enhancer.enhance(1.5)
            
            # 샤프닝 적용
            sharpened_image = contrast_image.filter(ImageFilter.SHARPEN)
            
            # 크기 조정 (너무 작은 이미지 확대)
            width, height = sharpened_image.size
            if width < 1000:
                scale = 1000 / width
                new_size = (int(width * scale), int(height * scale))
                sharpened_image = sharpened_image.resize(new_size, Image.Resampling.LANCZOS)
                logger.info(f"🔍 이미지 확대: {width}x{height} → {new_size[0]}x{new_size[1]}")
            
            logger.info("✅ 이미지 전처리 완료 (그레이스케일, 대비강화, 샤프닝)")
            return sharpened_image
            
        except Exception as e:
            logger.warning(f"⚠️ 이미지 전처리 중 오류: {e}")
            return image
    
    def _extract_text(self, image: Image.Image) -> str:
        """
        pytesseract로 이미지에서 텍스트 추출
        
        설정:
        - OEM 3: LSTM 신경망 엔진 사용
        - PSM 6: 단일 텍스트 블록으로 가정
        - 언어: 한국어 + 영어
        """
        try:
            # Tesseract 설정
            custom_config = r'--oem 3 --psm 6 -l kor+eng'
            
            # OCR 실행
            text = pytesseract.image_to_string(image, config=custom_config)
            
            return text
            
        except Exception as e:
            logger.error(f"❌ Tesseract OCR 실행 오류: {e}")
            return ""
    
    def _parse_ocr_text(self, text: str) -> List[Dict]:
        """
        OCR 추출 텍스트에서 KRX ETS 종목 데이터 파싱
        
        지원 종목 패턴:
        - KAU25, KAU24 등 (할당배출권)
        - KCU25, KCU24 등 (상쇄배출권)
        - KOC21-26, KOC22-27 등 (외부사업 상쇄배출권)
        - i-KCU25, i-KOC21-26 등 (국제 상쇄배출권)
        
        예상 OCR 결과 형식:
        KAU25 10,050 -200 -1.95 10,050 10,050 10,000 147,000 1,475,650,000
        KCU25 9,300 0 0.00 0 0 0 0 0
        """
        result_list = []
        current_time = datetime.now()
        
        try:
            # 줄 단위로 분할
            lines = text.strip().split('\n')
            logger.info(f"📋 OCR 텍스트 라인 수: {len(lines)}")
            
            for line_num, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # 종목명 패턴 찾기 (KAU, KCU, KOC, i-KCU, i-KOC)
                symbol_patterns = [
                    r'(KAU\d{2})',           # KAU25, KAU24 등
                    r'(KCU\d{2})',           # KCU25, KCU24 등
                    r'(KOC\d{2}-\d{2})',     # KOC21-26, KOC22-27 등
                    r'(i-KCU\d{2})',         # i-KCU25 등
                    r'(i-KOC\d{2}-\d{2})',   # i-KOC21-26 등
                ]
                
                symbol = None
                for pattern in symbol_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        symbol = match.group(1).upper()
                        # i-로 시작하는 경우 소문자 i 유지
                        if symbol.startswith('I-'):
                            symbol = 'i-' + symbol[2:]
                        break
                
                if not symbol:
                    continue
                
                # 숫자들 추출 (가격, 거래량 등)
                # 음수 지원: -200, -1.95 등
                numbers = re.findall(r'-?[\d,]+\.?\d*', line)
                
                # 종목명에 포함된 숫자 제거 (예: KAU25의 25)
                numbers = [n for n in numbers if len(n) > 2 or '.' in n or n.startswith('-')]
                
                if len(numbers) < 4:
                    logger.debug(f"라인 {line_num}: 숫자 부족 ({len(numbers)}개) - {line[:50]}")
                    continue
                
                try:
                    # 데이터 파싱 (KRX ETS 테이블 컬럼 순서)
                    # 현재가, 대비, 등락률, 시가, 고가, 저가, 거래량, 거래대금
                    parsed_item = {
                        'date': current_time.strftime('%Y-%m-%d'),
                        'time': current_time.strftime('%H:%M:%S'),
                        'symbol': symbol,
                        'current_price': self._parse_number(numbers[0]),
                        'change': self._parse_number(numbers[1]) if len(numbers) > 1 else 0,
                        'change_rate': self._parse_number(numbers[2]) if len(numbers) > 2 else 0,
                        'open_price': self._parse_number(numbers[3]) if len(numbers) > 3 else 0,
                        'high_price': self._parse_number(numbers[4]) if len(numbers) > 4 else 0,
                        'low_price': self._parse_number(numbers[5]) if len(numbers) > 5 else 0,
                        'volume': self._parse_number(numbers[6]) if len(numbers) > 6 else 0,
                        'trading_value': self._parse_number(numbers[7]) if len(numbers) > 7 else 0,
                        'weighted_avg': 0,
                        'collection_time': current_time.strftime('%H:%M:%S'),
                        'data_source': 'pytesseract_ocr'
                    }
                    
                    # 유효성 검증 (현재가가 0보다 커야 함)
                    if parsed_item['current_price'] > 0:
                        result_list.append(parsed_item)
                        logger.info(f"🔍 OCR 파싱: {symbol} - {parsed_item['current_price']:,.0f}원")
                    else:
                        logger.debug(f"라인 {line_num}: 현재가 0 - {symbol}")
                        
                except (ValueError, IndexError) as e:
                    logger.debug(f"OCR 숫자 파싱 오류 (라인 {line_num}): {e}")
                    continue
        
        except Exception as e:
            logger.error(f"❌ OCR 텍스트 파싱 오류: {e}")
        
        logger.info(f"🎯 OCR 파싱 완료: {len(result_list)}개 종목")
        return result_list
    
    def _parse_number(self, text: str) -> float:
        """숫자 문자열을 float로 변환 (콤마, 공백 제거)"""
        try:
            cleaned = text.replace(',', '').replace(' ', '').strip()
            return float(cleaned) if cleaned else 0.0
        except (ValueError, TypeError):
            return 0.0
    
    def save_debug_screenshot(self, image_bytes: bytes, prefix: str = "ocr_debug") -> str:
        """디버깅용 스크린샷 저장"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{prefix}_{timestamp}.png"
            filepath = self.screenshots_dir / filename
            
            with open(filepath, 'wb') as f:
                f.write(image_bytes)
            
            logger.info(f"📸 디버그 스크린샷 저장: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.warning(f"스크린샷 저장 실패: {e}")
            return ""


# 기존 클래스명 호환성 유지
OCRKRXCollector = PytesseractOCRCollector


# 테스트 코드
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    collector = PytesseractOCRCollector()
    
    if collector.tesseract_available:
        print("✅ Tesseract OCR 엔진 사용 가능")
        print("📋 지원 종목: KAU, KCU, KOC, i-KCU, i-KOC")
        print("💡 사용법: extract_from_screenshot(bytes) 또는 extract_from_file(path)")
    else:
        print("❌ Tesseract OCR 엔진 미설치")
        print("💡 설치: sudo apt-get install tesseract-ocr tesseract-ocr-kor")
