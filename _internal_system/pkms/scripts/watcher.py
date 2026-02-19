"""
PKMS Inbox Watcher
The Big 3 채널 모니터링 및 자동 분류

Monitors:
- Channel A: heptabase, marginnote, readwise
- Channel B: manual
- Channel C: youtube
"""

import time
import shutil
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import logging
from classifier import PKMSClassifier

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class InboxHandler(FileSystemEventHandler):
    """The Big 3 채널 이벤트 핸들러"""

    def __init__(self, classifier: PKMSClassifier, config: dict):
        self.classifier = classifier
        self.config = config
        self.processed_files = set()

    def on_created(self, event):
        """파일 생성 이벤트 처리"""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        # 이미 처리된 파일 무시
        if file_path in self.processed_files:
            return

        # 숨김 파일 및 ignore 패턴 확인
        if self._should_ignore(file_path):
            logger.debug(f"Ignoring file: {file_path.name}")
            return

        # 허용된 확장자 확인
        allowed_exts = self.config['file_patterns']['allowed_extensions']
        if file_path.suffix not in allowed_exts:
            logger.info(f"Skipping unsupported file type: {file_path.name}")
            return

        # 파일이 완전히 쓰여질 때까지 대기
        time.sleep(1)

        self.process_file(file_path)

    def _should_ignore(self, file_path: Path) -> bool:
        """ignore 패턴 확인"""
        ignore_patterns = self.config['file_patterns']['ignore_patterns']

        filename = file_path.name.lower()

        for pattern in ignore_patterns:
            pattern = pattern.replace('*', '')
            if pattern.startswith('.') and filename.startswith('.'):
                return True
            elif pattern in filename:
                return True

        return False

    def process_file(self, file_path: Path):
        """파일 분류 및 이동 처리"""
        try:
            logger.info(f"Processing file: {file_path.name}")

            # 1. 소스 검증
            is_valid, channel = self.classifier.validate_source(str(file_path))
            if not is_valid:
                logger.warning(f"Skipped: {file_path.name} (not from The Big 3)")
                return

            # 2. 파일 내용 읽기
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                logger.error(f"Failed to read {file_path.name}: encoding issue")
                return

            # 3. 문서 분류
            result = self.classifier.classify_document(
                content,
                file_path.name,
                str(file_path)
            )

            # 무시된 파일
            if result.get('ignored'):
                logger.warning(f"File ignored: {file_path.name}")
                return

            # 4. 신뢰도 확인
            threshold = self.config['classification']['confidence_threshold']
            if result['confidence'] < threshold:
                logger.warning(
                    f"Low confidence ({result['confidence']:.2f}) for {file_path.name}"
                )
                logger.warning(f"Reason: {result['reasoning']}")

            # 5. 파일명 제안
            suggested_name = self.classifier.suggest_filename(
                content,
                file_path.name,
                result['category']
            )

            # 6. 대상 경로 설정
            target_dir = self.classifier.get_target_path(result['category'])
            target_dir.mkdir(parents=True, exist_ok=True)

            target_path = target_dir / suggested_name

            # 7. 파일명 중복 처리
            if target_path.exists():
                stem = target_path.stem
                suffix = target_path.suffix
                counter = 1
                while target_path.exists():
                    target_path = target_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            # 8. 파일 이동
            shutil.move(str(file_path), str(target_path))

            logger.info(f"✅ Moved: {file_path.name}")
            logger.info(f"   → {target_path}")
            logger.info(f"   Category: {result['category']}")
            logger.info(f"   Channel: {result['source_channel']}")
            logger.info(f"   Confidence: {result['confidence']:.2f}")

            # 9. 처리 완료 기록
            self.processed_files.add(file_path)

            # 10. 로그 파일 기록
            self._log_classification(file_path.name, result, target_path)

        except Exception as e:
            logger.error(f"Failed to process {file_path.name}: {e}")

    def _log_classification(self, original_name: str, result: dict, target_path: Path):
        """분류 결과 로그 기록"""
        log_dir = Path(self.config['paths']['logs'])
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / "classifications.log"

        with open(log_file, 'a', encoding='utf-8') as f:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"\n[{timestamp}]\n")
            f.write(f"Original: {original_name}\n")
            f.write(f"Target: {target_path}\n")
            f.write(f"Category: {result['category']}\n")
            f.write(f"Channel: {result.get('source_channel', 'Unknown')}\n")
            f.write(f"Confidence: {result['confidence']:.2f}\n")
            f.write(f"Reasoning: {result['reasoning']}\n")
            f.write("-" * 80 + "\n")


def start_watching():
    """The Big 3 채널 모니터링 시작"""
    classifier = PKMSClassifier()
    config = classifier.config

    # Inbox 경로
    inbox_path = Path(config['paths']['inbox'])

    if not inbox_path.exists():
        logger.error(f"Inbox not found: {inbox_path}")
        return

    event_handler = InboxHandler(classifier, config)
    observer = Observer()

    # 각 소스 폴더 모니터링
    sources = config['paths']['sources']
    for source_name, source_path in sources.items():
        source_dir = Path(source_path)
        if source_dir.exists():
            observer.schedule(event_handler, str(source_dir), recursive=False)
            logger.info(f"Watching: {source_name} ({source_dir.name})")
        else:
            logger.warning(f"Source not found: {source_name} ({source_path})")

    observer.start()
    logger.info("=" * 60)
    logger.info("PKMS Watcher started - Monitoring The Big 3 channels")
    logger.info("Press Ctrl+C to stop")
    logger.info("=" * 60)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logger.info("\nStopped watching")

    observer.join()


def main():
    """메인 함수"""
    start_watching()


if __name__ == "__main__":
    main()
