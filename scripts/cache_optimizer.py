#!/usr/bin/env python3
"""
KRX ETS v3.1 캐시 최적화 도구
GitHub Actions 캐시 성능을 최대화하는 유틸리티
"""

import os
import subprocess
import json
import time
from pathlib import Path
from typing import Dict, List, Tuple
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CacheOptimizer:
    """GitHub Actions 캐시 최적화 도구"""
    
    def __init__(self):
        self.cache_paths = {
            'pip': Path.home() / '.cache' / 'pip',
            'playwright': Path.home() / '.cache' / 'ms-playwright',
            'site_packages': Path('/usr/local/lib/python3.9/site-packages'),
            'apt': Path('/var/cache/apt')
        }
        
    def analyze_cache_status(self) -> Dict:
        """현재 캐시 상태 분석"""
        try:
            logger.info("=== 캐시 상태 분석 시작 ===")
            
            status = {
                'timestamp': time.time(),
                'caches': {},
                'total_size': 0,
                'efficiency_score': 0,
                'recommendations': []
            }
            
            for cache_name, cache_path in self.cache_paths.items():
                cache_info = self._analyze_single_cache(cache_name, cache_path)
                status['caches'][cache_name] = cache_info
                status['total_size'] += cache_info['size_bytes']
            
            # 효율성 점수 계산
            active_caches = len([c for c in status['caches'].values() if c['exists']])
            status['efficiency_score'] = (active_caches / len(self.cache_paths)) * 100
            
            # 추천사항 생성
            status['recommendations'] = self._generate_recommendations(status)
            
            logger.info(f"캐시 효율성: {status['efficiency_score']:.1f}%")
            logger.info(f"총 캐시 크기: {self._format_size(status['total_size'])}")
            
            return status
            
        except Exception as e:
            logger.error(f"캐시 분석 중 오류: {e}")
            return {'error': str(e)}
    
    def _analyze_single_cache(self, name: str, path: Path) -> Dict:
        """개별 캐시 분석"""
        try:
            if not path.exists():
                return {
                    'exists': False,
                    'size_bytes': 0,
                    'size_human': '0B',
                    'file_count': 0,
                    'last_modified': None
                }
            
            # 크기 계산
            size_bytes = self._get_directory_size(path)
            
            # 파일 개수
            file_count = sum(1 for _ in path.rglob('*') if _.is_file())
            
            # 최근 수정 시간
            try:
                last_modified = max(f.stat().st_mtime for f in path.rglob('*') if f.is_file())
            except (ValueError, OSError):
                last_modified = None
            
            cache_info = {
                'exists': True,
                'size_bytes': size_bytes,
                'size_human': self._format_size(size_bytes),
                'file_count': file_count,
                'last_modified': last_modified,
                'path': str(path)
            }
            
            # 캐시별 특별 정보
            if name == 'playwright':
                cache_info['browser_versions'] = self._get_browser_versions(path)
            elif name == 'pip':
                cache_info['wheel_count'] = len(list(path.glob('**/*.whl')))
            elif name == 'site_packages':
                cache_info['package_count'] = len([d for d in path.iterdir() if d.is_dir()])
            
            return cache_info
            
        except Exception as e:
            logger.warning(f"{name} 캐시 분석 실패: {e}")
            return {'exists': False, 'error': str(e)}
    
    def _get_directory_size(self, path: Path) -> int:
        """디렉토리 크기 계산"""
        try:
            result = subprocess.run(
                ['du', '-sb', str(path)], 
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return int(result.stdout.split()[0])
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            pass
        
        # fallback: Python으로 계산 (느림)
        try:
            return sum(f.stat().st_size for f in path.rglob('*') if f.is_file())
        except Exception:
            return 0
    
    def _get_browser_versions(self, playwright_path: Path) -> List[str]:
        """Playwright 브라우저 버전 목록"""
        try:
            return [d.name for d in playwright_path.iterdir() 
                   if d.is_dir() and d.name.startswith('chromium-')]
        except Exception:
            return []
    
    def _format_size(self, bytes_size: int) -> str:
        """바이트를 인간이 읽기 쉬운 형태로 변환"""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.1f}{unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.1f}PB"
    
    def _generate_recommendations(self, status: Dict) -> List[str]:
        """캐시 최적화 추천사항 생성"""
        recommendations = []
        
        # 총 크기 체크 (5GB 한도)
        total_gb = status['total_size'] / (1024**3)
        if total_gb > 4:
            recommendations.append("캐시 크기가 4GB 초과 - 정리 필요")
        
        # Playwright 브라우저 중복 체크
        playwright_cache = status['caches'].get('playwright', {})
        if playwright_cache.get('exists') and len(playwright_cache.get('browser_versions', [])) > 1:
            recommendations.append("Playwright 중복 브라우저 버전 - 이전 버전 삭제 권장")
        
        # pip 캐시 크기 체크
        pip_cache = status['caches'].get('pip', {})
        if pip_cache.get('size_bytes', 0) > 1024**3:  # 1GB 초과
            recommendations.append("pip 캐시 크기 1GB 초과 - 대형 파일 정리 권장")
        
        # 효율성 점수 기반 추천
        if status['efficiency_score'] < 50:
            recommendations.append("캐시 효율성 저조 - 캐시 warming 권장")
        elif status['efficiency_score'] >= 75:
            recommendations.append("높은 캐시 효율성 - 최적 상태")
        
        return recommendations
    
    def optimize_caches(self) -> Dict:
        """캐시 최적화 실행"""
        try:
            logger.info("=== 캐시 최적화 시작 ===")
            
            optimization_results = {
                'timestamp': time.time(),
                'actions_taken': [],
                'space_saved': 0,
                'errors': []
            }
            
            # 1. Playwright 브라우저 중복 제거
            self._cleanup_playwright_browsers(optimization_results)
            
            # 2. pip 캐시 대형 파일 정리
            self._cleanup_pip_cache(optimization_results)
            
            # 3. 임시 파일 정리
            self._cleanup_temp_files(optimization_results)
            
            logger.info(f"최적화 완료 - 절약된 공간: {self._format_size(optimization_results['space_saved'])}")
            
            return optimization_results
            
        except Exception as e:
            logger.error(f"캐시 최적화 중 오류: {e}")
            return {'error': str(e)}
    
    def _cleanup_playwright_browsers(self, results: Dict):
        """Playwright 브라우저 중복 제거"""
        try:
            playwright_path = self.cache_paths['playwright']
            if not playwright_path.exists():
                return
            
            browser_dirs = [d for d in playwright_path.iterdir() 
                          if d.is_dir() and d.name.startswith('chromium-')]
            
            if len(browser_dirs) <= 1:
                return
            
            # 가장 최신 버전만 유지
            browser_dirs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            latest_browser = browser_dirs[0]
            
            space_saved = 0
            for old_browser in browser_dirs[1:]:
                try:
                    old_size = self._get_directory_size(old_browser)
                    subprocess.run(['rm', '-rf', str(old_browser)], check=True)
                    space_saved += old_size
                    results['actions_taken'].append(f"이전 브라우저 삭제: {old_browser.name}")
                except Exception as e:
                    results['errors'].append(f"브라우저 삭제 실패: {e}")
            
            results['space_saved'] += space_saved
            logger.info(f"Playwright 브라우저 정리: {self._format_size(space_saved)} 절약")
            
        except Exception as e:
            results['errors'].append(f"Playwright 정리 실패: {e}")
    
    def _cleanup_pip_cache(self, results: Dict):
        """pip 캐시 대형 파일 정리"""
        try:
            pip_path = self.cache_paths['pip']
            if not pip_path.exists():
                return
            
            # 100MB 이상 파일 삭제
            large_files = []
            for file_path in pip_path.rglob('*'):
                if file_path.is_file():
                    try:
                        size = file_path.stat().st_size
                        if size > 100 * 1024 * 1024:  # 100MB
                            large_files.append((file_path, size))
                    except Exception:
                        continue
            
            space_saved = 0
            for file_path, size in large_files:
                try:
                    file_path.unlink()
                    space_saved += size
                    results['actions_taken'].append(f"대형 파일 삭제: {file_path.name} ({self._format_size(size)})")
                except Exception as e:
                    results['errors'].append(f"파일 삭제 실패: {e}")
            
            results['space_saved'] += space_saved
            if space_saved > 0:
                logger.info(f"pip 캐시 정리: {self._format_size(space_saved)} 절약")
            
        except Exception as e:
            results['errors'].append(f"pip 캐시 정리 실패: {e}")
    
    def _cleanup_temp_files(self, results: Dict):
        """임시 파일 정리"""
        try:
            temp_patterns = [
                '/tmp/*playwright*',
                '/tmp/*chromium*', 
                '/tmp/pip-*',
                '~/.cache/pip/log/*'
            ]
            
            space_saved = 0
            for pattern in temp_patterns:
                try:
                    result = subprocess.run(
                        f'find {pattern} -type f -size +10M 2>/dev/null | head -10', 
                        shell=True, capture_output=True, text=True
                    )
                    
                    if result.stdout:
                        files = result.stdout.strip().split('\n')
                        for file_path in files:
                            if file_path:
                                try:
                                    size = os.path.getsize(file_path)
                                    os.remove(file_path)
                                    space_saved += size
                                    results['actions_taken'].append(f"임시 파일 삭제: {os.path.basename(file_path)}")
                                except Exception:
                                    continue
                                    
                except Exception as e:
                    results['errors'].append(f"임시 파일 정리 실패 ({pattern}): {e}")
            
            results['space_saved'] += space_saved
            if space_saved > 0:
                logger.info(f"임시 파일 정리: {self._format_size(space_saved)} 절약")
                
        except Exception as e:
            results['errors'].append(f"임시 파일 정리 실패: {e}")

    def generate_cache_report(self) -> str:
        """캐시 상태 리포트 생성"""
        try:
            status = self.analyze_cache_status()
            
            report = "=== KRX ETS v3.1 캐시 상태 리포트 ===\n\n"
            
            # 전체 요약
            report += f"📊 캐시 효율성: {status.get('efficiency_score', 0):.1f}%\n"
            report += f"💾 총 캐시 크기: {self._format_size(status.get('total_size', 0))}\n"
            report += f"⏰ 생성 시간: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            
            # 개별 캐시 상태
            report += "📋 개별 캐시 상태:\n"
            for cache_name, cache_info in status.get('caches', {}).items():
                if cache_info.get('exists'):
                    report += f"✅ {cache_name}: {cache_info['size_human']}"
                    if cache_name == 'pip' and 'wheel_count' in cache_info:
                        report += f" ({cache_info['wheel_count']}개 wheel)"
                    elif cache_name == 'playwright' and 'browser_versions' in cache_info:
                        report += f" ({len(cache_info['browser_versions'])}개 브라우저)"
                    elif cache_name == 'site_packages' and 'package_count' in cache_info:
                        report += f" ({cache_info['package_count']}개 패키지)"
                    report += "\n"
                else:
                    report += f"❌ {cache_name}: 없음\n"
            
            # 추천사항
            recommendations = status.get('recommendations', [])
            if recommendations:
                report += "\n🔧 최적화 추천사항:\n"
                for rec in recommendations:
                    report += f"• {rec}\n"
            
            return report
            
        except Exception as e:
            return f"❌ 캐시 리포트 생성 실패: {e}"

