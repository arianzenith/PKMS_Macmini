#!/usr/bin/env python3
"""
sync_highlights.py  v2
Readwise → NotebookLM_Staging 동기화 엔진 + Claude AI 인사이트

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[MCP 연동 설계 노트]
Claude Code의 Readwise MCP(mcp__readwise__search_readwise_highlights)는
대화형 Claude 세션 내에서만 동작하는 도구입니다.
이 스크립트는 MCP가 사용하는 동일한 데이터 소스인 Readwise REST API를
직접 호출하여 동일한 하이라이트 데이터를 가져옵니다.

  MCP 도구  ──┐
              ├──→ Readwise 계정 (동일한 데이터)
  REST API ──┘

새 하이라이트를 가져올 때는 REST API가 더 적합합니다.
(날짜 필터, 페이지네이션, 전체 메타데이터 지원)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

사전 준비:
  pip install requests anthropic
  export READWISE_API_TOKEN="your_token"
  export ANTHROPIC_API_KEY="your_key"
  # 토큰 발급: https://readwise.io/access_token

사용법:
  python3 sync_highlights.py                      # 최근 7일, AI 인사이트 포함
  python3 sync_highlights.py --days 30            # 최근 30일
  python3 sync_highlights.py --no-ai              # AI 인사이트 없이 빠른 동기화
  python3 sync_highlights.py --batch-size 3       # 배치당 3개씩 처리 (기본: 5)
  python3 sync_highlights.py --dry-run            # 파일 변경 없이 미리보기
  python3 sync_highlights.py --all                # processed_ids 무시, 전체 재처리

.env 자동 로드 우선순위:
  1. _internal_system/pkms/.env  (BASE_DIR 기준)
  2. 스크립트와 같은 디렉터리의 .env
  3. 환경변수가 이미 설정된 경우 .env 값보다 우선
"""

import os
import sys
import json
import time
import random
import shutil
import argparse
import subprocess
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── SSL 인증서 검증 우회 — [SSL: CERTIFICATE_VERIFY_FAILED] 영구 해결 ─────────
# macOS Python 번들 인증서가 시스템 CA를 찾지 못하는 문제를 방지.
# certifi가 설치된 경우 정식 CA 번들을 사용하고, 없으면 검증을 비활성화한다.
import ssl as _ssl
_ssl._create_default_https_context = _ssl._create_unverified_context  # 기본 우회
try:
    import certifi as _certifi
    _ssl._create_default_https_context = lambda: _ssl.create_default_context(
        cafile=_certifi.where()
    )
except ImportError:
    pass  # certifi 없으면 위의 _create_unverified_context 유지

# ─── .env 자동 로드 ───────────────────────────────────────────────────────────

def load_dotenv(base_dir: Path) -> str | None:
    """
    .env 파일을 찾아 환경변수로 로드.
    이미 설정된 환경변수는 덮어쓰지 않음 (shell export가 우선).

    탐색 순서:
      1. <base_dir>/_internal_system/pkms/.env
      2. 스크립트 파일과 같은 디렉터리의 .env
    """
    candidates = [
        base_dir / "_internal_system" / "pkms" / ".env",
        Path(__file__).parent / ".env",
    ]

    env_path = next((p for p in candidates if p.exists()), None)
    if env_path is None:
        return None

    loaded: list[str] = []
    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")   # 따옴표 제거
            if key and key not in os.environ:  # 기존 환경변수 우선
                os.environ[key] = val
                loaded.append(key)

    return str(env_path)


# anthropic은 선택적 의존성 — 없으면 AI 인사이트 자동 비활성화
try:
    import anthropic as _anthropic_module
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

# pypdf는 선택적 의존성 — 없으면 PDF 파일 건너뜀
try:
    from pypdf import PdfReader as _PdfReader
    PDF_AVAILABLE = True
except ImportError:
    try:
        from PyPDF2 import PdfReader as _PdfReader  # type: ignore
        PDF_AVAILABLE = True
    except ImportError:
        PDF_AVAILABLE = False

# pandas/openpyxl (Excel/CSV) — 선택적 의존성
try:
    import pandas as _pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

# openai-whisper (STT) — 선택적 의존성
try:
    import whisper as _whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# pytesseract + Pillow (이미지 OCR) — 선택적 의존성
try:
    import pytesseract as _pytesseract
    from PIL import Image as _PILImage
    # macOS Homebrew tesseract 경로 명시 (launchd 등 PATH 미설정 환경 대비)
    _TESSERACT_BIN = "/opt/homebrew/bin/tesseract"
    if __import__("os").path.exists(_TESSERACT_BIN):
        _pytesseract.pytesseract.tesseract_cmd = _TESSERACT_BIN
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

# python-docx (DOCX) — 선택적 의존성
try:
    from docx import Document as _DocxDocument
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

# ─── 경로 설정 ────────────────────────────────────────────────────────────────
BASE_DIR          = Path("/Users/arian/GDrive/NotebookLM_Staging")
PROCESSED_IDS_LOG = BASE_DIR / "processed_ids.log"

CATEGORY_DIRS = {
    "01_업무지식": BASE_DIR / "01_업무지식",
    "02_업무심화": BASE_DIR / "02_업무심화",
    "03_확장교양": BASE_DIR / "03_확장교양",
    "04_재미":     BASE_DIR / "04_재미",
}

READWISE_HIGHLIGHTS_URL = "https://readwise.io/api/v2/highlights/"
READWISE_BOOKS_URL      = "https://readwise.io/api/v2/books/"

# Claude 모델
AI_MODEL       = "claude-sonnet-4-6"
AI_MAX_TOKENS  = 2048
BATCH_DELAY_S  = 1.5   # 배치 사이 대기 시간(초) — rate limit 방지

# ─── Zettelkasten 융합 엔진 상수 ─────────────────────────────────────────────
VAULT_FILE     = BASE_DIR / "_internal_system" / "pkms" / "pending_vault.json"
FUSION_TRIGGER = 6     # 고유 출처 수 임계값 — 이 이상이면 융합 인사이트 생성
HEPTABASE_DIR  = BASE_DIR / "00_Raw_Inputs"          # 로컬 입력 폴더 (Heptabase 포함)
ARCHIVE_DIR    = HEPTABASE_DIR / "Archive"            # 처리 완료 파일 보관
FUSION_OUTPUT  = BASE_DIR / "Zettelkasten_Latest.txt" # 항상 덮어쓰기 (고정 파일명)
MAX_HEPTABASE  = 10    # 로컬 파일 최대 스캔 수 (폭발 방지)

# ─── v2.5 이원화 공정 슬롯 상수 ─────────────────────────────────────────────
#
#  [아침 공정 — Time-Triggered] Readwise(외부) 5개 : 이전 제텔카스텐 기록(내부) 5개
#  [업무 공정 — Event-Triggered] 새 업무 파일(Source C) 5개 : Readwise+아카이브 5개
#  총 10개 슬롯. 최신 업무 데이터와 과거 기록/외부 지혜를 반드시 충돌시켜 통찰 도출.
#  업무 파일 부족 시 과거 지혜로 나머지 보충 (역방향 보충도 동일 적용).
#
WORK_SLOTS_MAX         = 5   # [업무 공정] 새 업무 파일(Source C) 최대 슬롯 (50%)
WISDOM_SLOTS_MIN       = 5   # [업무 공정] 과거 지혜 최소 슬롯 (50%)
FUSION_SOURCES_TARGET  = WORK_SLOTS_MAX + WISDOM_SLOTS_MIN  # = 10
# [아침 공정] Readwise : 내부 아카이브 5:5
MORNING_READWISE_SLOTS = 5   # Readwise 하이라이트 슬롯
MORNING_ZETTEL_SLOTS   = 5   # 이전 제텔카스텐 기록 슬롯
CURATION_POOL_SIZE    = 30   # 후보군 크기 (각 풀 내부 정렬용)
DEDUP_THRESHOLD       = 0.80 # Readwise-로컬 간 단어 겹침 80% 이상 = 중복

# 이전 제텔카스텐 파일 (과거 지혜 보조 소스)
ZETTEL_WISDOM_MAX   = 3      # 과거 지혜로 사용할 이전 Zettelkasten.txt 최대 개수
ZETTEL_WISDOM_CHARS = 1500   # 각 파일에서 추출할 최대 글자 수 (앞부분 추출)

# ─── 실시간 파일 감시 ─────────────────────────────────────────────────────────
WATCH_STABLE_SECS  = 5                                   # 파일 안정화 대기 시간(초)
WATCH_EXTENSIONS   = {
    ".pdf", ".txt", ".docx", ".md",           # 텍스트 기반
    ".xlsx", ".csv",                           # Excel/CSV
    ".mp3", ".m4a", ".wav", ".qta",           # 오디오 (iPhone / QuickTime Audio)
    ".jpg", ".jpeg", ".png",                   # 이미지 (OCR)
}
WHISPER_MODEL         = "base"   # STT 모델: tiny/base/small/medium/large (클수록 정확, 느림)
PROCESSED_INPUTS_DIR  = HEPTABASE_DIR / "01_Processed_Inputs"  # 처리 완료 원본 보관 경로

# ─── Google Chat 알림 ─────────────────────────────────────────────────────────
GCHAT_WEBHOOK_URL = (
    "https://chat.googleapis.com/v1/spaces/AAQA4NNvY7s/messages"
    "?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
    "&token=rTFEjZ-m-LUzemZUiwlN0wCkLzPE42d20XseaQ9upr4"
)

# 알림 메시지 내 하이퍼링크 URL (아래 값을 직접 수정하세요)
DRIVE_URL    = "https://drive.google.com/drive/folders/1w78_INIEj6FP1u0a6QJLPaWvpMp494nB?usp=drive_link"
NOTEBOOK_URL = "https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1"

# ─── 분류 키워드 (카테고리별 매칭 가중치) ────────────────────────────────────
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "01_업무지식": [
        "ai", "인공지능", "gpt", "llm", "생성형", "에이전트", "클로드", "챗gpt",
        "머신러닝", "딥러닝", "자동화", "디지털",
        "업무", "생산성", "productivity", "직장", "커리어", "경력", "이직",
        "경영", "마케팅", "스타트업", "비즈니스", "전략", "기업", "조직",
        "경제", "gdp", "성장률", "잠재", "금리", "주식", "투자", "시장",
        "출산", "인구", "저성장", "불경기", "소비", "물가",
        "테슬라", "폭스콘", "삼성", "제조", "공정", "반도체",
    ],
    "02_업무심화": [
        "노트", "기록", "메모", "요약", "정리", "시스템",
        "성장", "역량", "전문성", "코칭", "멘토", "리더십",
        "메커니즘", "원칙", "패러다임", "프레임워크", "구조",
        "혁신", "변화", "미래", "트렌드",
        "사이넥", "드러커", "피터", "칙센트미하이", "베일리", "거인",
        "why", "start with", "스타트위드", "일의격", "일기",
    ],
    "03_확장교양": [
        "역사", "사피엔스", "harari", "진화", "생물", "농업혁명", "문명",
        "고대", "중세", "근대", "전쟁", "제국",
        "사회", "문화", "인문", "철학", "심리", "정신", "감정",
        "중년", "발달", "치유", "심리사회",
        "과학", "물리", "화학", "천문", "우주", "양자", "절기", "달력",
        "정치", "국제", "외교", "민주주의", "선거",
    ],
    "04_재미": [
        "음식", "맛집", "요리", "레시피",
        "여행", "관광", "호텔",
        "칵테일", "술", "와인", "맥주",
        "영화", "드라마", "넷플릭스", "웹툰", "게임",
        "스포츠", "야구", "축구", "농구",
        "패션", "뷰티", "인테리어",
    ],
}

