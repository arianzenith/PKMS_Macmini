"""
Readwise Highlights 자동 감지 및 이동
Google Drive 루트에 생성된 Readwise 파일을 00_INBOX/readwise로 이동
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

ROOT_PATH = Path(os.getenv("ROOT_PATH", "/Users/arian/GDrive/NotebookLM_Staging"))
GDRIVE_ROOT = ROOT_PATH.parent
READWISE_TARGET = ROOT_PATH / "00_INBOX/readwise"


def find_readwise_files():
    """Google Drive 루트에서 Readwise 파일 찾기"""
    readwise_files = []

    # Readwise Highlights 폴더 찾기
    readwise_folder = GDRIVE_ROOT / "Readwise Highlights"
    if readwise_folder.exists():
        print(f"✅ 'Readwise Highlights' 폴더 발견!")
        for file in readwise_folder.rglob('*.md'):
            readwise_files.append(file)

    # 루트에 있는 Readwise 파일 찾기 (패턴: 날짜__제목.md)
    for file in GDRIVE_ROOT.glob('*.md'):
        if '__' in file.stem and file.name != 'README.md':
            # Readwise 패턴일 가능성
            readwise_files.append(file)

    return readwise_files


def move_readwise_file(file_path: Path):
    """Readwise 파일을 00_INBOX/readwise로 이동"""
    try:
        # 대상 폴더 확인
        READWISE_TARGET.mkdir(parents=True, exist_ok=True)

        # 파일 이동
        target_path = READWISE_TARGET / file_path.name

        # 중복 처리
        if target_path.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            stem = target_path.stem
            suffix = target_path.suffix
            target_path = READWISE_TARGET / f"{stem}_{timestamp}{suffix}"

        shutil.move(str(file_path), str(target_path))
        print(f"   ✅ 이동 완료: {file_path.name} → {target_path.name}")
        return True

    except Exception as e:
        print(f"   ❌ 이동 실패: {file_path.name} - {e}")
        return False


def main():
    """메인 함수"""
    print("\n" + "=" * 70)
    print("🔍 Readwise Highlights 자동 감지 및 이동")
    print("=" * 70)
    print(f"📂 Google Drive 루트: {GDRIVE_ROOT}")
    print(f"📥 대상 폴더: {READWISE_TARGET}")
    print("=" * 70 + "\n")

    # Readwise 파일 찾기
    print("🔍 Readwise 파일 검색 중...")
    readwise_files = find_readwise_files()

    if not readwise_files:
        print("   ℹ️  Readwise 파일을 찾을 수 없습니다.")
        print("   💡 Readwise 연결 후 파일이 생성되면 다시 실행하세요.\n")
        return

    # 파일 발견
    print(f"\n✅ {len(readwise_files)}개의 Readwise 파일 발견!\n")

    for file in readwise_files:
        print(f"📄 {file.name}")
        move_readwise_file(file)

    print("\n" + "=" * 70)
    print("✅ Readwise 파일 이동 완료!")
    print("=" * 70)

    # 분류 프로세스 안내
    print("\n💡 다음 단계: 자동 분류 실행")
    print("   cd _internal_system/pkms/scripts")
    print("   python manual_classify.py --all --auto")
    print()


if __name__ == "__main__":
    main()
