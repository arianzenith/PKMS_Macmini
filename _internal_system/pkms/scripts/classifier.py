"""
PKMS Classification Engine
Project Rule: Only 3 allowed sources (The Big 3)

Channels:
- Channel A: Marginnote4 → Readwise → Heptabase (MCP)
- Channel B: Google Drive inbox/manual (Work Files)
- Channel C: YouTube (MCP 자막/요약)

Target: NotebookLM 4 Categories
"""

import os
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
from anthropic import Anthropic
import logging

# 환경 변수 로드
load_dotenv(Path(__file__).parent.parent / ".env")

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class PKMSClassifier:
    """
    PKMS 문서 분류 엔진
    The Big 3 소스만 처리
    """

    def __init__(self, config_path: str = "../config.yaml"):
        """분류 엔진 초기화"""
        self.config = self._load_config(config_path)
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = os.getenv("MODEL_NAME", "claude-sonnet-4-5-20250929")

        # 허용된 소스 경로 저장
        self.allowed_sources = self._get_allowed_source_paths()

    def _load_config(self, config_path: str) -> Dict:
        """설정 파일 로드"""
        config_file = Path(__file__).parent / config_path
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _get_allowed_source_paths(self) -> List[str]:
        """허용된 3개 채널의 경로 목록 반환"""
        sources = []
        for key, path in self.config['paths']['sources'].items():
            sources.append(path)
        return sources

    def validate_source(self, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        파일이 허용된 3개 채널 중 하나에서 왔는지 검증

        Args:
            file_path: 검증할 파일 경로

        Returns:
            (is_valid, channel_name): 유효 여부와 채널명
        """
        file_path = str(Path(file_path).absolute())

        # 각 채널 확인
        for source_path in self.allowed_sources:
            if file_path.startswith(source_path):
                # 채널명 추출
                if 'heptabase' in source_path or 'marginnote' in source_path or 'readwise' in source_path:
                    return True, "channel_a"
                elif 'manual' in source_path:
                    return True, "channel_b"
                elif 'youtube' in source_path:
                    return True, "channel_c"

        logger.warning(f"Invalid source: {file_path}")
        logger.warning(f"File is not from The Big 3 channels. Ignoring.")
        return False, None

    def classify_document(self, content: str, filename: str, file_path: str) -> Dict:
        """
        문서 내용을 분석하여 NotebookLM 4개 카테고리로 분류

        Args:
            content: 문서 내용
            filename: 파일명
            file_path: 파일 전체 경로

        Returns:
            분류 결과 딕셔너리
        """
        # 1. 소스 검증
        if self.config['classification']['validate_source']:
            is_valid, channel = self.validate_source(file_path)
            if not is_valid:
                if self.config['classification']['ignore_unknown_sources']:
                    return {
                        'category': None,
                        'confidence': 0.0,
                        'reasoning': '허용되지 않은 소스. The Big 3 채널이 아님.',
                        'source_channel': None,
                        'ignored': True
                    }

        # 2. 카테고리 분류
        categories = self.config['categories']
        category_desc = "\n".join([f"- {k}: {v}" for k, v in categories.items()])

        # 채널 정보 포함
        channel_info = self._get_channel_info(file_path)

        prompt = f"""다음 문서를 분석하여 NotebookLM의 4개 카테고리 중 가장 적합한 곳으로 분류해주세요.

파일명: {filename}
소스 채널: {channel_info}

문서 내용:
{content[:3000]}

카테고리 (4개만 존재):
{category_desc}

분류 기준:
- work_knowledge: 업무에 즉시 활용 가능한 실무 지식 (코딩, 도구, 프로세스)
- work_advanced: 업무 역량을 향상시키는 심화 학습 (아키텍처, 원리, 전략)
- extended_learning: 간접적으로 업무에 도움되는 교양/인사이트 (심리학, 철학, 경영)
- entertainment: 순수 흥미/재미 위주 콘텐츠 (업무와 무관)

다음 형식으로 응답:
CATEGORY: [카테고리명]
CONFIDENCE: [0.0-1.0 신뢰도]
REASONING: [1-2문장 이유]
"""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = message.content[0].text
            result = self._parse_classification_response(response_text)
            result['source_channel'] = channel_info
            result['ignored'] = False

            logger.info(f"Classified '{filename}' as '{result['category']}' "
                       f"(confidence: {result['confidence']}, channel: {channel_info})")

            return result

        except Exception as e:
            logger.error(f"Classification failed for '{filename}': {e}")
            return {
                'category': 'work_knowledge',  # 기본값
                'confidence': 0.0,
                'reasoning': f'분류 실패: {str(e)}',
                'source_channel': channel_info,
                'ignored': False
            }

    def _get_channel_info(self, file_path: str) -> str:
        """파일 경로에서 채널 정보 추출"""
        file_path = str(Path(file_path).absolute())

        if 'heptabase' in file_path:
            return "Channel A: Heptabase"
        elif 'marginnote' in file_path:
            return "Channel A: Marginnote"
        elif 'readwise' in file_path:
            return "Channel A: Readwise"
        elif 'manual' in file_path:
            return "Channel B: Manual Upload"
        elif 'youtube' in file_path:
            return "Channel C: YouTube"
        else:
            return "Unknown"

    def _parse_classification_response(self, response: str) -> Dict:
        """Claude 응답 파싱"""
        lines = response.strip().split('\n')
        result = {
            'category': 'work_knowledge',
            'confidence': 0.5,
            'reasoning': ''
        }

        for line in lines:
            if line.startswith('CATEGORY:'):
                result['category'] = line.split(':', 1)[1].strip().lower()
            elif line.startswith('CONFIDENCE:'):
                try:
                    result['confidence'] = float(line.split(':', 1)[1].strip())
                except ValueError:
                    result['confidence'] = 0.5
            elif line.startswith('REASONING:'):
                result['reasoning'] = line.split(':', 1)[1].strip()

        return result

    def suggest_filename(self, content: str, original_filename: str, category: str) -> str:
        """
        문서 내용과 카테고리를 기반으로 의미있는 파일명 제안

        Args:
            content: 문서 내용
            original_filename: 원본 파일명
            category: 분류된 카테고리

        Returns:
            제안된 파일명
        """
        prompt = f"""다음 문서의 내용을 보고 명확하고 의미있는 파일명을 제안해주세요.

원본 파일명: {original_filename}
카테고리: {category}

문서 내용:
{content[:1000]}

파일명 규칙:
- 영문 소문자와 하이픈 사용 (예: api-design-patterns.md)
- 카테고리 키워드 포함 권장
- 간결하고 검색 가능하게
- 확장자는 원본과 동일하게 유지

제안된 파일명만 응답해주세요 (설명 없이).
"""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            suggested_name = message.content[0].text.strip()
            logger.info(f"Suggested filename: '{suggested_name}' for '{original_filename}'")

            return suggested_name

        except Exception as e:
            logger.error(f"Filename suggestion failed: {e}")
            return original_filename

    def get_target_path(self, category: str) -> Path:
        """카테고리에 해당하는 목표 경로 반환"""
        targets = self.config['paths']['targets']
        return Path(targets.get(category, targets['work_knowledge']))

    def is_readwise_file(self, file_path: str) -> bool:
        """Readwise 파일인지 확인"""
        return 'readwise' in str(file_path).lower()


def main():
    """테스트용 메인 함수"""
    classifier = PKMSClassifier()

    # 테스트: 소스 검증
    print("\n=== 소스 검증 테스트 ===")
    test_paths = [
        "/Users/arian/GDrive/NotebookLM_Staging/00_INBOX/manual/test.md",
        "/Users/arian/GDrive/NotebookLM_Staging/00_INBOX/heptabase/note.md",
        "/Users/admin/some/random/path/file.md"
    ]

    for path in test_paths:
        is_valid, channel = classifier.validate_source(path)
        print(f"Path: {path}")
        print(f"Valid: {is_valid}, Channel: {channel}\n")

    # 테스트: 문서 분류
    print("\n=== 문서 분류 테스트 ===")
    test_content = """
    # FastAPI REST API 설계 패턴

    ## 개요
    FastAPI를 사용한 프로덕션 레벨 REST API 설계 방법

    ## 주요 내용
    - 의존성 주입 패턴
    - 에러 핸들링 전략
    - 인증/인가 구현
    """

    test_file = "/Users/arian/GDrive/NotebookLM_Staging/00_INBOX/manual/api-guide.md"
    result = classifier.classify_document(test_content, "api-guide.md", test_file)

    print(f"카테고리: {result['category']}")
    print(f"신뢰도: {result['confidence']}")
    print(f"채널: {result['source_channel']}")
    print(f"이유: {result['reasoning']}")


if __name__ == "__main__":
    main()
