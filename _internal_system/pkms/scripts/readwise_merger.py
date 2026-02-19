"""
Readwise 파일 병합 모듈
복잡한 Readwise 파일명(날짜__제목.md)을 파싱하여 rolling.md에 병합
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class ReadwiseMerger:
    """Readwise 파일을 rolling.md에 병합하는 클래스"""

    def __init__(self, target_dir: Path):
        """
        Args:
            target_dir: rolling.md가 있는 대상 디렉토리
        """
        self.target_dir = Path(target_dir)
        self.rolling_file = self.target_dir / "rolling.md"

    def parse_readwise_filename(self, filename: str) -> Tuple[str, str]:
        """
        Readwise 파일명 파싱

        예시:
        - "20260218__Python_Best_Practices.md" -> ("2026-02-18", "Python Best Practices")
        - "2026-02-18__API_Design.md" -> ("2026-02-18", "API Design")

        Args:
            filename: Readwise 파일명

        Returns:
            (date_str, title): 날짜와 제목
        """
        # 확장자 제거
        name = Path(filename).stem

        # 날짜__제목 패턴 매칭
        pattern = r'^(\d{4}[-_]?\d{2}[-_]?\d{2})__(.+)$'
        match = re.match(pattern, name)

        if match:
            date_part = match.group(1)
            title_part = match.group(2)

            # 날짜 정규화 (YYYY-MM-DD)
            date_normalized = re.sub(r'[-_]', '', date_part)
            if len(date_normalized) == 8:
                date_str = f"{date_normalized[:4]}-{date_normalized[4:6]}-{date_normalized[6:]}"
            else:
                date_str = date_part

            # 제목 정규화 (언더스코어 -> 공백)
            title = title_part.replace('_', ' ')

            return date_str, title
        else:
            # 패턴이 맞지 않으면 파일명 그대로 반환
            return datetime.now().strftime('%Y-%m-%d'), name

    def extract_content(self, file_path: Path) -> str:
        """
        파일에서 실제 내용만 추출

        Args:
            file_path: 읽을 파일 경로

        Returns:
            정제된 내용
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

            # 메타데이터나 프론트매터 제거
            # YAML front matter 제거
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    content = parts[2].strip()

            return content

        except Exception as e:
            logger.error(f"Failed to read {file_path}: {e}")
            return ""

    def merge_to_rolling(self, file_path: Path, category: str) -> bool:
        """
        Readwise 파일을 rolling.md에 병합

        Args:
            file_path: 병합할 파일
            category: 카테고리명

        Returns:
            성공 여부
        """
        try:
            # 파일명 파싱
            date_str, title = self.parse_readwise_filename(file_path.name)

            # 내용 추출
            content = self.extract_content(file_path)

            if not content:
                logger.warning(f"Empty content in {file_path.name}")
                return False

            # rolling.md 파일이 없으면 생성
            if not self.rolling_file.exists():
                with open(self.rolling_file, 'w', encoding='utf-8') as f:
                    f.write("# Rolling Knowledge Base\n\n")
                    f.write("> 자동 생성된 지식 통합 문서\n\n")
                    f.write("---\n\n")

            # 병합 내용 생성
            merge_block = f"""
## {title}

> 📅 {date_str} | 📂 {category} | 📖 Readwise

{content}

---

"""

            # rolling.md에 추가
            with open(self.rolling_file, 'a', encoding='utf-8') as f:
                f.write(merge_block)

            logger.info(f"✅ Merged: {title} -> {self.rolling_file.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to merge {file_path}: {e}")
            return False


def test_parser():
    """파싱 테스트"""
    merger = ReadwiseMerger(Path("."))

    test_filenames = [
        "20260218__Python_Best_Practices.md",
        "2026-02-18__API_Design_Patterns.md",
        "20260217__Machine_Learning_Basics.md",
        "random_file.md"
    ]

    print("\n=== Readwise 파일명 파싱 테스트 ===\n")
    for filename in test_filenames:
        date, title = merger.parse_readwise_filename(filename)
        print(f"파일명: {filename}")
        print(f"  날짜: {date}")
        print(f"  제목: {title}\n")


if __name__ == "__main__":
    test_parser()
