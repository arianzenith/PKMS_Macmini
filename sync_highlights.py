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
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

# ─── 5:5 큐레이션 상수 ───────────────────────────────────────────────────────
CURATION_POOL_SIZE    = 30   # 후보군 크기
CURATION_FIXED        = 3    # 최신성 기준 고정 선별 수
CURATION_RANDOM       = 3    # 무작위(의외성) 선별 수
FUSION_SOURCES_TARGET = CURATION_FIXED + CURATION_RANDOM  # = 6

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


def scan_heptabase_files(max_files: int = MAX_HEPTABASE) -> list[dict]:
    """
    00_Raw_Inputs/ 폴더를 재귀적으로 스캔하여 로컬 파일 수집.
    지원 포맷: .md, .txt, .pdf (텍스트 추출 시도)
    - Archive/ 하위폴더 제외
    - 날짜 필터 없음: Archive 이동으로 중복 방지
    - 파일 수 초과 시 최신 수정순으로 max_files개만 처리
    """
    HEPTABASE_DIR.mkdir(parents=True, exist_ok=True)
    valid: list[dict] = []
    supported = {".md", ".txt", ".pdf"}

    for f in HEPTABASE_DIR.rglob("*"):
        if not f.is_file():
            continue
        if ARCHIVE_DIR in f.parents:
            continue
        if f.suffix.lower() not in supported:
            continue
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        try:
            if f.suffix.lower() == ".pdf":
                content = extract_pdf_text(f)
            else:
                content = f.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            continue
        if not content:
            continue
        valid.append({"path": f, "name": f.stem, "mtime": mtime, "content": content})

    valid.sort(key=lambda x: x["mtime"], reverse=True)
    if len(valid) > max_files:
        print(f"  ⚠️  로컬 파일 {len(valid)}개 감지 → 최신 {max_files}개만 처리")
        valid = valid[:max_files]
    return valid


def archive_heptabase_files(files: list[dict], dry_run: bool = False) -> None:
    """처리 완료된 Heptabase .md/.txt 파일을 Archive/ 폴더로 이동."""
    if not files:
        return
    if dry_run:
        print(f"  [DRY-RUN] {len(files)}개 파일 Archive 이동 건너뜀")
        return
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    for f_info in files:
        src = f_info["path"]
        dst = ARCHIVE_DIR / src.name
        if dst.exists():
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = ARCHIVE_DIR / f"{src.stem}_{ts}{src.suffix}"
        src.rename(dst)
        print(f"  📁 Archive 이동: {src.name}")


