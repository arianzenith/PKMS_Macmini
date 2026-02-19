"""
PKMS 시스템 상태 확인 대시보드
Google Drive 미러링 경로 기반
"""

import os
from pathlib import Path
from datetime import datetime
import yaml
from dotenv import load_dotenv

# .env 로드 (스크립트 위치 기준 상위 폴더)
load_dotenv(Path(__file__).parent.parent / ".env")

ROOT_PATH = Path(os.getenv("ROOT_PATH", "/Users/arian/GDrive/NotebookLM_Staging"))


def load_config():
    """설정 파일 로드"""
    config_path = ROOT_PATH / "_internal_system/pkms/config.yaml"
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"⚠️  설정 파일 로드 실패: {e}")
        return None


def check_folder_status(folder_path: Path, name: str):
    """폴더 상태 확인"""
    if not folder_path.exists():
        return {
            'name': name,
            'status': '❌',
            'exists': False,
            'files': 0,
            'size': '0B'
        }

    # 파일 개수 세기
    files = list(folder_path.rglob('*'))
    file_count = len([f for f in files if f.is_file()])

    # 전체 크기 계산
    total_size = sum(f.stat().st_size for f in files if f.is_file())

    # 크기 포맷팅
    if total_size < 1024:
        size_str = f"{total_size}B"
    elif total_size < 1024 * 1024:
        size_str = f"{total_size / 1024:.1f}KB"
    elif total_size < 1024 * 1024 * 1024:
        size_str = f"{total_size / (1024 * 1024):.1f}MB"
    else:
        size_str = f"{total_size / (1024 * 1024 * 1024):.1f}GB"

    return {
        'name': name,
        'status': '✅',
        'exists': True,
        'files': file_count,
        'size': size_str
    }


def check_inbox_sources():
    """00_INBOX 소스 폴더 상세 확인"""
    inbox = ROOT_PATH / "00_INBOX"
    sources = {
        'heptabase': 'Channel A',
        'marginnote': 'Channel A',
        'readwise': 'Channel A',
        'manual': 'Channel B',
        'youtube': 'Channel C'
    }

    results = []
    for folder, channel in sources.items():
        folder_path = inbox / folder
        status = check_folder_status(folder_path, folder)
        status['channel'] = channel
        results.append(status)

    return results


def check_categories():
    """카테고리 폴더 확인"""
    categories = [
        '01_업무지식',
        '02_업무심화',
        '03_확장교양',
        '04_재미'
    ]

    results = []
    for cat in categories:
        cat_path = ROOT_PATH / cat
        status = check_folder_status(cat_path, cat)

        # rolling.md 확인
        rolling_md = cat_path / "rolling.md"
        if rolling_md.exists():
            rolling_size = rolling_md.stat().st_size
            rolling_lines = len(rolling_md.read_text(encoding='utf-8').split('\n'))
            status['rolling_md'] = f"{rolling_size}B ({rolling_lines} lines)"
        else:
            status['rolling_md'] = "❌ 없음"

        results.append(status)

    return results


def check_output():
    """99_OUTPUT 폴더 확인"""
    output = ROOT_PATH / "99_OUTPUT"

    results = []
    for cat in ['01_업무지식', '02_업무심화', '03_확장교양', '04_재미']:
        cat_path = output / cat
        if cat_path.exists():
            # 하위 폴더 확인
            insights = len(list((cat_path / "insights").glob('*'))) if (cat_path / "insights").exists() else 0
            summaries = len(list((cat_path / "summaries").glob('*'))) if (cat_path / "summaries").exists() else 0
            connections = len(list((cat_path / "connections").glob('*'))) if (cat_path / "connections").exists() else 0

            results.append({
                'name': cat,
                'status': '✅',
                'insights': insights,
                'summaries': summaries,
                'connections': connections
            })

    return results


