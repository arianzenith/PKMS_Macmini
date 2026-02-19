"""
PKMS 설정 테스트 스크립트
API 키 없이도 기본 구조를 확인할 수 있습니다.
"""

import os
import sys
from pathlib import Path
import yaml

def test_structure():
    """폴더 구조 확인"""
    print("=" * 60)
    print("📁 PKMS 폴더 구조 확인")
    print("=" * 60)

    required_dirs = [
        'inbox',
        'knowledge_base',
        'knowledge_base/concepts',
        'knowledge_base/projects',
        'knowledge_base/resources',
        'knowledge_base/dailies',
        'archive',
        'templates',
        'scripts',
        'logs'
    ]

    all_exist = True
    for dir_path in required_dirs:
        full_path = Path(dir_path)
        exists = full_path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {dir_path}")
        if not exists:
            all_exist = False

    print()
    return all_exist

def test_config():
    """설정 파일 확인"""
    print("=" * 60)
    print("⚙️  설정 파일 확인")
    print("=" * 60)

    config_file = Path('config.yaml')
    if not config_file.exists():
        print("❌ config.yaml 파일이 없습니다.")
        return False

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        print("✅ config.yaml 로드 성공")
        print(f"\n카테고리:")
        for cat, desc in config.get('categories', {}).items():
            print(f"  - {cat}: {desc}")

        print(f"\n신뢰도 임계값: {config['classification']['confidence_threshold']}")
        print()
        return True
    except Exception as e:
        print(f"❌ config.yaml 로드 실패: {e}")
        return False

def test_scripts():
    """스크립트 파일 확인"""
    print("=" * 60)
    print("🐍 Python 스크립트 확인")
    print("=" * 60)

    scripts = [
        'scripts/classifier.py',
        'scripts/watcher.py',
        'scripts/manual_classify.py'
    ]

    all_exist = True
    for script in scripts:
        script_path = Path(script)
        exists = script_path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {script}")
        if not exists:
            all_exist = False

    print()
    return all_exist

def test_dependencies():
    """의존성 패키지 확인"""
    print("=" * 60)
    print("📦 의존성 패키지 확인")
    print("=" * 60)

    packages = {
        'anthropic': 'Claude API 클라이언트',
        'dotenv': '환경 변수 관리',
        'yaml': 'YAML 설정 파일',
        'watchdog': '파일 시스템 모니터링'
    }

    all_installed = True
    for package, description in packages.items():
        try:
            if package == 'dotenv':
                __import__('dotenv')
            else:
                __import__(package)
            print(f"✅ {package:15s} - {description}")
        except ImportError:
            print(f"❌ {package:15s} - {description} (설치 필요)")
            all_installed = False

    print()
    return all_installed

def test_env():
    """환경 변수 확인"""
    print("=" * 60)
    print("🔑 환경 변수 확인")
    print("=" * 60)

    env_file = Path('.env')
    env_example = Path('.env.example')

    if env_example.exists():
        print("✅ .env.example 파일 존재")
    else:
        print("❌ .env.example 파일 없음")

    if env_file.exists():
        print("✅ .env 파일 존재")

        # API 키 확인 (실제 값은 표시하지 않음)
        from dotenv import load_dotenv
        load_dotenv(env_file)

        api_key = os.getenv('ANTHROPIC_API_KEY')
        if api_key and api_key != 'your_api_key_here':
            print(f"✅ ANTHROPIC_API_KEY 설정됨 (길이: {len(api_key)})")
        else:
            print("⚠️  ANTHROPIC_API_KEY가 설정되지 않았습니다.")
            print("   .env 파일에 실제 API 키를 입력해주세요.")
    else:
        print("⚠️  .env 파일이 없습니다.")
        print("   .env.example을 복사하여 .env 파일을 만들고 API 키를 입력하세요:")
        print("   cp .env.example .env")

    print()

def test_templates():
    """템플릿 파일 확인"""
    print("=" * 60)
    print("📄 템플릿 파일 확인")
    print("=" * 60)

    templates = [
        'templates/daily-note.md',
        'templates/concept-note.md',
        'templates/project-note.md'
    ]

    all_exist = True
    for template in templates:
        template_path = Path(template)
        exists = template_path.exists()
        status = "✅" if exists else "❌"
        print(f"{status} {template}")
        if not exists:
            all_exist = False

    print()
    return all_exist

def test_inbox_files():
    """inbox 파일 확인"""
    print("=" * 60)
    print("📨 Inbox 파일 확인")
    print("=" * 60)

    inbox = Path('inbox')
    files = list(inbox.glob('*.md')) + list(inbox.glob('*.txt'))

    if files:
        print(f"발견된 파일: {len(files)}개")
        for file in files:
            print(f"  - {file.name}")
    else:
        print("inbox에 처리할 파일이 없습니다.")

    print()

def main():
    """메인 테스트 함수"""
    print("\n🚀 PKMS 설정 테스트 시작\n")

    results = {
        '폴더 구조': test_structure(),
        '설정 파일': test_config(),
        '스크립트': test_scripts(),
        '의존성': test_dependencies(),
        '템플릿': test_templates(),
    }

    test_env()
    test_inbox_files()

    print("=" * 60)
    print("📊 테스트 결과 요약")
    print("=" * 60)

    for test_name, passed in results.items():
        status = "✅ 통과" if passed else "❌ 실패"
        print(f"{test_name:15s}: {status}")

    all_passed = all(results.values())

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 모든 기본 테스트를 통과했습니다!")
        print("\n다음 단계:")
        print("1. .env 파일에 API 키를 설정하세요")
        print("2. inbox에 마크다운 파일을 넣어보세요")
        print("3. python scripts/manual_classify.py --inbox 실행")
    else:
        print("⚠️  일부 테스트가 실패했습니다.")
        print("위의 오류를 확인하고 수정해주세요.")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