def cleanup_empty_subdirs(base_dir: "Path", exclude_dir: "Path") -> int:
    """
    base_dir 내 빈 하위 디렉토리를 제거 (exclude_dir 및 그 하위 제외).
    깊은 경로부터 처리하여 연쇄 삭제 지원. 삭제된 디렉토리 수 반환.
    """
    removed = 0
    # 깊은 경로 우선 정렬
    subdirs = sorted(
        (d for d in base_dir.rglob("*") if d.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in subdirs:
        if d == base_dir or d == exclude_dir:
            continue
        if exclude_dir in d.parents:
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed += 1
        except Exception:
            pass
    return removed


def curate_sources(
    local_files: list[dict],
    vault: dict,
) -> tuple[list[dict], list[dict]]:
    """
    5:5 전략적 큐레이션.
    전체 소스(로컬 파일 + Readwise vault)에서 후보군 CURATION_POOL_SIZE개를 선정,
    최신 CURATION_FIXED개 고정 + 무작위 CURATION_RANDOM개 선별.
    Returns: (selected_local_files, selected_readwise_highlights)
    """
    pool: list[dict] = []

    for f in local_files:
        pool.append({
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
        pool.append({
            "stype": "readwise",
            "title": h.get("book_title") or "?",
            "mtime": ts,
            "item":  h,
        })

    # 최신순 정렬 → 후보군 30개
    pool.sort(key=lambda x: x["mtime"], reverse=True)
    candidates = pool[:CURATION_POOL_SIZE]

    fixed_slots  = candidates[:CURATION_FIXED]
    remain_slots = candidates[CURATION_FIXED:]
    random_slots = random.sample(remain_slots, min(CURATION_RANDOM, len(remain_slots)))

    selected = fixed_slots + random_slots

    print(f"\n🎯 5:5 큐레이션: 전체 {len(pool)}개 → 후보 {len(candidates)}개")
    print(f"   ├ [최신 {len(fixed_slots)}개 고정]")
    for s in fixed_slots:
        tag = "🏷️ 로컬" if s["stype"] == "local" else "📚 RW"
        print(f"   │  {tag} {s['title'][:50]} ({s['mtime'].strftime('%m-%d %H:%M')})")
    print(f"   └ [무작위 {len(random_slots)}개 선별]")
    for s in random_slots:
        tag = "🏷️ 로컬" if s["stype"] == "local" else "📚 RW"
        pool_rank = next((i + 1 for i, p in enumerate(pool) if p["item"] is s["item"]), "?")
        print(f"      {tag} {s['title'][:50]} (풀 #{pool_rank})")

    sel_local = [s["item"] for s in selected if s["stype"] == "local"]
    sel_rw    = [s["item"] for s in selected if s["stype"] == "readwise"]
    return sel_local, sel_rw


# ─── Zettelkasten 융합 프롬프트 & 생성 ───────────────────────────────────────

FUSION_PROMPT = """\
당신은 냉철한 전략 컨설턴트이자 비판적 철학자입니다.
아래 두 유형의 소스를 분석해 3개의 섹션을 한국어로 작성하세요.

━━━━━━━━━━━━━━━━━━━━━━━━
🧠 [사용자의 생각] — 로컬 노트/메모 ({heptabase_count}개)
━━━━━━━━━━━━━━━━━━━━━━━━
{heptabase_block}

━━━━━━━━━━━━━━━━━━━━━━━━
📚 [외부 지식] — Readwise 하이라이트 ({readwise_count}개, {source_count}개 출처)
━━━━━━━━━━━━━━━━━━━━━━━━
{readwise_block}

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
    """Heptabase 파일 목록 → 프롬프트 블록."""
    lines: list[str] = []
    for f_info in heptabase_files:
        lines.append(f"[메모: {f_info['name']}]\n{f_info['content']}")
    return "\n\n".join(lines)


def _generate_fusion(
    client,
    local_files: list[dict],
    readwise_items: list[dict],
) -> str:
    """Claude API로 Zettelkasten 융합 인사이트 생성 (큐레이션된 소스 사용)."""
    readwise_block, rw_cnt, s_cnt = _build_readwise_block(readwise_items)
    heptabase_block = _build_heptabase_block(local_files)

    if not readwise_block.strip() and not heptabase_block.strip():
        print("  ⚠️  처리할 내용이 없습니다.")
        return ""

    prompt = FUSION_PROMPT.format(
        heptabase_count=len(local_files),
        heptabase_block=heptabase_block or "(로컬 노트 없음)",
        readwise_count=rw_cnt,
        source_count=s_cnt,
        readwise_block=readwise_block or "(Readwise 하이라이트 없음)",
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


def _save_fusion_output(
    local_files: list[dict],
    readwise_items: list[dict],
    fusion_text: str,
    dry_run: bool = False,
) -> "Path | None":
    """
    Zettelkasten 융합 결과를 Zettelkasten_Latest.txt에 덮어쓰기.
    동시에 Archive/[날짜_시간_Fusion.txt]에 백업 보관.
    """
    rw_sources = sorted({
        h.get("book_title") or f"Source_{h.get('book_id', '?')}"
        for h in readwise_items
    })
    rw_lines   = "\n".join(f"  • [Readwise]   {s}" for s in rw_sources)
    hept_lines = "\n".join(f"  • [로컬]       {f['name']}" for f in local_files)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    content = "\n".join([
        FUSION_SEP,
        "  Zettelkasten 융합 인사이트 (Latest)",
        f"  생성일: {now_str}",
        f"  큐레이션: 로컬 {len(local_files)}개 + Readwise {len(rw_sources)}개 출처",
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

    if dry_run:
        print(f"\n  [DRY-RUN] 저장 예정: {FUSION_OUTPUT}")
        for ln in content.splitlines()[:25]:
            print(f"    {ln}")
        print("    ...")
        return None

    # ── 메인 출력 (NotebookLM 새로고침용, 고정 파일명)
    FUSION_OUTPUT.write_text(content, encoding="utf-8")

    # ── Archive 백업 (날짜_시간_Fusion.txt)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts_backup   = datetime.now().strftime("%Y%m%d_%H%M")
    backup_path = ARCHIVE_DIR / f"{ts_backup}_Fusion.txt"
    backup_path.write_text(content, encoding="utf-8")
    print(f"  📦 Archive 백업: {backup_path.name}")

    return FUSION_OUTPUT


# ─── 융합 엔진 진입점 ─────────────────────────────────────────────────────────

def run_fusion_engine(
    all_highlights: list[dict],
    rw_token: str,
    ai_client,
    dry_run: bool = False,
    force: bool = False,
) -> None:
    """
    Fusion Insight Engine v2.2
    ① Omni-Source: 로컬 파일(md/txt/pdf) + Readwise vault
    ② 5:5 큐레이션: 후보 30개 → 최신 3개 고정 + 무작위 3개 = 6개 선별
    ③ Anti-Neutrality 프롬프트 → Zettelkasten_Latest.txt 덮어쓰기
    ④ Archive 백업 + 사용된 로컬 파일 이동 + 빈 서브폴더 정리
    """
    print("\n" + "━" * 60)
    print("  🧠 Fusion Insight Engine v2.2")
    print("━" * 60)

    # ── ① 로컬 파일 스캔 (00_Raw_Inputs/ 전체, Archive 제외)
    print("\n📂 로컬 파일 스캔 중... (00_Raw_Inputs/ — md/txt/pdf)")
    local_files = scan_heptabase_files()
    if local_files:
        for f_info in local_files:
            fmt = f_info['path'].suffix.upper()[1:]
            print(f"   • [{fmt}] {f_info['name']} ({f_info['mtime'].strftime('%m-%d %H:%M')})")
    else:
        print("   → 처리할 파일 없음 (Archive 제외)")

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

    # ── ③ 5:5 전략적 큐레이션
    sel_local, sel_rw = curate_sources(local_files, vault)
    n_local = len(sel_local)
    n_rw    = len(sel_rw)

    print(f"\n🔗 로컬 {n_local}개 + Readwise {n_rw}개 → 융합 생성 중...")
    print(f"🤖 AI 분석 중... (모델: {AI_MODEL})")
    fusion_text = _generate_fusion(ai_client, sel_local, sel_rw)
    if not fusion_text:
        print("❌ AI 인사이트 생성 실패 — vault 및 파일 유지됩니다.")
        return

    # ── Zettelkasten_Latest.txt 덮어쓰기 + Archive 백업
    out_path = _save_fusion_output(sel_local, sel_rw, fusion_text, dry_run=dry_run)
    if out_path:
        print(f"\n✅ 융합 인사이트 저장: {out_path}")

    # ── ④ 사용된 로컬 파일 → Archive/ 이동
    archive_heptabase_files(sel_local, dry_run=dry_run)

    # ── 빈 서브폴더 정리 (rm -rf 대신 안전한 rmdir 체인)
    if not dry_run:
        removed = cleanup_empty_subdirs(HEPTABASE_DIR, ARCHIVE_DIR)
        if removed:
            print(f"  🧹 빈 서브폴더 {removed}개 정리 완료")

    # ── vault 초기화 (다음 사이클 준비)
    clear_vault(dry_run=dry_run)

    print("\n" + "━" * 60)
    print("  Fusion Insight Engine v2.2 완료")
    print(f"  큐레이션: 로컬 {n_local}개 + Readwise {n_rw}개 융합")
    if out_path:
        print(f"  출력: {out_path}")
    print("━" * 60)


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