def print_dashboard():
    """대시보드 출력"""
    print("\n" + "=" * 70)
    print("🎯 PKMS 시스템 상태 대시보드")
    print("=" * 70)
    print(f"📅 확인 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📂 기준 경로: {ROOT_PATH}")
    print("=" * 70)

    # 경로 존재 확인
    if not ROOT_PATH.exists():
        print(f"\n❌ 오류: 기준 경로가 존재하지 않습니다!")
        print(f"   경로: {ROOT_PATH}")
        return

    print(f"\n✅ 기준 경로 확인: 정상")

    # 1. 00_INBOX 상태
    print("\n" + "-" * 70)
    print("📥 00_INBOX (The Big 3 Sources)")
    print("-" * 70)

    inbox_sources = check_inbox_sources()
    for source in inbox_sources:
        print(f"{source['status']} {source['name']:15s} "
              f"({source['channel']}) - {source['files']} files, {source['size']}")

    # 2. 카테고리 폴더 상태
    print("\n" + "-" * 70)
    print("📚 지식 저장소 (Categories)")
    print("-" * 70)

    categories = check_categories()
    for cat in categories:
        print(f"{cat['status']} {cat['name']:15s} - "
              f"{cat['files']} files, {cat['size']}")
        print(f"   └─ rolling.md: {cat['rolling_md']}")

    # 3. 99_OUTPUT 상태
    print("\n" + "-" * 70)
    print("📤 99_OUTPUT (AI Outputs)")
    print("-" * 70)

    outputs = check_output()
    for out in outputs:
        print(f"{out['status']} {out['name']:15s} - "
              f"insights: {out['insights']}, "
              f"summaries: {out['summaries']}, "
              f"connections: {out['connections']}")

    # 4. 시스템 폴더 상태
    print("\n" + "-" * 70)
    print("🔧 _internal_system")
    print("-" * 70)

    internal = ROOT_PATH / "_internal_system"
    pkms = internal / "pkms"
    archive = internal / "archive"

    print(f"{'✅' if internal.exists() else '❌'} _internal_system/")
    print(f"   ├─ {'✅' if pkms.exists() else '❌'} pkms/ (scripts, config)")
    print(f"   └─ {'✅' if archive.exists() else '❌'} archive/ (backups)")

    # 5. 설정 확인
    print("\n" + "-" * 70)
    print("⚙️  설정 파일")
    print("-" * 70)

    config_file = pkms / "config.yaml"
    env_file = pkms / ".env"

    print(f"{'✅' if config_file.exists() else '❌'} config.yaml")
    print(f"{'✅' if env_file.exists() else '❌'} .env")

    # 환경 변수 확인
    if env_file.exists():
        with open(env_file, 'r') as f:
            content = f.read()
            if 'ROOT_PATH=' in content:
                print(f"   └─ ✅ ROOT_PATH 설정됨")
            if 'ANTHROPIC_API_KEY=' in content and 'sk-ant-' in content:
                print(f"   └─ ✅ API KEY 설정됨")

    # 요약
    print("\n" + "=" * 70)
    print("📊 요약")
    print("=" * 70)

    total_inbox = sum(s['files'] for s in inbox_sources)
    total_categories = sum(c['files'] for c in categories)
    total_outputs = sum(o['insights'] + o['summaries'] + o['connections'] for o in outputs)

    print(f"📥 INBOX 파일: {total_inbox}개")
    print(f"📚 저장소 파일: {total_categories}개")
    print(f"📤 OUTPUT 파일: {total_outputs}개")
    print(f"🟢 시스템 상태: {'정상' if ROOT_PATH.exists() else '오류'}")

    print("\n" + "=" * 70)

    # Readwise 대기 메시지
    if total_inbox == 0:
        print("\n💡 Readwise 연결 대기 중...")
        print("   파일이 00_INBOX/readwise/에 저장되면 자동 처리됩니다.")

    print()


def main():
    """메인 함수"""
    try:
        print_dashboard()
    except Exception as e:
        print(f"\n❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