DOC_CATEGORY_BOOST: dict[str, dict[str, int]] = {
    "books":         {"02_업무심화": 3},
    "articles":      {"01_업무지식": 2},
    "supplementals": {"03_확장교양": 2},
    "tweets":        {"04_재미": 1},
    "podcasts":      {"03_확장교양": 1},
}


# ─── 중복 방지 로직 ───────────────────────────────────────────────────────────

def load_processed_ids() -> set[str]:
    """processed_ids.log에서 처리 완료된 highlight_id 목록 로드"""
    if not PROCESSED_IDS_LOG.exists():
        return set()
    with open(PROCESSED_IDS_LOG, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_processed_id(highlight_id: int) -> None:
    """AI 답변까지 완료된 highlight_id를 processed_ids.log에 기록"""
    with open(PROCESSED_IDS_LOG, "a", encoding="utf-8") as f:
        f.write(f"{highlight_id}\n")


# ─── Readwise API 호출 ────────────────────────────────────────────────────────

def fetch_highlights(token: str, updated_after: datetime) -> list[dict]:
    """Readwise REST API에서 하이라이트 목록 가져오기 (페이지네이션 포함)"""
    highlights: list[dict] = []
    headers = {"Authorization": f"Token {token}"}
    params  = {
        "updated__gt": updated_after.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "page_size": 100,
    }

    url: str | None = READWISE_HIGHLIGHTS_URL
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        highlights.extend(data.get("results", []))
        url    = data.get("next")
        params = {}  # next URL에는 이미 모든 파라미터 포함

    return highlights


def fetch_book_info(token: str, book_id: int, cache: dict) -> dict:
    """책/문서 메타데이터 가져오기 (캐시 활용)"""
    if book_id in cache:
        return cache[book_id]
    headers = {"Authorization": f"Token {token}"}
    resp    = requests.get(
        f"{READWISE_BOOKS_URL}{book_id}/", headers=headers, timeout=30
    )
    result        = resp.json() if resp.status_code == 200 else {}
    cache[book_id] = result
    return result


# ─── AI 인사이트 생성 ─────────────────────────────────────────────────────────

AI_PROMPT = """\
아래는 독서 중 하이라이트한 텍스트입니다.

[출처] {title}{author_line}
[본문]
{text}

이 하이라이트에 대해 다음 3가지 질문에 각각 답해주세요.
답변은 한국어로 작성하고, 각 섹션 제목을 그대로 사용해주세요.

────────────────────────────────────────
① 요약
────────────────────────────────────────
핵심 주장/가설을 10줄 이내로 정리하세요.
각 주장마다 근거가 되는 키워드나 예시를 1개씩 괄호 안에 붙여주세요.
예: "저자는 습관이 정체성을 형성한다고 주장한다. (키워드: 정체성 기반 습관)"

────────────────────────────────────────
② 비판적 토론
────────────────────────────────────────
핵심 주장에 대한 반박 3~5개를 제시하고,
찬성(A)과 반대(B)가 토론하는 형식으로 핵심 쟁점을 정리하세요.
마지막에 중립적 결론을 5줄로 작성하세요.

────────────────────────────────────────
③ 실행 아이디어
────────────────────────────────────────
이 내용을 업무/실생활에 적용할 수 있는 새로운 아이디어 7가지를 제안하세요.
각 아이디어마다 아래 형식을 사용하세요:
  [아이디어 N] 제목
  - 적용 시나리오: (한 문장)
  - 기대 효과: (한 문장)
  - 리스크/주의점: (한 문장)
"""


def generate_ai_insights(client, text: str, title: str, author: str) -> str:
    """
    Claude API를 호출해 하이라이트에 대한 3가지 인사이트 생성.
    오류 발생 시 빈 문자열 반환 (해당 하이라이트는 인사이트 없이 저장).
    """
    author_line = f" / {author}" if author else ""
    prompt = AI_PROMPT.format(
        title=title or "Unknown",
        author_line=author_line,
        text=text.strip(),
    )

    try:
        message = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️  AI 오류 (인사이트 생략): {e}")
        return ""


# ─── 카테고리 분류 ────────────────────────────────────────────────────────────

def classify_highlight(highlight: dict, book_info: dict) -> str:
    """키워드 점수 + 문서 유형 가중치로 카테고리 결정. 무점수 시 03_확장교양."""
    text    = (highlight.get("text")    or "").lower()
    title   = (book_info.get("title")   or "").lower()
    author  = (book_info.get("author")  or "").lower()
    doc_cat = (book_info.get("category") or "").lower()
    combined = f"{text} {title} {author}"

    scores: dict[str, int] = {cat: 0 for cat in CATEGORY_DIRS}

    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                scores[cat] += 1

    for boost_cat, boost_val in DOC_CATEGORY_BOOST.get(doc_cat, {}).items():
        scores[boost_cat] += boost_val

    best = max(scores, key=lambda c: scores[c])
    return best if scores[best] > 0 else "03_확장교양"


# ─── 텍스트 포맷 (.txt) ───────────────────────────────────────────────────────

SEP_MAJOR = "=" * 72
SEP_MINOR = "-" * 40


def format_highlight_txt(
    highlight: dict,
    book_info: dict,
    category: str,
    ai_insights: str,
) -> str:
    """하이라이트 본문 + AI 인사이트를 rolling.txt 블록으로 포맷"""
    now_str    = datetime.now().strftime("%Y-%m-%d %H:%M")
    title      = book_info.get("title", "Unknown")
    author     = book_info.get("author", "")
    source_url = book_info.get("source_url", "") or book_info.get("highlights_url", "")
    text       = (highlight.get("text") or "").strip()
    note       = (highlight.get("note") or "").strip()
    h_id       = highlight.get("id", "")
    doc_cat    = book_info.get("category", "")

    lines = [
        "",
        SEP_MAJOR,
        f"[제목]  {title}",
    ]

    meta_parts = []
    if author:
        meta_parts.append(f"저자: {author}")
    if doc_cat:
        meta_parts.append(f"유형: {doc_cat}")
    meta_parts.append(f"분류: {category}")
    meta_parts.append(f"동기화: {now_str}")
    meta_parts.append(f"ID: {h_id}")
    lines.append(f"[정보]  {' | '.join(meta_parts)}")

    if source_url:
        lines.append(f"[원문]  {source_url}")

    lines.append("")
    lines.append(text)

    if note:
        lines.append("")
        lines.append(f"[메모]  {note}")

    if ai_insights:
        lines.append("")
        lines.append(SEP_MINOR + " AI 인사이트 " + SEP_MINOR)
        lines.append(ai_insights)

    lines.append("")
    return "\n".join(lines)


# ─── rolling.txt 관리 ─────────────────────────────────────────────────────────

def append_to_rolling_txt(
    category: str, content: str, dry_run: bool = False
) -> None:
    """해당 카테고리의 rolling.txt 맨 끝에 블록 추가. 없으면 헤더와 함께 생성."""
    rolling_path = CATEGORY_DIRS[category] / "rolling.txt"

    if dry_run:
        print(f"\n  [DRY-RUN] → {rolling_path}")
        preview = "\n".join("    " + ln for ln in content.splitlines()[:20])
        print(preview)
        if content.count("\n") > 20:
            print("    ...")
        return

    if not rolling_path.exists():
        CATEGORY_DIRS[category].mkdir(parents=True, exist_ok=True)
        header = "\n".join([
            f"{category} - Rolling Highlights",
            "",
            "Readwise 자동 동기화 + Claude AI 인사이트",
            f"최초 생성: {datetime.now().strftime('%Y-%m-%d')}",
            f"경로: {rolling_path}",
            "",
        ])
        rolling_path.write_text(header, encoding="utf-8")
        print(f"  📄 rolling.txt 생성: {rolling_path}")

    with open(rolling_path, "a", encoding="utf-8") as f:
        f.write(content)


# ─── 배치 처리 헬퍼 ──────────────────────────────────────────────────────────

def chunked(lst: list, size: int):
    """리스트를 size 단위로 나눠서 yield"""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ─── Vault (누적 버퍼) 관리 ──────────────────────────────────────────────────

def load_vault() -> dict:
    """pending_vault.json 로드. 없으면 빈 vault 반환."""
    if not VAULT_FILE.exists():
        return {"highlights": [], "created_at": datetime.now().isoformat()}
    with open(VAULT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_vault(vault: dict, dry_run: bool = False) -> None:
    """vault를 JSON으로 저장."""
    if dry_run:
        print(f"  [DRY-RUN] vault 저장 건너뜀: {VAULT_FILE}")
        return
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    vault["updated_at"] = datetime.now().isoformat()
    with open(VAULT_FILE, "w", encoding="utf-8") as f:
        json.dump(vault, f, ensure_ascii=False, indent=2)


def clear_vault(dry_run: bool = False) -> None:
    """vault 초기화 (융합 생성 후 다음 사이클 준비)."""
    if dry_run:
        print("  [DRY-RUN] vault 초기화 건너뜀")
        return
    empty = {
        "highlights": [],
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(VAULT_FILE, "w", encoding="utf-8") as f:
        json.dump(empty, f, ensure_ascii=False, indent=2)
    print(f"  🗑️  vault 초기화 완료: {VAULT_FILE}")


def add_to_vault(vault: dict, highlights: list[dict]) -> tuple[int, int]:
    """vault에 하이라이트 추가 (ID 기준 중복 방지). Returns (추가 수, 스킵 수)."""
    existing_ids = {str(h["id"]) for h in vault.get("highlights", [])}
    added, skipped = 0, 0
    for h in highlights:
        h_id = str(h["id"])
        if h_id in existing_ids:
            skipped += 1
        else:
            vault["highlights"].append(h)
            existing_ids.add(h_id)
            added += 1
    return added, skipped


def count_vault_sources(vault: dict) -> int:
    """vault 내 고유 출처(book_id) 수 반환."""
    book_ids = {str(h.get("book_id", "")) for h in vault.get("highlights", [])}
    book_ids.discard("")
    return len(book_ids)


def enrich_vault_titles(vault: dict, token: str, cache: dict) -> None:
    """book_title/book_author 미설정 항목만 API로 보완 (재실행 안전)."""
    for h in vault.get("highlights", []):
        if "book_title" not in h and h.get("book_id"):
            info           = fetch_book_info(token, h["book_id"], cache)
            h["book_title"]  = info.get("title")  or f"Source_{h['book_id']}"
            h["book_author"] = info.get("author") or ""


# ─── 로컬 파일 스캔 및 Archive ───────────────────────────────────────────────

def extract_pdf_text(path: "Path") -> str:
    """PDF에서 텍스트 추출 (최대 20페이지). pypdf/PyPDF2 없으면 빈 문자열 반환."""
    if not PDF_AVAILABLE:
        return ""
    try:
        reader = _PdfReader(str(path))
        pages: list[str] = []
        for page in reader.pages[:20]:
            text = page.extract_text()
            if text:
                pages.append(text.strip())
        return "\n\n".join(pages)
    except Exception as e:
        print(f"  ⚠️  PDF 텍스트 추출 실패 ({path.name}): {e}")
        return ""


# ─── 멀티 포맷 텍스트 추출 ────────────────────────────────────────────────────

# 형식 레이블 (메타데이터 헤더용)
_FORMAT_LABELS: dict[str, str] = {
    ".xlsx": "엑셀", ".csv": "CSV",
    ".mp3": "음성", ".m4a": "음성(아이폰)", ".wav": "음성", ".qta": "음성(QuickTime)",
    ".jpg": "이미지", ".jpeg": "이미지", ".png": "이미지",
    ".pdf": "PDF", ".txt": "텍스트", ".md": "마크다운", ".docx": "문서",
}

# Whisper 모델 캐시 (반복 로딩 방지)
_whisper_model_cache: dict = {}


def _get_whisper_model(size: str = WHISPER_MODEL):
    if size not in _whisper_model_cache:
        print(f"  🎙️  Whisper 모델 로딩 중... (크기: {size})")
        _whisper_model_cache[size] = _whisper.load_model(size)
    return _whisper_model_cache[size]


def extract_docx_text(path: "Path") -> str:
    """DOCX → 텍스트 추출 (python-docx). 미설치 시 RuntimeError."""
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx 미설치 (pip install python-docx)")
    try:
        doc = _DocxDocument(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)
    except Exception as e:
        raise RuntimeError(f"DOCX 파싱 오류: {e}") from e


def extract_excel_text(path: "Path") -> str:
    """Excel(.xlsx) / CSV → 마크다운 테이블 (pandas). 미설치 시 RuntimeError."""
    if not PANDAS_AVAILABLE:
        raise RuntimeError("pandas 미설치 (pip install pandas openpyxl)")
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            # 인코딩 자동 감지 (한국어 CSV 지원)
            df = None
            for enc in ("utf-8-sig", "utf-8", "euc-kr", "cp949"):
                try:
                    df = _pd.read_csv(str(path), encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if df is None:
                raise RuntimeError("CSV 인코딩 감지 실패")
            parts = [f"[CSV 데이터: {path.name}]"]
        else:  # .xlsx
            xls = _pd.ExcelFile(str(path), engine="openpyxl")
            parts = []
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name)
                try:
                    table_text = df.to_markdown(index=False)
                except ImportError:
                    table_text = df.to_string(index=False)
                parts.append(f"[시트: {sheet_name}]\n{table_text}")
            return "\n\n".join(parts)

        # CSV 단일 테이블
        try:
            table_text = df.to_markdown(index=False)
        except ImportError:
            table_text = df.to_string(index=False)
        parts.append(table_text)
        return "\n".join(parts)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Excel/CSV 파싱 오류: {e}") from e


def extract_audio_text(path: "Path") -> str:
    """음성 파일(.mp3/.m4a/.wav) → 텍스트 (Whisper STT). 미설치 시 RuntimeError."""
    if not WHISPER_AVAILABLE:
        raise RuntimeError("openai-whisper 미설치 (pip install openai-whisper)")
    # launchd 환경에서 ffmpeg를 못 찾는 경우를 대비해 PATH에 Homebrew bin 추가
    import os as _os
    _brew_bin = "/opt/homebrew/bin"
    if _brew_bin not in _os.environ.get("PATH", ""):
        _os.environ["PATH"] = _brew_bin + ":" + _os.environ.get("PATH", "")
    try:
        file_mb = path.stat().st_size / (1024 * 1024)
        if file_mb > 50:
            print(f"  🎙️  대용량 음성 파일 ({file_mb:.0f}MB) — 변환에 수 분이 걸릴 수 있습니다...")
        model = _get_whisper_model(WHISPER_MODEL)
        result = model.transcribe(str(path), verbose=False, fp16=False)
        segments = result.get("segments", [])
        if segments:
            return "\n".join(s["text"].strip() for s in segments if s.get("text"))
        return result["text"].strip()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"STT 변환 오류: {e}") from e


def extract_image_text(path: "Path") -> str:
    """이미지(.jpg/.png) → OCR 텍스트 (pytesseract). 미설치 시 RuntimeError."""
    if not TESSERACT_AVAILABLE:
        raise RuntimeError("pytesseract/Pillow 미설치 (pip install pytesseract Pillow)")
    import tempfile
    tmp_path = None
    try:
        img = _PILImage.open(str(path))
        # MPO(아이폰 다중 이미지), RGBA, 팔레트 등 → RGB 정규화 후 PNG 임시 저장
        # (pytesseract는 비표준 JPEG 포맷을 직접 처리 못함)
        if img.format not in (None, "PNG", "TIFF") or img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name
            img.save(tmp_path, "PNG")
            img = _PILImage.open(tmp_path)
        # 한국어+영어 시도 → 언어팩 없으면 영어만
        try:
            text = _pytesseract.image_to_string(img, lang="kor+eng")
        except Exception:
            text = _pytesseract.image_to_string(img, lang="eng")
        return text.strip()
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"OCR 오류: {e}") from e
    finally:
        if tmp_path:
            try:
                __import__("os").unlink(tmp_path)
            except Exception:
                pass


def extract_file_content(path: "Path") -> tuple[str, str, str]:
    """
    파일 형식에 따라 텍스트를 추출하고 메타데이터 헤더를 추가한다.
    [출처: 파일명] [형식: 형식명] [생성일시: YYYY-MM-DD HH:MM]
    Returns: (content_with_header, format_label, error_msg)
      - error_msg가 비어 있으면 성공
      - content가 비어 있으면 내용 없음 (정상 파일)
    """
    suffix = path.suffix.lower()
    fmt = _FORMAT_LABELS.get(suffix, suffix.upper().lstrip("."))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta_header = f"[출처: {path.name}] [형식: {fmt}] [생성일시: {now_str}]"

    raw_text = ""
    error_msg = ""

    try:
        if suffix in {".md", ".txt"}:
            raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
        elif suffix == ".pdf":
            raw_text = extract_pdf_text(path)
        elif suffix == ".docx":
            raw_text = extract_docx_text(path)
        elif suffix in {".xlsx", ".csv"}:
            raw_text = extract_excel_text(path)
        elif suffix in {".mp3", ".m4a", ".wav", ".qta"}:
            raw_text = extract_audio_text(path)
        elif suffix in {".jpg", ".jpeg", ".png"}:
            raw_text = extract_image_text(path)
        else:
            raw_text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as e:
        error_msg = str(e)

    if error_msg:
        content = f"{meta_header}\n⚠️ 추출 오류: {error_msg}"
    elif raw_text:
        content = f"{meta_header}\n\n{raw_text}"
    else:
        content = ""

    return content, fmt, error_msg


def scan_heptabase_files(
    max_files: int = MAX_HEPTABASE,
) -> tuple[list[dict], list[tuple[str, str]]]:
    """
    00_Raw_Inputs/ 폴더를 재귀적으로 스캔하여 로컬 파일 수집.
    지원 포맷: md/txt/pdf/docx/xlsx/csv/mp3/m4a/wav/jpg/png
    - Archive/ 및 01_Processed_Inputs/ 하위폴더 제외
    - 날짜 필터 없음: 처리 완료 시 01_Processed_Inputs/ 이동으로 중복 방지
    - 파일 수 초과 시 최신 수정순으로 max_files개만 처리
    Returns: (valid_files, processing_errors)
      - processing_errors: [(filename, error_msg), ...]
    """
    HEPTABASE_DIR.mkdir(parents=True, exist_ok=True)
    valid: list[dict] = []
    errors: list[tuple[str, str]] = []

    for f in HEPTABASE_DIR.rglob("*"):
        if not f.is_file():
            continue
        if ARCHIVE_DIR in f.parents:
            continue
        if PROCESSED_INPUTS_DIR in f.parents:
            continue
        if f.suffix.lower() not in WATCH_EXTENSIONS:
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime)

        content, fmt, error_msg = extract_file_content(f)

        if error_msg:
            print(f"  ⚠️  [{fmt}] {f.name}: {error_msg}")
            errors.append((f.name, error_msg))
            continue
        if not content:
            continue

        valid.append({
            "path": f,
            "name": f.stem,
            "mtime": mtime,
            "content": content,
            "fmt": fmt,
        })

    valid.sort(key=lambda x: x["mtime"], reverse=True)
    if len(valid) > max_files:
        print(f"  ⚠️  로컬 파일 {len(valid)}개 감지 → 최신 {max_files}개만 처리")
        valid = valid[:max_files]
    return valid, errors


