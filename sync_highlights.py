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
import time
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
    args = parser.parse_args()

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


if __name__ == "__main__":
    main()
