#!/usr/bin/env python3
"""
Google Docs 다운로더
.gdoc 파일에서 doc_id를 추출하고 마크다운으로 다운로드
"""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Tuple
from dotenv import load_dotenv

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

INBOX_READWISE = Path(os.getenv("ROOT_PATH", "/Users/arian/GDrive/NotebookLM_Staging")) / "00_INBOX/readwise"


class GDocDownloader:
    """Google Docs 파일 다운로더"""

    def __init__(self):
        self.inbox = INBOX_READWISE
        self.downloaded = []
        self.failed = []

    def extract_doc_id(self, gdoc_path: Path) -> Optional[str]:
        """.gdoc 파일에서 doc_id 추출"""
        try:
            with open(gdoc_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get('doc_id')
        except Exception as e:
            logger.error(f"doc_id 추출 실패: {gdoc_path.name} - {e}")
            return None

    def download_as_text(self, doc_id: str, output_path: Path) -> bool:
        """
        Google Docs를 텍스트로 다운로드

        참고: 이 방법은 공개 문서이거나 로그인된 상태에서만 작동합니다.
        """
        export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

        try:
            # curl로 다운로드 시도
            cmd = [
                "curl", "-L",
                "-o", str(output_path),
                export_url
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0 and output_path.exists():
                size = output_path.stat().st_size
                if size > 100:  # 최소 크기 체크
                    logger.info(f"✅ 다운로드 성공: {output_path.name} ({size}B)")
                    return True
                else:
                    logger.warning(f"⚠️  다운로드된 파일이 너무 작음: {size}B")
                    output_path.unlink()  # 실패한 파일 삭제
                    return False
            else:
                logger.error(f"❌ 다운로드 실패: {result.stderr}")
                return False

        except Exception as e:
            logger.error(f"❌ 다운로드 예외: {e}")
            return False

    def process_gdoc_file(self, gdoc_path: Path) -> bool:
        """.gdoc 파일 처리"""
        logger.info(f"📄 처리 중: {gdoc_path.name}")

        # 1. doc_id 추출
        doc_id = self.extract_doc_id(gdoc_path)
        if not doc_id:
            logger.error(f"❌ doc_id를 찾을 수 없음: {gdoc_path.name}")
            self.failed.append((gdoc_path.name, "doc_id 없음"))
            return False

        logger.info(f"   Document ID: {doc_id}")

        # 2. 출력 파일 이름 생성
        base_name = gdoc_path.stem
        output_path = self.inbox / f"{base_name}.txt"

        # 중복 파일명 처리
        if output_path.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = self.inbox / f"{base_name}_{timestamp}.txt"

        # 3. 다운로드
        success = self.download_as_text(doc_id, output_path)

        if success:
            self.downloaded.append((gdoc_path.name, output_path.name))
            # 원본 .gdoc 파일 삭제
            gdoc_path.unlink()
            logger.info(f"   ✅ 원본 .gdoc 파일 삭제")
            return True
        else:
            self.failed.append((gdoc_path.name, "다운로드 실패"))
            return False

    def run(self) -> dict:
        """모든 .gdoc 파일 처리"""
        logger.info("=" * 70)
        logger.info("📥 Google Docs 다운로더 시작")
        logger.info("=" * 70)
        logger.info(f"📂 INBOX: {self.inbox}")
        logger.info("")

        # .gdoc 파일 찾기
        gdoc_files = list(self.inbox.glob("*.gdoc"))

        if not gdoc_files:
            logger.info("✅ 처리할 .gdoc 파일이 없습니다")
            return {
                'downloaded': 0,
                'failed': 0,
                'files': []
            }

        logger.info(f"📦 발견된 .gdoc 파일: {len(gdoc_files)}개")
        logger.info("")

        # 각 파일 처리
        for gdoc_path in gdoc_files:
            self.process_gdoc_file(gdoc_path)
            logger.info("")

        # 결과 보고
        logger.info("=" * 70)
        logger.info("📊 다운로드 결과")
        logger.info("=" * 70)
        logger.info(f"✅ 성공: {len(self.downloaded)}개")
        logger.info(f"❌ 실패: {len(self.failed)}개")

        if self.downloaded:
            logger.info("")
            logger.info("✅ 다운로드된 파일:")
            for original, downloaded in self.downloaded:
                logger.info(f"   {original} → {downloaded}")

        if self.failed:
            logger.info("")
            logger.info("❌ 실패한 파일:")
            for name, reason in self.failed:
                logger.info(f"   {name}: {reason}")

        logger.info("")
        logger.info("💡 참고:")
        logger.info("   - Readwise 설정에서 'Export Format: Markdown'으로")
        logger.info("     변경하면 .md 파일로 직접 내보내기가 가능합니다.")
        logger.info("   - Google Docs 다운로드가 실패하면 문서가 비공개일 수 있습니다.")
        logger.info("=" * 70)

        return {
            'downloaded': len(self.downloaded),
            'failed': len(self.failed),
            'files': self.downloaded
        }


def main():
    """메인 실행 함수"""
    downloader = GDocDownloader()
    result = downloader.run()

    if result['failed'] > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