def archive_heptabase_files(files: list[dict], dry_run: bool = False) -> None:
    """처리 완료된 원본 파일을 01_Processed_Inputs/ 폴더로 이동 (중복 분석 방지)."""
    if not files:
        return
    if dry_run:
        print(f"  [DRY-RUN] {len(files)}개 파일 처리완료 이동 건너뜀")
        return
    PROCESSED_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    for f_info in files:
        src = f_info["path"]
        dst = PROCESSED_INPUTS_DIR / src.name
        if dst.exists():
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = PROCESSED_INPUTS_DIR / f"{src.stem}_{ts}{src.suffix}"
        src.rename(dst)
        fmt = f_info.get("fmt", src.suffix.upper().lstrip("."))
        print(f"  📁 처리완료 이동 [{fmt}]: {src.name}")


def scan_zettelkasten_wisdom(
    max_files: int = ZETTEL_WISDOM_MAX,
    max_chars: int = ZETTEL_WISDOM_CHARS,
) -> list[dict]:
    """
    BASE_DIR에 저장된 이전 Zettelkasten.txt 파일을 '과거 지혜' 소스로 로드한다.
    - 오늘 날짜 파일은 제외 (당일 작업 중 중복 방지)
    - 최신 수정순으로 max_files개 선택
    - 각 파일의 앞 max_chars 글자만 추출 (프롬프트 길이 제한)
    Returns: pool-entry 형식의 dict 리스트 (stype="zettelkasten")
    """
    today_prefix = datetime.now().strftime("%y%m%d")
    wisdom: list[dict] = []

    for f in BASE_DIR.glob("*_Zettelkasten*.txt"):
        if f.stem.startswith(today_prefix):
            continue  # 오늘 파일 제외
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            text  = f.read_text(encoding="utf-8", errors="ignore")
            # YAML 프론트매터 스킵 후 실제 내용 추출
            if text.startswith("---"):
                end = text.find("---", 3)
                text = text[end + 3:].strip() if end > 0 else text
            snippet = text[:max_chars].strip()
            if not snippet:
                continue
            wisdom.append({
                "stype": "zettelkasten",
                "title": f.stem,
                "mtime": mtime,
                "item":  {"name": f.stem, "content": snippet, "path": f, "fmt": "제텔카스텐"},
            })
        except Exception:
            continue

    wisdom.sort(key=lambda x: x["mtime"], reverse=True)
    return wisdom[:max_files]


