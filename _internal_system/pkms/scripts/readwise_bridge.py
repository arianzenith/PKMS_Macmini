#!/usr/bin/env python3
"""
Readwise Bridge - 구글 드라이브 루트에서 INBOX로 파일 자동 이동
"""

import os
import sys
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Tuple
import json
import subprocess
from dotenv import load_dotenv

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

# 경로 설정 (.env 기반)
STAGING_ROOT = Path(os.getenv("ROOT_PATH", "/Users/arian/GDrive/NotebookLM_Staging"))
GDRIVE_ROOT = STAGING_ROOT.parent
INBOX_READWISE = STAGING_ROOT / "00_INBOX/readwise"

# Readwise 파일 탐색 패턴
READWISE_PATTERNS = [
    "*Readwise*.gdoc",
    "*Readwise*.md",
    "*Readwise*.txt",
    "*readwise*.gdoc",
    "*readwise*.md",
    "*readwise*.txt",
    "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]__*.md",  # YYYYMMDD__Title.md
    "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]__*.txt",  # YYYYMMDD__Title.txt
]

# Readwise 폴더 이름 패턴
READWISE_FOLDER_NAMES = [
    "Readwise",
    "Readwise Highlights",
    "Readwise Reader",
]


class ReadwiseBridge:
    """Readwise 파일을 INBOX로 자동 이동하는 브릿지"""

    def __init__(self):
        self.gdrive_root = GDRIVE_ROOT
        self.inbox_target = INBOX_READWISE
        self.staging_root = STAGING_ROOT

        # INBOX 폴더 생성
        self.inbox_target.mkdir(parents=True, exist_ok=True)

        self.moved_files = []
        self.failed_files = []
        self.gdoc_files = []

    def find_readwise_folders(self) -> List[Path]:
        """Readwise 관련 폴더 찾기"""
        found_folders = []

        if not self.gdrive_root.exists():
            logger.error(f"Google Drive 루트 폴더를 찾을 수 없습니다: {self.gdrive_root}")
            return found_folders

        for item in self.gdrive_root.iterdir():
            if item.is_dir() and item.name in READWISE_FOLDER_NAMES:
                found_folders.append(item)
                logger.info(f"📁 Readwise 폴더 발견: {item.name}")

        return found_folders

    def find_readwise_files(self) -> List[Path]:
        """Google Drive 루트에서 Readwise 파일 찾기"""
        found_files = []

        # 1. 루트에서 직접 찾기
        for pattern in READWISE_PATTERNS:
            for file_path in self.gdrive_root.glob(pattern):
                if file_path.is_file() and not self._is_in_staging(file_path):
                    found_files.append(file_path)

        # 2. Readwise 폴더 안에서 찾기
        readwise_folders = self.find_readwise_folders()
        for folder in readwise_folders:
            for pattern in READWISE_PATTERNS:
                for file_path in folder.glob(pattern):
                    if file_path.is_file():
                        found_files.append(file_path)

            # 폴더 내 모든 .md, .txt 파일도 포함
            for ext in ['.md', '.txt']:
                for file_path in folder.rglob(f"*{ext}"):
                    if file_path.is_file():
                        found_files.append(file_path)

        # 중복 제거
        found_files = list(set(found_files))

        return found_files

    def _is_in_staging(self, file_path: Path) -> bool:
        """파일이 이미 Staging 폴더 안에 있는지 확인"""
        try:
            file_path.relative_to(self.staging_root)
            return True
        except ValueError:
            return False

    def convert_gdoc_to_markdown(self, gdoc_path: Path) -> Tuple[bool, str]:
        """
        Google Docs 파일을 Markdown으로 변환

        .gdoc 파일에서 doc_id를 추출하고 Google Docs API로 다운로드
        현재는 doc_id만 추출하고 수동 변환을 안내
        """
        try:
            with open(gdoc_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            doc_id = data.get('doc_id')
            if not doc_id:
                return False, "doc_id를 찾을 수 없습니다"

            # Google Docs 링크 생성
            doc_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

            logger.warning(f"⚠️  Google Docs 파일 발견: {gdoc_path.name}")
            logger.warning(f"   📄 Document ID: {doc_id}")
            logger.warning(f"   🔗 Export URL: {doc_url}")
            logger.warning(f"   💡 Readwise 설정에서 'Export Format: Markdown'으로 변경을 권장합니다")

            # .gdoc 파일 자체를 이동 (나중에 처리하기 위해)
            self.gdoc_files.append(gdoc_path)
            return True, doc_id

        except Exception as e:
            logger.error(f"❌ .gdoc 파일 처리 실패: {e}")
            return False, str(e)

    def move_file(self, source: Path) -> bool:
        """파일을 INBOX로 이동"""
        try:
            # .gdoc 파일은 별도 처리
            if source.suffix == '.gdoc':
                success, doc_id = self.convert_gdoc_to_markdown(source)
                if success:
                    # .gdoc 파일을 INBOX로 이동 (나중에 처리)
                    target = self.inbox_target / source.name
                    shutil.move(str(source), str(target))
                    logger.info(f"📦 .gdoc 파일 이동: {source.name} -> INBOX")
                    self.moved_files.append((source.name, 'gdoc', doc_id))
                    return True
                return False

            # 일반 파일 이동
            target = self.inbox_target / source.name

            # 중복 파일명 처리
            if target.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                stem = target.stem
                suffix = target.suffix
                target = self.inbox_target / f"{stem}_{timestamp}{suffix}"

            shutil.move(str(source), str(target))
            logger.info(f"✅ 파일 이동: {source.name} -> {target.name}")
            self.moved_files.append((source.name, target.name, 'moved'))

            return True

        except Exception as e:
            logger.error(f"❌ 파일 이동 실패: {source.name} - {e}")
            self.failed_files.append((source.name, str(e)))
            return False

    def run(self) -> dict:
        """브릿지 실행"""
        logger.info("=" * 70)
        logger.info("🌉 Readwise Bridge 시작")
        logger.info("=" * 70)
        logger.info(f"📂 Google Drive 루트: {self.gdrive_root}")
        logger.info(f"📥 INBOX 타겟: {self.inbox_target}")
        logger.info("")

        # 1. Readwise 파일 찾기
        logger.info("🔍 Readwise 파일 탐색 중...")
        files = self.find_readwise_files()

        if not files:
            logger.info("✅ 이동할 파일이 없습니다 (모두 처리됨)")
            return {
                'moved': 0,
                'failed': 0,
                'gdoc': 0,
                'files': []
            }

        logger.info(f"📦 발견된 파일: {len(files)}개")
        for f in files:
            logger.info(f"   - {f.name} ({f.suffix})")
        logger.info("")

        # 2. 파일 이동
        logger.info("🚚 파일 이동 시작...")
        for file_path in files:
            self.move_file(file_path)

        # 3. 결과 보고
        logger.info("")
        logger.info("=" * 70)
        logger.info("📊 브릿지 실행 결과")
        logger.info("=" * 70)
        logger.info(f"✅ 성공: {len(self.moved_files)}개")
        logger.info(f"❌ 실패: {len(self.failed_files)}개")
        logger.info(f"📄 Google Docs: {len(self.gdoc_files)}개")

        if self.gdoc_files:
            logger.info("")
            logger.info("⚠️  Google Docs 파일 처리 필요:")
            logger.info("   Readwise 설정에서 'Export Format: Markdown'으로 변경하면")
            logger.info("   .md 파일로 직접 내보내기가 가능합니다.")

        if self.failed_files:
            logger.info("")
            logger.info("❌ 실패한 파일:")
            for name, error in self.failed_files:
                logger.info(f"   - {name}: {error}")

        logger.info("=" * 70)

        return {
            'moved': len(self.moved_files),
            'failed': len(self.failed_files),
            'gdoc': len(self.gdoc_files),
            'files': self.moved_files
        }


def main():
    """메인 실행 함수"""
    bridge = ReadwiseBridge()
    result = bridge.run()

    # 종료 코드 반환
    if result['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
