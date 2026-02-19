#!/usr/bin/env python3
"""
Root Sweeper - 내 드라이브 루트의 Readwise 파일 자동 청소
Readwise가 루트에 생성한 파일을 INBOX로 강제 이동
"""

import os
import sys
import shutil
import logging
from pathlib import Path
from datetime import datetime

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 경로 설정 (CloudStorage 실제 경로)
GDRIVE_ROOT = Path("/Users/admin/Library/CloudStorage/GoogleDrive-geneses99@gmail.com/내 드라이브")
INBOX_READWISE = GDRIVE_ROOT / "NotebookLM_Staging/00_INBOX/readwise"

# Readwise 파일 패턴
READWISE_FILE_PATTERNS = [
    "*Readwise Highlights*.gdoc",
    "*Readwise Highlights*.md",
    "*Readwise Highlights*.txt",
    "Readwise*.gdoc",
    "Readwise*.md",
    "Readwise*.txt",
]


class RootSweeper:
    """내 드라이브 루트의 Readwise 파일 청소기"""

    def __init__(self):
        self.gdrive_root = GDRIVE_ROOT
        self.inbox_target = INBOX_READWISE
        self.moved_files = []
        self.failed_files = []

        # INBOX 폴더 생성
        self.inbox_target.mkdir(parents=True, exist_ok=True)

    def find_readwise_files_in_root(self):
        """루트에서 Readwise 파일 찾기 (깊이 1만)"""
        found_files = []

        if not self.gdrive_root.exists():
            logger.error(f"Google Drive 루트 폴더를 찾을 수 없습니다: {self.gdrive_root}")
            return found_files

        # 루트 디렉토리에서만 검색 (maxdepth=1)
        for pattern in READWISE_FILE_PATTERNS:
            for file_path in self.gdrive_root.glob(pattern):
                if file_path.is_file():
                    # NotebookLM_Staging 안의 파일은 제외
                    try:
                        file_path.relative_to(self.gdrive_root / "NotebookLM_Staging")
                        continue  # Staging 안의 파일이면 스킵
                    except ValueError:
                        # Staging 밖의 파일이면 포함
                        found_files.append(file_path)

        return found_files

    def move_file(self, source: Path) -> bool:
        """파일을 INBOX로 이동"""
        try:
            target = self.inbox_target / source.name

            # 중복 파일명 처리
            if target.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = target.stem
                suffix = target.suffix
                target = self.inbox_target / f"{stem}_{timestamp}{suffix}"

            shutil.move(str(source), str(target))
            logger.info(f"✅ 이동 완료: {source.name} -> {target.name}")
            self.moved_files.append((source.name, target.name))

            return True

        except Exception as e:
            logger.error(f"❌ 이동 실패: {source.name} - {e}")
            self.failed_files.append((source.name, str(e)))
            return False

    def sweep(self) -> dict:
        """루트 청소 실행"""
        logger.info("=" * 70)
        logger.info("🧹 Root Sweeper 시작")
        logger.info("=" * 70)
        logger.info(f"📂 루트: {self.gdrive_root}")
        logger.info(f"📥 타겟: {self.inbox_target}")
        logger.info("")

        # 1. 루트에서 Readwise 파일 찾기
        logger.info("🔍 루트에서 Readwise 파일 탐색 중...")
        files = self.find_readwise_files_in_root()

        if not files:
            logger.info("✅ 루트에 Readwise 파일 없음 (청소 완료)")
            return {
                'moved': 0,
                'failed': 0,
                'files': []
            }

        logger.info(f"📦 발견된 파일: {len(files)}개")
        for f in files:
            logger.info(f"   - {f.name} ({f.stat().st_size}B)")
        logger.info("")

        # 2. 파일 이동
        logger.info("🚚 INBOX로 이동 시작...")
        for file_path in files:
            self.move_file(file_path)

        # 3. 결과 보고
        logger.info("")
        logger.info("=" * 70)
        logger.info("📊 Root Sweeper 완료")
        logger.info("=" * 70)
        logger.info(f"✅ 이동: {len(self.moved_files)}개")
        logger.info(f"❌ 실패: {len(self.failed_files)}개")

        if self.moved_files:
            logger.info("")
            logger.info("✅ 이동된 파일:")
            for original, moved in self.moved_files:
                logger.info(f"   {original} → {moved}")

        if self.failed_files:
            logger.info("")
            logger.info("❌ 실패한 파일:")
            for name, error in self.failed_files:
                logger.info(f"   {name}: {error}")

        logger.info("=" * 70)

        return {
            'moved': len(self.moved_files),
            'failed': len(self.failed_files),
            'files': self.moved_files
        }


def main():
    """메인 실행 함수"""
    sweeper = RootSweeper()
    result = sweeper.sweep()

    # 종료 코드 반환
    if result['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