def cleanup_empty_subdirs(base_dir: "Path", *exclude_dirs: "Path") -> int:
    """
    base_dir 내 빈 하위 디렉토리를 제거 (exclude_dirs 및 그 하위 제외).
    깊은 경로부터 처리하여 연쇄 삭제 지원. 삭제된 디렉토리 수 반환.
    """
    removed = 0
    exclude_set = set(exclude_dirs)
    subdirs = sorted(
        (d for d in base_dir.rglob("*") if d.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in subdirs:
        if d == base_dir or d in exclude_set:
            continue
        if any(exc in d.parents for exc in exclude_set):
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except Exception:
            pass
    return removed


def text_overlap(a: str, b: str) -> float:
    """
    두 텍스트의 단어 단위 겹침 비율 (Overlap Coefficient).
    짧은 쪽 단어 집합 기준으로 계산 → 한 텍스트가 다른 텍스트에 포함된 경우도 검출.
    """
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    smaller = min(len(words_a), len(words_b))
    return intersection / smaller


def deduplicate_pool(pool: list[dict]) -> tuple[list[dict], int]:
    """
    pool 내 Readwise-로컬 간 텍스트 중복 감지 및 제거.
    - 겹침 >= DEDUP_THRESHOLD(80%) → 중복 판정
    - 중복 시 Readwise 항목 제거, 로컬(Heptabase) 항목 유지
    - 사용자의 주관적 메모가 담긴 로컬 데이터를 우선시
    Returns: (deduped_pool, removed_count)
    """
    # 로컬 파일 텍스트 사전 수집
    local_texts: list[str] = [
        p["item"].get("content", "").strip()
        for p in pool if p["stype"] == "local"
    ]

    to_remove: set[int] = set()
    for i, p in enumerate(pool):
        if p["stype"] != "readwise":
            continue
        rw_text = (p["item"].get("text") or "").strip()
        if not rw_text:
            continue
        for loc_text in local_texts:
            if loc_text and text_overlap(rw_text, loc_text) >= DEDUP_THRESHOLD:
                to_remove.add(i)
                break

    deduped = [p for i, p in enumerate(pool) if i not in to_remove]
    return deduped, len(to_remove)


def curate_sources(
    local_files: list[dict],
    vault: dict,
    pinned_path: "Path | None" = None,
    zettelkasten_wisdom: "list[dict] | None" = None,
    pipeline_mode: str = "work",
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    v2.5 이원화 공정 큐레이션.

    [업무 공정 — pipeline_mode='work']
      Source C(새 업무 파일) 5개 : Readwise + 이전 제텔카스텐 5개 = 총 10개
      - pinned_path 지정 시 해당 파일을 슬롯 #1에 강제 고정 (실시간 트리거)

    [아침 공정 — pipeline_mode='morning']
      Source C(로컬 파일) 완전 배제. Readwise 5개 : 이전 제텔카스텐 기록 5개 = 총 10개
      - 영감/철학적 관점 배합. 업무 파일 없이 외부 지식 vs 내부 아카이브 충돌.

    Returns: (sel_local, sel_readwise, sel_zettelkasten)
    """
    # ─── 아침 공정: 로컬 파일 완전 배제 ─────────────────────────────────────
    if pipeline_mode == "morning":
        # Readwise 풀 구성
        readwise_pool: list[dict] = []
        for h in vault.get("highlights", []):
            ts_str = h.get("updated") or h.get("highlighted_at") or ""
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                ts = datetime.min
            readwise_pool.append({
                "stype": "readwise",
                "title": h.get("book_title") or "?",
                "mtime": ts,
                "item":  h,
            })
        readwise_pool.sort(key=lambda x: x["mtime"], reverse=True)

        # 이전 제텔카스텐 풀 구성
        zettel_pool: list[dict] = list(zettelkasten_wisdom or [])
        zettel_pool.sort(key=lambda x: x["mtime"], reverse=True)

        # 5:5 배분 (부족 시 상대방으로 보충)
        n_rw    = min(MORNING_READWISE_SLOTS, len(readwise_pool))
        n_zettel = min(MORNING_ZETTEL_SLOTS, len(zettel_pool))
        total_morning = MORNING_READWISE_SLOTS + MORNING_ZETTEL_SLOTS
        # 한쪽 부족 시 반대쪽으로 보충
        if n_rw < MORNING_READWISE_SLOTS:
            n_zettel = min(total_morning - n_rw, len(zettel_pool))
        elif n_zettel < MORNING_ZETTEL_SLOTS:
            n_rw = min(total_morning - n_zettel, len(readwise_pool))

        # Readwise: 최신 절반 고정 + 나머지 무작위
        rw_candidates = readwise_pool[:CURATION_POOL_SIZE]
        n_rw_fixed = max(1, n_rw // 2)
        n_rw_rand  = n_rw - n_rw_fixed
        rw_fixed   = rw_candidates[:n_rw_fixed]
        rw_rand    = random.sample(rw_candidates[n_rw_fixed:], min(n_rw_rand, len(rw_candidates[n_rw_fixed:])))
        sel_rw_entries   = rw_fixed + rw_rand
        sel_zettel_entries = zettel_pool[:n_zettel]

        print(f"\n🌅 아침 공정 5:5 믹스: Readwise {len(sel_rw_entries)}개 (50%) + 이전 기록 {len(sel_zettel_entries)}개 (50%)")
        print(f"   ├ [Readwise {len(sel_rw_entries)}개]")
        for s in sel_rw_entries:
            print(f"   │  📚 {s['title'][:50]} ({s['mtime'].strftime('%m-%d %H:%M')})")
        print(f"   └ [이전 제텔카스텐 {len(sel_zettel_entries)}개]")
        for s in sel_zettel_entries:
            print(f"      📜 {s['title'][:50]} ({s['mtime'].strftime('%m-%d %H:%M')})")

        return (
            [],  # sel_local — 아침 공정에서 로컬 파일 없음
            [s["item"] for s in sel_rw_entries],
            [s["item"] for s in sel_zettel_entries],
        )

    # ─── 업무 공정: Source C + Readwise/아카이브 ──────────────────────────────
    work_pool:   list[dict] = []
    wisdom_pool: list[dict] = []

    for f in local_files:
        work_pool.append({
            "stype": "local",
            "title": f["name"],
            "mtime": f["mtime"],
            "item":  f,
        })

    for h in vault.get("highlights", []):
        ts_str = h.get("updated") or h.get("highlighted_at") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            ts = datetime.min
        wisdom_pool.append({
            "stype": "readwise",
            "title": h.get("book_title") or "?",
            "mtime": ts,
            "item":  h,
        })

    for z in (zettelkasten_wisdom or []):
        wisdom_pool.append(z)

    # ── 최신순 정렬
    work_pool.sort(key=lambda x: x["mtime"], reverse=True)
    wisdom_pool.sort(key=lambda x: x["mtime"], reverse=True)

    # ── 로컬-Readwise 중복 제거 (Readwise 제거, 로컬 우선)
    all_pool = work_pool + wisdom_pool
    all_pool, dedup_removed = deduplicate_pool(all_pool)
    if dedup_removed:
        print(f"\n🧹 중복 데이터 {dedup_removed}개를 발견하여 로컬 우선으로 정리했습니다")
    # 중복 제거 후 풀 재분리
    work_pool   = [p for p in all_pool if p["stype"] == "local"]
    wisdom_pool = [p for p in all_pool if p["stype"] in ("readwise", "zettelkasten")]

    # ── 실시간 모드: 트리거 파일을 슬롯 #1에 강제 고정
    pinned_entry = None
    if pinned_path is not None:
        for entry in work_pool:
            if entry["stype"] == "local" and entry["item"]["path"] == pinned_path:
                pinned_entry = entry
                work_pool.remove(entry)
                print(f"\n📌 실시간 핀: {pinned_entry['title'][:50]} → 슬롯 #1 고정")
                break

    # ── 5:5 슬롯 배분
    n_work_avail   = len(work_pool) + (1 if pinned_entry else 0)
    n_wisdom_avail = len(wisdom_pool)

    n_work   = min(n_work_avail, WORK_SLOTS_MAX)
    n_wisdom = FUSION_SOURCES_TARGET - n_work
    if n_wisdom > n_wisdom_avail:
        extra   = n_wisdom - n_wisdom_avail
        n_work  = min(n_work + extra, n_work_avail)
        n_wisdom = FUSION_SOURCES_TARGET - n_work
    if n_wisdom_avail > 0:
        n_work   = min(n_work, FUSION_SOURCES_TARGET - WISDOM_SLOTS_MIN)
        n_wisdom = FUSION_SOURCES_TARGET - n_work

    # ── 업무 파일 선택 (핀 → 최신순)
    if pinned_entry:
        work_selected = [pinned_entry] + work_pool[:n_work - 1]
    else:
        work_selected = work_pool[:n_work]

    # ── 과거 지혜 선택 (최신 절반 고정 + 나머지 무작위)
    wisdom_candidates = wisdom_pool[:CURATION_POOL_SIZE]
    n_fixed  = max(1, n_wisdom // 2)
    n_rand   = n_wisdom - n_fixed
    fixed_w  = wisdom_candidates[:n_fixed]
    rand_w   = random.sample(wisdom_candidates[n_fixed:], min(n_rand, len(wisdom_candidates[n_fixed:])))
    wisdom_selected = fixed_w + rand_w

    selected = work_selected + wisdom_selected

    # ── 큐레이션 결과 출력
    print(f"\n🎯 업무 공정 5:5 믹스: 업무/로컬 {len(work_selected)}개 (50%) + 과거 지혜 {len(wisdom_selected)}개 (50%)")
    print(f"   ├ [업무 파일 {len(work_selected)}개]")
    for s in work_selected:
        fmt_tag = f"[{s['item'].get('fmt','?')}]" if s["stype"] == "local" else ""
        print(f"   │  🏷️ {fmt_tag} {s['title'][:48]} ({s['mtime'].strftime('%m-%d %H:%M')})")
    print(f"   └ [과거 지혜 {len(wisdom_selected)}개]")
    for s in wisdom_selected:
        tag = "📚 RW" if s["stype"] == "readwise" else "📜 ZK"
        print(f"      {tag} {s['title'][:48]} ({s['mtime'].strftime('%m-%d %H:%M')})")

    sel_local  = [s["item"] for s in selected if s["stype"] == "local"]
    sel_rw     = [s["item"] for s in selected if s["stype"] == "readwise"]
    sel_zettel = [s["item"] for s in selected if s["stype"] == "zettelkasten"]
    return sel_local, sel_rw, sel_zettel


# ─── Zettelkasten 융합 프롬프트 & 생성 ───────────────────────────────────────

# [업무 공정 프롬프트] Event-Triggered: 새 업무 파일 + Readwise/아카이브 충돌
FUSION_PROMPT_WORK = """\
당신은 냉철한 전략 컨설턴트이자 비판적 철학자입니다.
아래 소스들을 분석해 3개의 섹션을 한국어로 작성하세요.

━━━━━━━━━━━━━━━━━━━━━━━━
🧠 [오늘의 업무 인풋] — 로컬 노트/메모 ({heptabase_count}개)
━━━━━━━━━━━━━━━━━━━━━━━━
{heptabase_block}

━━━━━━━━━━━━━━━━━━━━━━━━
📚 [외부 지식] — Readwise 하이라이트 ({readwise_count}개, {source_count}개 출처)
━━━━━━━━━━━━━━━━━━━━━━━━
{readwise_block}
{zettelkasten_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[철칙 — 이 5가지를 위반한 리포트는 처음부터 다시 작성한다]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 중립성 절대 금지: "양쪽 모두 일리가 있다", "상황에 따라 다르다"는 문장은
   금지어다. 반드시 하나의 우월한 논리를 선택하거나, 두 주장의 모순을 극한으로
   몰아붙여 어느 쪽도 아닌 날카로운 제3의 대안을 선언하라.

2. 비판의 칼날: "일부 한계가 있다", "재고해볼 필요가 있다"는 표현은 쓰레기통에
   버려라. "이 주장은 [구체 상황]에서 시대착오적 허구이며 실행 불가능하다"처럼
   사용자가 기분이 나쁘더라도 반박할 수 없는 팩트를 들이밀어라.

3. 즉각 실행 수준: 액션 플랜에 "노력하라", "고려하라", "시도해보라"는 금지.
   "내일 오전 9시에 [무엇]을 [몇 분] 동안 하라"는 수준으로 구체화하라.

4. 모순 우선 충돌: 동의하는 소스끼리 묶는 것은 지적 비겁이다.
   가장 모순되고 이질적인 소스 쌍을 1순위로 골라 충돌시켜라.

5. 난이도·파급력 명시: 아이디어 7개 각각에 [난이도: 상/중/하]와 [파급력: 상/중/하]를
   반드시 명시하라. 파급력이 낮은 아이디어는 처음부터 제외하라.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

════════════════════════════════════════
① 거대 가설 요약 (Cross-Source Synthesis)
════════════════════════════════════════
모든 소스를 관통하는 하나의 "거대 가설"을 10줄 이내로 도출하세요.
- 가장 모순되는 소스 쌍을 골라 그 충돌을 가설의 핵심으로 삼으세요
- "상황에 따라 다르다"는 결론 금지. 하나의 단호한 주장으로 끝내세요
- 마지막 문장은 "지금 당장 믿어야 할 한 줄: ..."로 반드시 끝내세요

════════════════════════════════════════
② 사용자 vs 외부 지식: 비판적 대화
════════════════════════════════════════
사용자의 생각을 법정의 피고인으로 세우고, 외부 지식이 검사·변호인 역할을 맡습니다.

### 🔴 검사 측 논고
사용자 생각의 핵심 전제가 왜 틀렸는지를,
"이 전제는 [구체 이유]로 시대착오적 허구다"라는 문장 수준으로 공격하세요.

### 🟢 변호인 측 변론
"이 부분은 [출처]가 증명한다"는 형식으로 지지하세요.

### ⚡ 판결 (중립 아닌 제3의 대안)
검사도 변호인도 아닌, 두 충돌에서 탄생하는 새로운 명제를 한 문장으로 선언하세요.
"두 관점 모두 일리가 있다"는 판결은 기각입니다.

════════════════════════════════════════
③ 창발적 실무 아이디어 7개 (Emergent Practical Ideas)
════════════════════════════════════════
동의하는 소스끼리 묶지 마세요. 가장 이질적인 소스 충돌에서만 아이디어를 뽑으세요.
파급력이 낮은 아이디어는 처음부터 제외하세요.

각 아이디어마다 아래 형식을 정확히 따르세요:

  [아이디어 N] 제목 (동사로 시작하는 명령형)
  - 난이도: 상/중/하 | 파급력: 상/중/하
  - 충돌 소스: (소스A × 소스B — 이 둘은 반드시 모순돼야 한다)
  - 불편한 진실: (이 충돌에서 도출되는 반박 불가 팩트 한 문장)
  - 즉각 실행: "내일 [오전/오후] [시각]에 [구체적 행동]을 [N분] 동안 하라"
  - 하지 않으면: (실행 안 할 경우의 구체적 손실 한 문장)
"""

# [아침 공정 프롬프트] Time-Triggered: Readwise + 내부 아카이브 철학적 융합
FUSION_PROMPT_MORNING = """\
당신은 지식의 연금술사이자 철학적 통찰을 탐구하는 사상가입니다.
아래 외부 지식(Readwise)과 내부 기록(이전 제텔카스텐 아카이브)을 융합하여
3개의 섹션을 한국어로 작성하세요. 오늘 하루를 시작하는 공장장님에게
영감과 철학적 질문을 던지는 아침 보고서입니다.

━━━━━━━━━━━━━━━━━━━━━━━━
📚 [외부 지식] — Readwise 하이라이트 ({readwise_count}개, {source_count}개 출처)
━━━━━━━━━━━━━━━━━━━━━━━━
{readwise_block}
{zettelkasten_section}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[철칙 — 이 5가지를 위반한 아침 리포트는 처음부터 다시 작성한다]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. 영감의 날카로움: "흥미롭다", "시사점이 있다"는 금지. 반드시 "이것은 [구체 맥락]에서
   [기존 전제]를 뒤집는다"는 수준으로 충격을 주어라.

2. 연결의 도약: 표면적으로 관련 없어 보이는 두 소스를 반드시 하나의 통찰로 연결하라.
   "이 두 아이디어는 같은 방향이다"는 최악의 결과다. 모순적 연결만 가치 있다.

3. 철학적 깊이: 단순 요약이 아닌, 소스들이 함께 제기하는 더 깊은 질문을 드러내라.
   "이것은 결국 [근본 질문]으로 귀결된다"는 형식으로 심화하라.

4. 아침의 방향성: 오늘 하루 공장장님의 생각을 어떻게 바꾸거나 확장할지
   구체적인 생각 실험(Thought Experiment)을 반드시 하나 제시하라.

5. 모순 발굴: 내부 기록과 외부 지식 사이의 모순이나 역설을 찾아내라.
   모순이 없으면 더 깊이 파고들어라 — 표면적 동의는 항상 숨겨진 긴장을 덮는다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

════════════════════════════════════════
① 아침의 거대 통찰 (Morning Synthesis)
════════════════════════════════════════
외부 지식과 내부 기록을 관통하는 하나의 "아침 통찰"을 10줄 이내로 도출하세요.
- 가장 이질적인 소스 쌍을 골라 그 충돌을 통찰의 핵심으로 삼으세요
- "상황에 따라 다르다"는 결론 금지. 하나의 단호한 명제로 끝내세요
- 마지막 문장은 "오늘 내가 믿어야 할 한 줄: ..."로 반드시 끝내세요

════════════════════════════════════════
② 외부 지식 vs 내부 기록: 철학적 대화
════════════════════════════════════════
외부 지식(Readwise)과 내부 기록(이전 제텔카스텐)을 철학적 토론의 두 목소리로 세웁니다.

### 🌅 외부의 목소리 (Readwise)
Readwise의 핵심 주장을 "세계는 [이렇다]고 말한다"는 형식으로 선언하세요.
그것이 내포하는 가장 불편한 함의를 드러내세요.

### 🔮 내부의 목소리 (과거 기록)
이전 제텔카스텐 기록의 핵심 패턴을 "나는 [이렇게] 생각해왔다"는 형식으로 요약하세요.
그것의 맹점이나 미완성된 부분을 솔직하게 드러내세요.

### ⚡ 오늘의 새로운 명제
두 목소리가 충돌한 결과 탄생하는 새로운 명제를 한 문장으로 선언하세요.
"둘 다 맞다"거나 "균형이 필요하다"는 결론은 기각입니다.

════════════════════════════════════════
③ 아침의 영감 아이디어 7개 (Morning Inspirations)
════════════════════════════════════════
동의하는 소스끼리 묶지 마세요. 가장 이질적인 소스 충돌에서만 영감을 뽑으세요.
오늘 하루 생각의 방향을 바꿀 수 있는 아이디어만 포함하세요.

각 아이디어마다 아래 형식을 정확히 따르세요:

  [영감 N] 제목 (동사로 시작하는 탐구형)
  - 깊이: 피상적/보통/심층 | 전복성: 낮음/보통/높음
  - 충돌 소스: (소스A × 소스B — 이 둘은 반드시 이질적이어야 한다)
  - 철학적 역설: (이 충돌에서 드러나는 모순 또는 역설 한 문장)
  - 생각 실험: "오늘 하루 [구체적 상황]에서 [이렇게] 다르게 생각해보라"
  - 연결 고리: (이 영감이 이전 제텔카스텐 기록의 어떤 생각과 연결되는지)
"""

FUSION_SEP = "=" * 72


def _build_readwise_block(highlights: list[dict]) -> tuple[str, int, int]:
    """Readwise 하이라이트 리스트 → 프롬프트 블록. Returns (블록, 하이라이트수, 출처수)."""
    seen_sources: set[str] = set()
    lines: list[str] = []
    for h in highlights:
        title  = h.get("book_title") or f"Source_{h.get('book_id', '?')}"
        author = h.get("book_author", "")
        text   = (h.get("text") or "").strip()
        if not text:
            continue
        label = f"{title} / {author}" if author else title
        seen_sources.add(title)
        lines.append(f"[출처: {label}]\n{text}")
    return "\n\n".join(lines), len(highlights), len(seen_sources)


def _build_heptabase_block(heptabase_files: list[dict]) -> str:
    """Heptabase/로컬 파일 목록 → 프롬프트 블록. fmt 레이블 포함."""
    lines: list[str] = []
    for f_info in heptabase_files:
        fmt = f_info.get("fmt", "")
        label = f"{f_info['name']} [{fmt}]" if fmt else f_info['name']
        lines.append(f"[업무 인풋: {label}]\n{f_info['content']}")
    return "\n\n".join(lines)


def _build_zettelkasten_block(zettelkasten_items: list[dict]) -> str:
    """이전 Zettelkasten 파일 → 과거 지혜 프롬프트 블록."""
    lines: list[str] = []
    for item in zettelkasten_items:
        lines.append(f"[이전 기록: {item['name']}]\n{item['content']}")
    return "\n\n".join(lines)


def _generate_fusion(
    client,
    local_files: list[dict],
    readwise_items: list[dict],
    zettelkasten_items: "list[dict] | None" = None,
    pipeline_mode: str = "work",
) -> str:
    """Claude API로 Zettelkasten 융합 인사이트 생성 (큐레이션된 소스 사용).

    pipeline_mode='work'   → FUSION_PROMPT_WORK  (업무 전략/실행 톤)
    pipeline_mode='morning' → FUSION_PROMPT_MORNING (철학적 영감 톤, 로컬 파일 없음)
    """
    readwise_block, rw_cnt, s_cnt = _build_readwise_block(readwise_items)

    # 이전 제텔카스텐 과거 지혜 블록 (있을 때만 포함)
    zettel_items = zettelkasten_items or []
    if zettel_items:
        zk_block = _build_zettelkasten_block(zettel_items)
        section_label = "🌅 [내부 아카이브]" if pipeline_mode == "morning" else "📜 [과거의 지혜]"
        zettelkasten_section = (
            f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{section_label} — 이전 제텔카스텐 기록 ({len(zettel_items)}개)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{zk_block}"
        )
    else:
        zettelkasten_section = ""

    if pipeline_mode == "morning":
        # 아침 공정: 로컬 파일 없음 — Readwise + 내부 아카이브만
        if not readwise_block.strip() and not zettelkasten_section:
            print("  ⚠️  처리할 내용이 없습니다.")
            return ""
        prompt = FUSION_PROMPT_MORNING.format(
            readwise_count=rw_cnt,
            source_count=s_cnt,
            readwise_block=readwise_block or "(Readwise 하이라이트 없음)",
            zettelkasten_section=zettelkasten_section,
        )
    else:
        # 업무 공정: 로컬 파일 + Readwise + 아카이브
        heptabase_block = _build_heptabase_block(local_files)
        if not readwise_block.strip() and not heptabase_block.strip() and not zettelkasten_section:
            print("  ⚠️  처리할 내용이 없습니다.")
            return ""
        prompt = FUSION_PROMPT_WORK.format(
            heptabase_count=len(local_files),
            heptabase_block=heptabase_block or "(로컬 노트 없음)",
            readwise_count=rw_cnt,
            source_count=s_cnt,
            readwise_block=readwise_block or "(Readwise 하이라이트 없음)",
            zettelkasten_section=zettelkasten_section,
        )

    try:
        msg = client.messages.create(
            model=AI_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  AI 오류: {e}")
        return ""


def _summarize_for_notification(client, fusion_text: str, pipeline_mode: str = "work") -> str:
    """
    융합 텍스트에서 ①②③ 섹션을 추출해 10가지 포인트 불렛 요약을 반환한다.
    1~9번은 구조화된 분석, 10번(딥 퀘스천)은 pipeline_mode에 따라 다르게 생성.
    - work mode:    실행 지향 Q10 (오늘 즉시 실행 가능한 날카로운 행동 질문)
    - morning mode: 통찰 지향 Q10 (오늘 하루 생각의 방향을 바꾸는 철학적 질문)
    API 오류 시 빈 문자열 반환 — 알림은 요약 없이 발송.
    """
    if pipeline_mode == "morning":
        q10_instruction = (
            "• 10. 🔟 *(딥 퀘스천):* 1~9번의 통찰과 철학적 역설을 모두 종합하여,\n"
            "  공장장님이 오늘 하루 내내 머릿속에 품고 다녀야 할 가장 깊은 철학적 질문을\n"
            "  한 문장으로 생성하라. 오늘 외부 지식과 내부 기록의 충돌 지점에서 탄생한\n"
            "  질문이어야 하며, 즉각적 답이 없는 열린 탐구 질문으로, 반드시 물음표(?)로 끝나야 한다.\n"
        )
    else:
        q10_instruction = (
            "• 10. 🔟 *(딥 퀘스천):* 1~9번의 분석과 실행 아이템을 모두 종합하여,\n"
            "  공장장님이 오늘 즉시 스스로에게 던져야 할 가장 날카로운 핵심 질문을\n"
            "  한 문장으로 생성하라. 오늘 소스들의 충돌 지점과 실행 맥락을 정확히\n"
            "  반영해야 하며, 반드시 물음표(?)로 끝나는 완전한 질문이어야 한다.\n"
        )

    prompt = (
        "아래는 Zettelkasten 융합 인사이트 리포트입니다.\n"
        "반드시 아래 형식을 엄격히 준수하여 **정확히 10가지 포인트**를 작성하세요.\n\n"
        "[Q1. 맥락 분석과 생각] — 반드시 3가지 포인트\n"
        "• 1. 오늘 정보의 핵심 사실 #1 (원문의 핵심 의도가 담긴 깊이 있는 문장)\n"
        "• 2. 오늘 정보의 핵심 사실 #2\n"
        "• 3. 이 정보가 독자의 비즈니스·삶에 미칠 영향에 대한 검증 가능한 가설 "
        "(반드시 '~라는 가설을 세워볼 수 있습니다' 또는 '~할 가능성이 큽니다' 형식으로 끝낼 것)\n\n"
        "[Q2. 관점의 충돌과 대조] — 반드시 3가지 포인트\n"
        "• 4. 오늘 수집 정보들 사이의 핵심 모순 또는 긴장 관계\n"
        "• 5. 기존 통념 또는 사용자 기존 생각과의 시각 차이\n"
        "• 6. 위 두 충돌이 동시에 성립할 경우 발생하는 더 깊은 문제 또는 역설\n\n"
        "[Q3. 실행적 통찰] — 반드시 3가지 포인트\n"
        "• 7. 가설을 검증하기 위해 오늘 바로 실행할 행동 (구체적 시간·방법 포함)\n"
        "• 8. 충돌을 해결하기 위한 실험 또는 관찰 행동\n"
        "• 9. 단기적으로 피해야 할 함정 또는 오류\n\n"
        "[Q4. 딥 퀘스천] — 반드시 1가지 포인트\n"
        f"{q10_instruction}\n"
        "출력 규칙 (엄격히 준수):\n"
        "- 소제목([Q1...], [Q2...], [Q3...], [Q4...])과 번호 붙은 불렛만 출력.\n"
        "- 1~6번: 핵심만 담은 1~2문장, 포인트당 **최대 120자**.\n"
        "- 7~9번: 1문장, 포인트당 **최대 80자**.\n"
        "- 10번: **최대 200자**, 반드시 물음표(?)로 끝나야 한다. 절대 생략 금지.\n"
        "- 전체 출력이 **2,000자를 넘지 않도록** 조절하되, 초과 시 7~9번을 먼저 단축.\n"
        "- 절대 10가지 미만으로 줄이거나 항목을 합치지 마세요.\n\n"
        f"{fusion_text}"
    )
    try:
        resp = client.messages.create(
            model=AI_MODEL,
            max_tokens=1400,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  요약 생성 오류 (무시): {e}")
        return ""


def _send_gchat_notification(
    filename: str,
    source_count: int,
    sources_brief: str,
    summary: str,
    urgent: bool = False,
    processing_errors: "list[tuple[str, str]] | None" = None,
) -> None:
    """
    Google Chat 웹훅으로 융합 완료 알림을 발송한다.
    네트워크 오류 시 조용히 무시 — 로컬 저장에 영향 없음.
    processing_errors: [(filename, error_msg), ...] — 에러 발생 파일 목록
    """
    today = datetime.now().strftime("%Y-%m-%d")
    summary_block = summary if summary else "• (요약 생성 불가 — 원문을 직접 확인하세요)"
    title_line = (
        f"🚨 *[실시간] 공장장님, 긴급 생각 배달입니다! '제텔카스텐'의 새로운 조각이 도착했습니다.* ({today})"
        if urgent else
        f"📦 *공장장님, 오늘 이 생각은 어떠세요? '제텔카스텐'의 새로운 조각이 도착했습니다.* ({today})"
    )

    # 에러 섹션 (에러 있을 때만 추가)
    error_section = ""
    if processing_errors:
        error_lines = "\n".join(
            f"  • {fname}: {emsg[:60]}{'…' if len(emsg) > 60 else ''}"
            for fname, emsg in processing_errors
        )
        error_section = f"\n\n⚠️ *처리 오류 ({len(processing_errors)}개):*\n{error_lines}"

    text = (
        f"{title_line}\n\n"
        f"📚 *오늘의 출처:*\n{sources_brief}\n\n"
        f"💡 *오늘의 생각 (10가지 포인트):*\n\n"
        f"{summary_block}"
        f"{error_section}\n\n"
        f"🔗 *바로가기:*\n"
        f"1. <{NOTEBOOK_URL}|🧠 제텔카스텐 전략실 바로가기>\n"
        f"2. <{DRIVE_URL}|📄 오늘자 원문 (Google Drive)>"
    )
    try:
        resp = requests.post(
            GCHAT_WEBHOOK_URL,
            json={"text": text},
            timeout=8,
        )
        if resp.status_code == 200:
            print("  💬 Google Chat 알림 발송 완료")
        else:
            print(f"  ⚠️  Google Chat 알림 실패 (HTTP {resp.status_code})")
    except Exception as e:
        print(f"  ⚠️  Google Chat 알림 오류 (무시): {e}")


def _save_fusion_output(
    local_files: list[dict],
    readwise_items: list[dict],
    fusion_text: str,
    client=None,
    dry_run: bool = False,
    realtime_mode: bool = False,
    processing_errors: "list[tuple[str, str]] | None" = None,
) -> "Path | None":
    """
    Zettelkasten 융합 결과를 YYMMDD_Zettelkasten.txt로 저장.
    동시에 Archive/[날짜_시간_Fusion.txt]에 백업 보관.
    """
    rw_sources = sorted({
        h.get("book_title") or f"Source_{h.get('book_id', '?')}"
        for h in readwise_items
    })
    rw_lines   = "\n".join(f"  • [Readwise]   {s}" for s in rw_sources)
    hept_lines = "\n".join(f"  • [로컬]       {f['name']}" for f in local_files)

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")
    date_iso = now.strftime("%Y-%m-%d")

    # ── YAML 프론트매터 (DEVONthink 4 / NotebookLM 메타데이터)
    if readwise_items and local_files:
        sources_str = "Readwise + Heptabase"
    elif readwise_items:
        sources_str = "Readwise"
    else:
        sources_str = "Heptabase/PDF"

    yaml_front = "\n".join([
        "---",
        f"Date: {date_iso}",
        f"Source: {sources_str}",
        "Tags: [Zettelkasten, Insight, Automation]",
        "Status: Processed",
        "---",
        "",
    ])

    content = yaml_front + "\n".join([
        FUSION_SEP,
        "  📦 공장장님, 오늘 이 생각은 어떠세요? '제텔카스텐'의 새로운 조각이 도착했습니다.",
        f"  생성일: {now_str}  |  v2.4 Fusion Engine  |  5:5 균형 큐레이션",
        f"  큐레이션: 로컬/업무 {len(local_files)}개 (50%) + 과거지혜 {len(rw_sources)}개 출처 (50%)",
        FUSION_SEP,
        "",
        "[ 융합된 소스 목록 ]",
        rw_lines,
        hept_lines,
        "",
        FUSION_SEP,
        "",
        fusion_text,
        "",
        FUSION_SEP,
    ])

    # ── 날짜 기반 파일명 생성 (YYMMDD_Zettelkasten.txt, 중복 시 순번)
    date_str  = datetime.now().strftime("%y%m%d")
    base_name = f"{date_str}_Zettelkasten.txt"
    out_path  = BASE_DIR / base_name
    seq = 2
    while out_path.exists():
        out_path = BASE_DIR / f"{date_str}_Zettelkasten_{seq}.txt"
        seq += 1

    if dry_run:
        print(f"\n  [DRY-RUN] 저장 예정: {out_path}")
        for ln in content.splitlines()[:25]:
            print(f"    {ln}")
        print("    ...")
        return None

    # ── 날짜별 파일 저장 (NotebookLM 개별 소스 관리용)
    out_path.write_text(content, encoding="utf-8")

    # ── Google Chat 알림: 10줄 요약 생성 후 발송
    print("  📝 알림용 요약 생성 중...")
    summary = _summarize_for_notification(client, fusion_text) if client else ""

    # 출처 목록 — [플랫폼] 제목 형식, 스니펫(→ 이후) 완전 제거, 30자 절삭
    _TITLE_MAX = 30
    def _clean_title(t: str) -> str:
        # Heptabase 파일명: "책제목 → 하이라이트내용" → 책제목만 추출
        base = t.split(" → ")[0].split(" →")[0].strip()
        return base[:_TITLE_MAX] + "…" if len(base) > _TITLE_MAX else base
    sources_brief_parts = []
    seen_titles: set[str] = set()
    for s in rw_sources:
        title = _clean_title(s)
        if title not in seen_titles:
            sources_brief_parts.append(f"• [Readwise] {title}")
            seen_titles.add(title)
    for f in local_files:
        title = _clean_title(f["name"])
        if title not in seen_titles:
            sources_brief_parts.append(f"• [로컬] {title}")
            seen_titles.add(title)
    sources_brief = "\n".join(sources_brief_parts)

    total_sources = len(local_files) + len(readwise_items)
    _send_gchat_notification(
        out_path.name, total_sources, sources_brief, summary,
        urgent=realtime_mode,
        processing_errors=processing_errors or [],
    )

    # ── Archive 백업 (날짜_시간_Fusion.txt)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts_backup   = datetime.now().strftime("%Y%m%d_%H%M")
    backup_path = ARCHIVE_DIR / f"{ts_backup}_Fusion.txt"
    backup_path.write_text(content, encoding="utf-8")
    print(f"  📦 Archive 백업: {backup_path.name}")

    return out_path


# ─── 융합 엔진 진입점 ─────────────────────────────────────────────────────────

def run_fusion_engine(
    all_highlights: list[dict],
    rw_token: str,
    ai_client,
    dry_run: bool = False,
    force: bool = False,
    pinned_path: "Path | None" = None,
    realtime_mode: bool = False,
) -> None:
    """
    Fusion Insight Engine v2.4
    ① Omni-Source: 로컬 파일(md/txt/pdf/xlsx/mp3/m4a/wav/qta/jpg/png) + Readwise vault
    ② 5:5 균형 큐레이션: 업무/로컬 50%(최대 5개) + 과거 지혜 50%(최소 5개) = 총 10개
    ③ Anti-Neutrality 프롬프트 + 유동적 Q10 → YYMMDD_Zettelkasten.txt 저장
    ④ Archive 백업 + 사용된 로컬 파일 → 01_Processed_Inputs 이동 + 빈 서브폴더 정리
    """
    print("\n" + "━" * 60)
    print("  🧠 Fusion Insight Engine v2.4")
    print("━" * 60)

    # ── ① 로컬 파일 스캔 (00_Raw_Inputs/ 전체, Archive/Processed 제외)
    print("\n📂 로컬 파일 스캔 중... (00_Raw_Inputs/ — md/txt/pdf/docx/xlsx/csv/mp3/m4a/wav/qta/jpg/png)")
    local_files, scan_errors = scan_heptabase_files()
    if local_files:
        for f_info in local_files:
            fmt = f_info.get("fmt", f_info['path'].suffix.upper().lstrip("."))
            print(f"   • [{fmt}] {f_info['name']} ({f_info['mtime'].strftime('%m-%d %H:%M')})")
    else:
        print("   → 처리할 파일 없음 (Archive/Processed 제외)")
    if scan_errors:
        print(f"   ⚠️  처리 오류 {len(scan_errors)}개 (알림에 포함됩니다)")

    # ── ② Readwise vault 로드 및 업데이트
    vault    = load_vault()
    in_vault = len(vault.get("highlights", []))
    print(f"\n📦 Readwise vault: {in_vault}개 하이라이트 | {count_vault_sources(vault)}개 출처")

    added, skipped = add_to_vault(vault, all_highlights)
    print(f"   → 추가: {added}개 | 중복 스킵: {skipped}개")

    if added > 0:
        print("📚 출처 메타데이터 조회 중...")
        book_cache: dict = {}
        enrich_vault_titles(vault, rw_token, book_cache)

    # ── vault 저장 (항상)
    save_vault(vault, dry_run=dry_run)

    # ── 소스 풀 확인
    total_pool = len(local_files) + len(vault.get("highlights", []))
    print(f"\n📊 전체 풀: 로컬 {len(local_files)}개 + Readwise {len(vault.get('highlights',[]))}개 = {total_pool}개")

    if total_pool < FUSION_SOURCES_TARGET and not force:
        remaining = FUSION_SOURCES_TARGET - total_pool
        print(f"\n⏳ 지식 숙성 중... 소스 {remaining}개 더 필요 ({total_pool}/{FUSION_SOURCES_TARGET})")
        print("━" * 60)
        return

    # ── AI 클라이언트 확인
    if ai_client is None:
        print("\n⚠️  AI 클라이언트 없음 — 융합 생성 불가")
        return

    # ── ③ 과거 제텔카스텐 지혜 소스 로드
    zettelkasten_wisdom = scan_zettelkasten_wisdom()
    if zettelkasten_wisdom:
        print(f"\n📜 과거 지혜 로드: 이전 제텔카스텐 {len(zettelkasten_wisdom)}개")
        for z in zettelkasten_wisdom:
            print(f"   • {z['title']} ({z['mtime'].strftime('%m-%d')})")

    # ── ④ 5:5 균형 큐레이션 (v2.4 Balanced Insight Rule)
    sel_local, sel_rw, sel_zettel = curate_sources(
        local_files, vault,
        pinned_path=pinned_path,
        zettelkasten_wisdom=zettelkasten_wisdom,
    )
    n_local  = len(sel_local)
    n_rw     = len(sel_rw)
    n_zettel = len(sel_zettel)

    print(f"\n🔗 업무 {n_local}개 + Readwise {n_rw}개 + 이전기록 {n_zettel}개 → 융합 생성 중...")
    print(f"🤖 AI 분석 중... (모델: {AI_MODEL})")
    fusion_text = _generate_fusion(ai_client, sel_local, sel_rw, sel_zettel)
    if not fusion_text:
        print("❌ AI 인사이트 생성 실패 — vault 및 파일 유지됩니다.")
        return

    # ── Zettelkasten_Latest.txt 덮어쓰기 + Archive 백업
    out_path = _save_fusion_output(
        sel_local, sel_rw, fusion_text,
        client=ai_client, dry_run=dry_run, realtime_mode=realtime_mode,
        processing_errors=scan_errors,
    )
    if out_path:
        print(f"\n✅ 융합 인사이트 저장: {out_path}")

    # ── ④ 사용된 로컬 파일 → Archive/ 이동
    archive_heptabase_files(sel_local, dry_run=dry_run)

    # ── 빈 서브폴더 정리 (rm -rf 대신 안전한 rmdir 체인)
    if not dry_run:
        removed = cleanup_empty_subdirs(HEPTABASE_DIR, ARCHIVE_DIR, PROCESSED_INPUTS_DIR)
        if removed:
            print(f"  🧹 빈 서브폴더 {removed}개 정리 완료")

    # ── vault 초기화 (다음 사이클 준비)
    clear_vault(dry_run=dry_run)

    print("\n" + "━" * 60)
    print("  Fusion Insight Engine v2.4 완료")
    print(f"  5:5 균형: 업무/로컬 {n_local}개 (50%) + 과거지혜 {n_rw + n_zettel}개 (50%)")
    if out_path:
        print(f"  출력: {out_path.name}")
    print("━" * 60)
    print()
    print("  v2.4 제텔카스텐 엔진 가동 준비 완료")
    print("  공장장님, 오늘 이 생각은 어떠세요?")
    print("  '제텔카스텐'의 새로운 조각이 도착했습니다.")
    print("━" * 60)


# ─── 실시간 파일 감시 모드 ────────────────────────────────────────────────────

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler as _FSEHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False

if WATCHDOG_AVAILABLE:
    import threading

    class _FusionTriggerHandler(_FSEHandler):
        """
        00_Raw_Inputs/ 를 감시하여 새 파일 감지 시 융합 엔진을 가동한다.
        - WATCH_STABLE_SECS(5초) 동안 파일 크기가 변하지 않으면 '안정화 완료'로 판단
        - 동일 파일의 중복 트리거 방지
        """

        def __init__(self, rw_token: str, ai_client, all_highlights: list[dict]) -> None:
            super().__init__()
            self.rw_token       = rw_token
            self.ai_client      = ai_client
            self.all_highlights = all_highlights
            self._pending: dict[str, float] = {}   # path → first-seen timestamp
            self._done:    set[str]         = set() # 이미 처리한 경로
            self._lock = threading.Lock()
            self._start_stability_thread()

        def _start_stability_thread(self) -> None:
            t = threading.Thread(target=self._stability_loop, daemon=True)
            t.start()

        def _stability_loop(self) -> None:
            """1초마다 pending 파일을 확인해 안정화된 것을 처리한다."""
            import time as _time
            while True:
                _time.sleep(1)
                now = _time.time()
                with self._lock:
                    ready = [
                        p for p, ts in list(self._pending.items())
                        if now - ts >= WATCH_STABLE_SECS
                    ]
                for path_str in ready:
                    with self._lock:
                        self._pending.pop(path_str, None)
                    self._trigger(Path(path_str))

        def _is_target(self, path_str: str) -> bool:
            p = Path(path_str)
            return (
                p.suffix.lower() in WATCH_EXTENSIONS
                and ARCHIVE_DIR not in p.parents
                and PROCESSED_INPUTS_DIR not in p.parents
                and not p.name.startswith(".")
            )

        def _register(self, path_str: str) -> None:
            import time as _time
            if path_str in self._done:
                return
            if not self._is_target(path_str):
                return
            with self._lock:
                self._pending[path_str] = _time.time()
            print(f"\n👁  감지: {Path(path_str).name}  →  {WATCH_STABLE_SECS}초 후 분석 시작...")

        def on_created(self, event) -> None:
            if not event.is_directory:
                self._register(event.src_path)

        def on_moved(self, event) -> None:
            """Google Drive 동기화는 .tmp → 실제파일 rename 패턴을 사용한다."""
            if not event.is_directory:
                self._register(event.dest_path)

        def on_modified(self, event) -> None:
            """
            파일 수정 이벤트:
            - 이미 pending 중이면 타이머 리셋 (안정화 대기 연장)
            - 미등록 파일이면 신규 등록 (macOS Finder 복사는 create+modify 패턴)
            """
            if event.is_directory:
                return
            import time as _time
            with self._lock:
                if event.src_path in self._pending:
                    self._pending[event.src_path] = _time.time()
                    return
            # pending에 없는 파일 → 신규 등록
            self._register(event.src_path)

        def _trigger(self, path: Path) -> None:
            if str(path) in self._done:
                return
            self._done.add(str(path))

            print("\n" + "━" * 60)
            print(f"  🚨 실시간 긴급 브리핑 트리거: {path.name}")
            print("━" * 60)

            run_fusion_engine(
                all_highlights=self.all_highlights,
                rw_token=self.rw_token,
                ai_client=self.ai_client,
                force=True,
                pinned_path=path,
                realtime_mode=True,
            )


def run_watch_mode(rw_token: str, ai_client, all_highlights: list[dict]) -> None:
    """
    00_Raw_Inputs/ 를 실시간 감시한다.
    Ctrl+C 로 종료.
    """
    if not WATCHDOG_AVAILABLE:
        print("❌ watchdog 패키지가 없습니다. 'pip install watchdog' 후 재시도하세요.")
        sys.exit(1)

    watch_dir = str(HEPTABASE_DIR)
    handler   = _FusionTriggerHandler(rw_token, ai_client, all_highlights)
    observer  = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()

    print("\n" + "━" * 60)
    print("  👁  실시간 파일 감시 모드 활성화")
    print(f"  📂 감시 폴더: {watch_dir}")
    print(f"  📎 대상 확장자: {', '.join(sorted(WATCH_EXTENSIONS))}")
    print(f"  ⏱  안정화 대기: {WATCH_STABLE_SECS}초")
    print("  ⌨️  종료: Ctrl+C")
    print("━" * 60 + "\n")

    try:
        import time as _time
        while True:
            _time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n\n👋 파일 감시 종료.")
    observer.join()


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Readwise → NotebookLM_Staging 동기화 엔진 v2 (AI 인사이트 포함)"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="최근 N일 하이라이트 동기화 (기본: 7)"
    )
    parser.add_argument(
        "--no-ai", dest="no_ai", action="store_true",
        help="AI 인사이트 생성 건너뛰기 (빠른 동기화)"
    )
    parser.add_argument(
        "--batch-size", dest="batch_size", type=int, default=5,
        help="AI API 배치당 처리 개수 (기본: 5, rate limit 방지)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="파일 변경 없이 결과 미리보기"
    )
    parser.add_argument(
        "--all", dest="force_all", action="store_true",
        help="processed_ids 무시하고 전체 재처리"
    )
    parser.add_argument(
        "--fusion", action="store_true",
        help="Zettelkasten 융합 엔진 함께 실행 (vault 누적 → 6개 출처 도달 시 융합)"
    )
    parser.add_argument(
        "--force-fusion", dest="force_fusion", action="store_true",
        help="출처 수 미달해도 강제 융합 인사이트 생성 (--fusion 자동 포함)"
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="00_Raw_Inputs/ 실시간 감시 모드 — 새 파일 감지 시 즉시 긴급 브리핑 생성"
    )
    args = parser.parse_args()

    # --force-fusion은 --fusion도 활성화
    if args.force_fusion:
        args.fusion = True

    print("━" * 60)
    print("  Readwise → NotebookLM_Staging 동기화 엔진 v2")
    print(f"  기준 경로: {BASE_DIR}")
    print("━" * 60)

    # ── .env 자동 로드
    env_path = load_dotenv(BASE_DIR)
    if env_path:
        print(f"\n📂 .env 로드: {env_path}")
    else:
        print("\n⚠️  .env 파일 없음 — 환경변수 직접 설정 필요")

    # ── Readwise 토큰 확인
    rw_token = os.getenv("READWISE_API_TOKEN", "").strip()
    if not rw_token:
        print("\n❌ READWISE_API_TOKEN 환경변수가 없습니다.")
        print("   토큰 발급: https://readwise.io/access_token")
        print("   설정: export READWISE_API_TOKEN='your_token'")
        sys.exit(1)

    # ── Anthropic 클라이언트 초기화
    ai_client = None
    if not args.no_ai:
        if not ANTHROPIC_AVAILABLE:
            print("\n⚠️  anthropic 패키지 없음 → AI 인사이트 비활성화")
            print("   설치: pip install anthropic")
        else:
            anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            if anthropic_key:
                ai_client = _anthropic_module.Anthropic(api_key=anthropic_key)
                print(f"\n🤖 AI 인사이트: 활성화 (모델: {AI_MODEL}, 배치: {args.batch_size}개)")
            else:
                print("\n⚠️  ANTHROPIC_API_KEY 없음 → AI 인사이트 비활성화")
                print("   설정: export ANTHROPIC_API_KEY='your_key'")
    else:
        print("\n⏩ AI 인사이트: 비활성화 (--no-ai)")

    # ── --watch 모드: 여기서 분기하여 감시 루프 진입 (return 없음 — 블로킹)
    if args.watch:
        # 감시 모드는 Readwise 동기화 없이 바로 파일 감시 시작
        # (all_highlights는 빈 리스트 — 실시간 트리거 시 vault에서 로드)
        run_watch_mode(rw_token, ai_client, all_highlights=[])
        return

    # ── processed_ids 로드
    processed_ids = load_processed_ids()
    print(f"\n📋 기존 처리 완료: {len(processed_ids)}개")
    if args.force_all:
        print("   ⚠️  --all: 중복 체크 무시하고 전체 재처리")

    # ── Readwise 하이라이트 가져오기
    updated_after = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    print(f"\n🔄 Readwise API 호출 중... (최근 {args.days}일 / {updated_after.strftime('%Y-%m-%d')} 이후)")

    try:
        all_highlights = fetch_highlights(rw_token, updated_after)
    except requests.HTTPError as e:
        print(f"\n❌ Readwise API 오류: {e}")
        sys.exit(1)
    except requests.ConnectionError:
        print("\n❌ 네트워크 연결 실패.")
        sys.exit(1)

    print(f"📥 가져온 하이라이트: {len(all_highlights)}개")

    # ── 신규 항목만 필터링
    new_highlights = []
    skip_count = 0
    for hl in all_highlights:
        h_id = str(hl.get("id", ""))
        if not args.force_all and h_id in processed_ids:
            skip_count += 1
        else:
            new_highlights.append(hl)

    print(f"   → 신규: {len(new_highlights)}개 | 중복 스킵: {skip_count}개")

    if not new_highlights:
        print("\n✅ 새로운 하이라이트가 없습니다.")
        print("━" * 60)
        # --fusion이면 vault 누적 단계는 여전히 실행 (기존 vault 확인 목적)
        if args.fusion:
            run_fusion_engine(
                all_highlights=all_highlights,
                rw_token=rw_token,
                ai_client=ai_client,
                dry_run=args.dry_run,
                force=args.force_fusion,
            )
        return

    # ── 배치 처리
    book_cache:     dict[int, dict] = {}
    new_count       = 0
    ai_count        = 0
    category_count: dict[str, int] = {cat: 0 for cat in CATEGORY_DIRS}
    total_batches   = (len(new_highlights) + args.batch_size - 1) // args.batch_size

    for batch_idx, batch in enumerate(chunked(new_highlights, args.batch_size), 1):
        if total_batches > 1:
            print(f"\n  [배치 {batch_idx}/{total_batches}] {len(batch)}개 처리 중...")

        for hl in batch:
            h_id      = str(hl.get("id", ""))
            book_id   = hl.get("book_id")
            book_info = fetch_book_info(rw_token, book_id, book_cache) if book_id else {}
            category  = classify_highlight(hl, book_info)
            title_short = (book_info.get("title") or "?")[:40]

            print(f"  ✅ [{category}] {title_short}", end="", flush=True)

            # AI 인사이트 생성
            ai_insights = ""
            if ai_client is not None:
                print(" → AI...", end="", flush=True)
                ai_insights = generate_ai_insights(
                    ai_client,
                    text=hl.get("text", ""),
                    title=book_info.get("title", ""),
                    author=book_info.get("author", ""),
                )
                if ai_insights:
                    ai_count += 1
                    print(" ✓", end="")
            print()

            # rolling.txt에 추가
            txt_block = format_highlight_txt(hl, book_info, category, ai_insights)
            append_to_rolling_txt(category, txt_block, dry_run=args.dry_run)

            # processed_ids 기록 (AI 답변까지 완료된 상태로 기록)
            if not args.dry_run:
                save_processed_id(int(h_id))
                processed_ids.add(h_id)

            category_count[category] += 1
            new_count += 1

        # 배치 간 대기 (마지막 배치는 제외)
        if ai_client is not None and batch_idx < total_batches:
            print(f"  ⏳ 다음 배치까지 {BATCH_DELAY_S}초 대기...")
            time.sleep(BATCH_DELAY_S)

    # ── 결과 요약
    dry_label = "[DRY-RUN] " if args.dry_run else ""
    print(f"\n{'━' * 60}")
    print(f"  {dry_label}동기화 완료")
    ai_label = f" | AI 인사이트: {ai_count}개" if ai_client is not None else ""
    print(f"  새로 추가: {new_count}개 | 중복 스킵: {skip_count}개{ai_label}")
    if new_count > 0:
        print()
        for cat, cnt in category_count.items():
            if cnt > 0:
                rolling_path = CATEGORY_DIRS[cat] / "rolling.txt"
                print(f"  {cat}: {cnt}개  →  {rolling_path}")
    print(f"\n  processed_ids.log: {PROCESSED_IDS_LOG}")
    print("━" * 60)

    # ── Zettelkasten 융합 엔진 (--fusion 플래그 시 실행)
    if args.fusion:
        run_fusion_engine(
            all_highlights=all_highlights,
            rw_token=rw_token,
            ai_client=ai_client,
            dry_run=args.dry_run,
            force=args.force_fusion,
        )


if __name__ == "__main__":
    main()