def main():
    """메인 실행 함수"""
    try:
        import argparse
        parser = argparse.ArgumentParser(description='KRX ETS 캐시 최적화 도구')
        parser.add_argument('--analyze', action='store_true', help='캐시 상태 분석')
        parser.add_argument('--optimize', action='store_true', help='캐시 최적화 실행')
        parser.add_argument('--report', action='store_true', help='캐시 리포트 생성')
        parser.add_argument('--json', action='store_true', help='JSON 형태로 출력')
        
        args = parser.parse_args()
        
        optimizer = CacheOptimizer()
        
        if args.optimize:
            print("🚀 캐시 최적화 실행 중...")
            result = optimizer.optimize_caches()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"✅ 최적화 완료")
                print(f"💾 절약된 공간: {optimizer._format_size(result.get('space_saved', 0))}")
                print(f"🔧 수행된 작업: {len(result.get('actions_taken', []))}개")
        
        if args.analyze:
            print("📊 캐시 상태 분석 중...")
            result = optimizer.analyze_cache_status()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"💾 캐시 효율성: {result.get('efficiency_score', 0):.1f}%")
                print(f"📦 총 캐시 크기: {optimizer._format_size(result.get('total_size', 0))}")
        
        if args.report:
            print("📋 캐시 리포트 생성 중...")
            report = optimizer.generate_cache_report()
            print(report)
        
        if not any([args.analyze, args.optimize, args.report]):
            print("📊 기본 캐시 분석 실행...")
            result = optimizer.analyze_cache_status()
            print(f"💾 캐시 효율성: {result.get('efficiency_score', 0):.1f}%")
            print(f"📦 총 캐시 크기: {optimizer._format_size(result.get('total_size', 0))}")
            
            recommendations = result.get('recommendations', [])
            if recommendations:
                print("\n🔧 추천사항:")
                for rec in recommendations:
                    print(f"  • {rec}")
        
    except Exception as e:
        logger.error(f"실행 중 오류: {e}")
        exit(1)

if __name__ == "__main__":
    main()
