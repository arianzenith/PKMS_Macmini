#!/usr/bin/env python3
"""
PKMS 통합 파이프라인
Readwise Bridge → Classification → Status Check
"""

import os
import sys
import subprocess
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).parent
BRIDGE_SCRIPT = SCRIPTS_DIR / "readwise_bridge.py"
GDOC_DOWNLOADER_SCRIPT = SCRIPTS_DIR / "gdoc_downloader.py"
CLASSIFIER_SCRIPT = SCRIPTS_DIR / "manual_classify.py"
STATUS_SCRIPT = SCRIPTS_DIR / "check_status.py"


def run_script(script_path: Path, args: list = None) -> tuple:
    """스크립트 실행 헬퍼"""
    cmd = ["python3", str(script_path)]
    if args:
        cmd.extend(args)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        logger.error(f"스크립트 실행 실패: {script_path.name} - {e}")
        return 1, "", str(e)


def main():
    """통합 파이프라인 실행"""
    logger.info("=" * 70)
    logger.info("🚀 PKMS 통합 파이프라인 시작")
    logger.info("=" * 70)
    logger.info("")

    # Step 1: Readwise Bridge
    logger.info("📍 Step 1/4: Readwise Bridge 실행")
    logger.info("-" * 70)
    returncode, stdout, stderr = run_script(BRIDGE_SCRIPT)
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if returncode != 0:
        logger.warning("⚠️  브릿지 실행 중 일부 오류 발생 (계속 진행)")
    logger.info("")

    # Step 2: Google Docs Downloader
    logger.info("📍 Step 2/4: Google Docs 다운로드 시도")
    logger.info("-" * 70)
    returncode, stdout, stderr = run_script(GDOC_DOWNLOADER_SCRIPT)
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if returncode != 0:
        logger.warning("⚠️  Google Docs 다운로드 실패 (Markdown 형식 사용 권장)")
    logger.info("")

    # Step 3: Classification
    logger.info("📍 Step 3/4: 자동 분류 실행")
    logger.info("-" * 70)
    returncode, stdout, stderr = run_script(CLASSIFIER_SCRIPT, ["--all", "--auto"])
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    if returncode != 0:
        logger.warning("⚠️  분류 중 일부 오류 발생 (계속 진행)")
    logger.info("")

    # Step 4: Status Check
    logger.info("📍 Step 4/4: 시스템 상태 확인")
    logger.info("-" * 70)
    returncode, stdout, stderr = run_script(STATUS_SCRIPT)
    print(stdout)
    if stderr:
        print(stderr, file=sys.stderr)

    logger.info("")
    logger.info("=" * 70)
    logger.info("✅ PKMS 파이프라인 완료")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
