"""
PKMS Manual Classification Tool
The Big 3 채널의 파일을 수동으로 분류
Readwise 파일은 rolling.md에 자동 병합
"""

import sys
from pathlib import Path
from classifier import PKMSClassifier
from readwise_merger import ReadwiseMerger
import shutil
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def classify_file(file_path: str, auto_move: bool = False):
    """
    단일 파일 분류

    Args:
        file_path: 분류할 파일 경로
        auto_move: True면 자동으로 파일 이동, False면 결과만 출력
    """
    classifier = PKMSClassifier()
    config = classifier.config

    file = Path(file_path)

    if not file.exists():
        logger.error(f"File not found: {file_path}")
        return

    # 1. 소스 검증
    is_valid, channel = classifier.validate_source(str(file))
    if not is_valid:
        print("\n" + "=" * 60)
        print(f"❌ 파일: {file.name}")
        print("=" * 60)
        print("⚠️  이 파일은 허용된 소스(The Big 3)에서 온 것이 아닙니다.")
        print("\n허용된 소스:")
        print("  - Channel A: Heptabase, Marginnote, Readwise")
        print("  - Channel B: Google Drive inbox/manual")
        print("  - Channel C: YouTube")
        print("=" * 60 + "\n")
        return

    # 2. 파일 내용 읽기
    try:
        with open(file, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        return

    # 3. 분류 수행
    result = classifier.classify_document(content, file.name, str(file))

    if result.get('ignored'):
        print(f"\n⚠️  파일 무시됨: {result['reasoning']}")
        return

    # 4. 결과 출력
    print("\n" + "=" * 60)
    print(f"📄 파일: {file.name}")
    print("=" * 60)
    print(f"📍 소스 채널: {result['source_channel']}")
    print(f"📂 추천 카테고리: {result['category']}")
    print(f"💯 신뢰도: {result['confidence']:.2%}")
    print(f"💡 이유: {result['reasoning']}")
    print("=" * 60)

    # 5. 파일명 제안
    suggested_name = classifier.suggest_filename(content, file.name, result['category'])
    print(f"✏️  제안된 파일명: {suggested_name}")
    print("=" * 60 + "\n")

    # 6. 이동 처리
    if auto_move:
        _move_file(file, result, suggested_name, classifier)
    else:
        # 수동 확인
        try:
            response = input("이 분류대로 파일을 이동하시겠습니까? (y/n): ")
            if response.lower() == 'y':
                _move_file(file, result, suggested_name, classifier)
        except EOFError:
            logger.info("Interactive mode not available. Use --auto flag.")


def _move_file(file: Path, result: dict, suggested_name: str, classifier: PKMSClassifier):
    """파일 이동 실행"""
    target_dir = classifier.get_target_path(result['category'])
    target_dir.mkdir(parents=True, exist_ok=True)

    # Readwise 파일이면 rolling.md에 병합
    if classifier.is_readwise_file(str(file)):
        merger = ReadwiseMerger(target_dir)
        success = merger.merge_to_rolling(file, result['category'])

        if success:
            # 원본 파일 삭제 (이미 병합됨)
            file.unlink()
            logger.info(f"✅ Readwise 파일 병합 완료: {file.name} -> rolling.md")
            print(f"📝 Rolling.md에 병합되었습니다: {target_dir / 'rolling.md'}")
        else:
            logger.error(f"❌ Readwise 병합 실패: {file.name}")
        return

    # 일반 파일은 개별 파일로 이동
    target_path = target_dir / suggested_name

    # 중복 처리
    if target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        counter = 1
        while target_path.exists():
            target_path = target_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    shutil.move(str(file), str(target_path))
    logger.info(f"✅ 파일이 이동되었습니다: {target_path}")


def classify_all_sources(auto_mode: bool = False):
    """
    The Big 3 채널의 모든 파일 분류

    Args:
        auto_mode: True면 자동 이동, False면 하나씩 확인
    """
    classifier = PKMSClassifier()
    config = classifier.config

    # 모든 소스 경로에서 파일 찾기
    allowed_exts = config['file_patterns']['allowed_extensions']
    all_files = []

    for source_name, source_path in config['paths']['sources'].items():
        source_dir = Path(source_path)
        if not source_dir.exists():
            logger.warning(f"Source not found: {source_name}")
            continue

        for ext in allowed_exts:
            files = list(source_dir.glob(f"*{ext}"))
            all_files.extend([(f, source_name) for f in files])

    if not all_files:
        print("처리할 파일이 없습니다.")
        return

    print(f"\n총 {len(all_files)}개의 파일을 발견했습니다.\n")

    for file, source in all_files:
        print(f"\n📂 Source: {source}")
        classify_file(str(file), auto_move=auto_mode)
        print()


def main():
    """메인 함수"""
    import argparse

    parser = argparse.ArgumentParser(
        description="PKMS Manual Classification Tool (The Big 3 Only)"
    )
    parser.add_argument(
        'file',
        nargs='?',
        help='분류할 파일 경로 (선택사항)'
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='자동 모드 (확인 없이 이동)'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='모든 소스 채널 파일 분류'
    )

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("PKMS Manual Classifier")
    print("Project Rule: The Big 3 Channels Only")
    print("=" * 60)

    if args.all:
        classify_all_sources(auto_mode=args.auto)
    elif args.file:
        classify_file(args.file, auto_move=args.auto)
    else:
        classify_all_sources(auto_mode=args.auto)


if __name__ == "__main__":
    main()
