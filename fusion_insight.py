#!/usr/bin/env python3
"""
fusion_insight.py  v1
Zettelkasten 융합 인사이트 엔진

여러 Readwise 하이라이트가 6개 이상의 고유 출처에서 누적되면
Claude AI가 교차 출처 Zettelkasten 융합 인사이트를 생성합니다.

작동 방식:
  1. Readwise API에서 최신 하이라이트 가져오기
  2. pending_vault.json에 누적 (ID 기준 중복 방지)
  3. 고유 출처 수 >= TRIGGER_COUNT(6)이면 융합 인사이트 생성
  4. 출력: 00_통합인사이트/Zettelkasten_Fusion_YYYYMMDD_HHMM.txt
  5. 생성 후 vault 초기화 (다음 사이클 시작)

사용법:
  python3 fusion_insight.py                   # 최근 7일 하이라이트 처리
  python3 fusion_insight.py --days 1          # 최근 1일 (cron용)
  python3 fusion_insight.py --dry-run         # 파일 변경 없이 미리보기
  python3 fusion_insight.py --force           # 출처 수 미달해도 강제 생성
  python3 fusion_insight.py --status          # vault 현황만 출력
  python3 fusion_insight.py --clear-vault     # vault 초기화 후 종료

.env 자동 로드 우선순위:
  1. _internal_system/pkms/.env  (BASE_DIR 기준)
  2. 스크립트와 같은 디렉터리의 .env
  3. 환경변수가 이미 설정된 경우 .env 값보다 우선
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ─── .env 자동 로드 ───────────────────────────────────────────────────────────

def load_dotenv(base_dir: Path) -> str | None:
    """
    .env 파일을 찾아 환경변수로 로드.
    이미 설정된 환경변수는 덮어쓰지 않음 (shell export가 우선).
    """
    candidates = [
        base_dir / "_internal_system" / "pkms" / ".env",
        Path(__file__).parent / ".env",
    ]
    env_path = next((p for p in candidates if p.exists()), None)
    if env_path is None:
        return None

    with open(env_path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = val

    return str(env_path)


# anthropic은 선택적 의존성 — 없으면 실행 불가
try:
    import anthropic as _anthropic_module
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# ─── 경로 및 상수 설정 ───────────────────────────────────────────────────────

BASE_DIR   = Path("/Users/arian/GDrive/NotebookLM_Staging")
VAULT_FILE = BASE_DIR / "_internal_system" / "pkms" / "pending_vault.json"
OUTPUT_DIR = BASE_DIR / "00_통합인사이트"

READWISE_HIGHLIGHTS_URL = "https://readwise.io/api/v2/highlights/"
READWISE_BOOKS_URL      = "https://readwise.io/api/v2/books/"

AI_MODEL      = "claude-sonnet-4-6"
AI_MAX_TOKENS = 3000
TRIGGER_COUNT = 6    # 융합 실행 임계값: 고유 출처 수


# ─── Vault (누적 버퍼) 관리 ──────────────────────────────────────────────────

def load_vault() -> dict:
    """pending_vault.json 로드. 없으면 빈 vault 반환."""
    if not VAULT_FILE.exists():
        return {
            "highlights": [],
            "created_at": datetime.now().isoformat(),
        }
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
    """vault 초기화 (융합 생성 후 호출)."""
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
    """
    vault에 하이라이트 추가 (ID 기준 중복 방지).
    Returns: (추가된 수, 중복 스킵 수)
    """
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


def count_unique_sources(vault: dict) -> int:
    """vault 내 고유 출처(book_id) 수 반환."""
    book_ids = {str(h.get("book_id", "")) for h in vault.get("highlights", [])}
    book_ids.discard("")
    return len(book_ids)


# ─── Readwise API 호출 ────────────────────────────────────────────────────────

def fetch_highlights(token: str, updated_after: datetime) -> list[dict]:
    """Readwise REST API에서 하이라이트 목록 가져오기 (페이지네이션 포함)."""
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
        params = {}   # next URL에는 이미 모든 파라미터 포함
    return highlights


def fetch_book_info(token: str, book_id: int, cache: dict) -> dict:
    """책/문서 메타데이터 가져오기 (캐시 활용)."""
    if book_id in cache:
        return cache[book_id]
    headers = {"Authorization": f"Token {token}"}
    resp    = requests.get(
        f"{READWISE_BOOKS_URL}{book_id}/", headers=headers, timeout=30
    )
    result         = resp.json() if resp.status_code == 200 else {}
    cache[book_id] = result
    return result


def enrich_vault_with_titles(vault: dict, token: str) -> None:
    """
    vault의 각 하이라이트에 book 제목/저자 정보 추가.
    이미 book_title이 있는 항목은 건너뜀 (재실행 안전).
    """
    cache: dict = {}
    for h in vault.get("highlights", []):
        if "book_title" not in h and h.get("book_id"):
            book_info      = fetch_book_info(token, h["book_id"], cache)
            h["book_title"]  = book_info.get("title")  or f"Source_{h['book_id']}"
            h["book_author"] = book_info.get("author") or ""


# ─── AI 융합 인사이트 생성 ────────────────────────────────────────────────────

FUSION_PROMPT = """\
당신은 지식 융합 전문가입니다. 아래는 {source_count}개의 서로 다른 출처에서 수집한 \
{highlight_count}개의 하이라이트입니다.

각 하이라이트는 다음 형식으로 제공됩니다:
  [출처: 제목 / 저자] 본문 텍스트

{highlights_block}

위 하이라이트들을 Zettelkasten 방법론으로 교차 분석하여 다음 3가지를 작성하세요.
답변은 한국어로 작성하고, 각 섹션 제목을 그대로 사용하세요.

════════════════════════════════════════
① 거대 가설 요약 (Cross-Source Synthesis)
════════════════════════════════════════
여러 출처에서 공통으로 등장하는 패턴이나 주제를 찾아,
하나의 "거대 가설" 또는 통합 명제로 요약하세요.
- 각 출처가 이 가설을 어떻게 뒷받침하는지 구체적으로 설명하세요
- 출처 간 공통점, 차이점, 상호 보완 관계를 명시하세요
- 10줄 이내로 작성하세요

════════════════════════════════════════
② 지식 간 비판적 대화 (Critical Dialogue Between Sources)
════════════════════════════════════════
출처들이 서로 '대화'한다면 어떤 논쟁이 벌어질까요?
- 출처 A와 출처 B가 동의하는 점 vs 충돌하는 점을 표로 정리하세요
- 가장 흥미로운 긴장 관계(tension) 2~3개를 선정하고 설명하세요
- 이 긴장 관계를 해소하거나 통합할 수 있는 제3의 관점을 제시하세요

════════════════════════════════════════
③ 창발적 실무 아이디어 7개 (Emergent Practical Ideas)
════════════════════════════════════════
단일 출처에서는 나올 수 없었던, 여러 출처의 교차점에서만 탄생하는 아이디어 7개를 제안하세요.
각 아이디어마다:
  [아이디어 N] 제목
  - 연결된 출처: (어느 출처들이 교차하는지)
  - 핵심 인사이트: (이 교차점에서 나온 통찰 한 문장)
  - 적용 시나리오: (구체적인 실행 방법 한 문장)
  - 기대 효과: (한 문장)
"""


def build_highlights_block(vault: dict) -> tuple[str, int, int]:
    """
    vault 하이라이트를 AI 프롬프트용 텍스트 블록으로 변환.
    Returns: (텍스트 블록, 사용된 하이라이트 수, 고유 출처 수)
    """
    highlights   = vault.get("highlights", [])
    seen_sources: set[str] = set()
    lines: list[str] = []

    for h in highlights:
        title  = h.get("book_title") or f"Source_{h.get('book_id', '?')}"
        author = h.get("book_author", "")
        text   = (h.get("text") or "").strip()
        if not text:
            continue
        source_label = f"{title} / {author}" if author else title
        seen_sources.add(title)
        lines.append(f"[출처: {source_label}]\n{text}")

    return "\n\n".join(lines), len(highlights), len(seen_sources)


def generate_fusion_insight(client, vault: dict) -> str:
    """Claude API를 호출해 Zettelkasten 융합 인사이트 생성."""
    highlights_block, h_count, s_count = build_highlights_block(vault)

    if not highlights_block.strip():
        print("  ⚠️  처리할 하이라이트 본문이 없습니다.")
        return ""

    prompt = FUSION_PROMPT.format(
        source_count=s_count,
        highlight_count=h_count,
        highlights_block=highlights_block,
    )

    try:
        message = client.messages.create(
            model=AI_MODEL,
            max_tokens=AI_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()
    except Exception as e:
        print(f"  ⚠️  AI 오류: {e}")
        return ""


# ─── 출력 파일 저장 ───────────────────────────────────────────────────────────

SEP = "=" * 72


def save_fusion_output(
    vault: dict, fusion_text: str, dry_run: bool = False
) -> Path | None:
    """Zettelkasten 융합 결과를 00_통합인사이트 폴더에 저장."""
    date_str  = datetime.now().strftime("%Y%m%d_%H%M")
    out_path  = OUTPUT_DIR / f"Zettelkasten_Fusion_{date_str}.txt"

    highlights = vault.get("highlights", [])
    sources    = sorted({
        h.get("book_title") or f"Source_{h.get('book_id', '?')}"
        for h in highlights
    })
    source_lines = "\n".join(f"  • {s}" for s in sources)

    content = "\n".join([
        SEP,
        "  Zettelkasten 융합 인사이트",
        f"  생성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"  출처 수: {len(sources)}개 | 하이라이트 수: {len(highlights)}개",
        SEP,
        "",
        "[ 융합된 출처 목록 ]",
        source_lines,
        "",
        SEP,
        "",
        fusion_text,
        "",
        SEP,
    ])

    if dry_run:
        print(f"\n  [DRY-RUN] 저장 예정 경로: {out_path}")
        for ln in content.splitlines()[:30]:
            print(f"    {ln}")
        if content.count("\n") > 30:
            print("    ...")
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Zettelkasten 융합 인사이트 엔진 v1"
    )
    parser.add_argument(
        "--days", type=int, default=7,
        help="최근 N일 하이라이트 가져오기 (기본: 7)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="파일 변경 없이 미리보기"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="출처 수 미달해도 강제로 융합 인사이트 생성"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="vault 현황만 출력하고 종료"
    )
    parser.add_argument(
        "--clear-vault", dest="clear_vault", action="store_true",
        help="vault 초기화 후 종료"
    )
    args = parser.parse_args()

    print("━" * 60)
    print("  Zettelkasten 융합 인사이트 엔진 v1")
    print(f"  기준 경로: {BASE_DIR}")
    print("━" * 60)

    # ── .env 자동 로드
    env_path = load_dotenv(BASE_DIR)
    if env_path:
        print(f"\n📂 .env 로드: {env_path}")

    # ── vault 초기화 요청
    if args.clear_vault:
        clear_vault(dry_run=args.dry_run)
        return

    # ── vault 로드 및 현황 출력
    vault           = load_vault()
    h_in_vault      = len(vault.get("highlights", []))
    src_in_vault    = count_unique_sources(vault)
    print(f"\n📦 현재 vault: {h_in_vault}개 하이라이트 | {src_in_vault}개 출처 (임계값: {TRIGGER_COUNT})")

    if args.status:
        # 출처 목록 상세 출력
        titles = sorted({
            h.get("book_title") or f"book_id:{h.get('book_id','?')}"
            for h in vault.get("highlights", [])
        })
        if titles:
            print("\n[ 현재 vault 출처 목록 ]")
            for t in titles:
                print(f"  • {t}")
        return

    # ── API 키 확인
    rw_token = os.environ.get("READWISE_API_TOKEN")
    if not rw_token:
        print("❌ READWISE_API_TOKEN 없음. .env 파일을 확인하세요.")
        sys.exit(1)

    # ── Readwise에서 최신 하이라이트 가져오기
    updated_after = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    print(
        f"\n🔄 Readwise API 호출 중... "
        f"(최근 {args.days}일 / {updated_after.strftime('%Y-%m-%d')} 이후)"
    )

    try:
        new_highlights = fetch_highlights(rw_token, updated_after)
    except Exception as e:
        print(f"❌ Readwise API 오류: {e}")
        sys.exit(1)

    print(f"📥 가져온 하이라이트: {len(new_highlights)}개")

    # ── vault에 추가 (ID 기준 중복 방지)
    added, skipped = add_to_vault(vault, new_highlights)
    print(f"   → vault 추가: {added}개 | 중복 스킵: {skipped}개")

    # ── 제목 정보 보완 (book_title 미설정 항목만)
    needs_enrich = any(
        "book_title" not in h
        for h in vault.get("highlights", [])
        if h.get("book_id")
    )
    if needs_enrich:
        print("📚 출처 메타데이터 조회 중...")
        enrich_vault_with_titles(vault, rw_token)

    # ── 현재 상태 재계산
    total_in_vault = len(vault.get("highlights", []))
    unique_sources = count_unique_sources(vault)
    needs_more     = unique_sources < TRIGGER_COUNT

    print(f"\n📊 vault 상태: {total_in_vault}개 하이라이트 | {unique_sources}/{TRIGGER_COUNT}개 출처")

    # ── vault 저장 (융합 전이라도 항상 저장)
    save_vault(vault, dry_run=args.dry_run)

    # ── 출처 미달 시 대기
    if needs_more and not args.force:
        remaining = TRIGGER_COUNT - unique_sources
        print(f"\n⏳ 융합 대기 중 — 출처 {remaining}개 더 필요 ({unique_sources}/{TRIGGER_COUNT})")
        print("━" * 60)
        return

    # ── AI 클라이언트 초기화
    if not ANTHROPIC_AVAILABLE:
        print("❌ anthropic 라이브러리 없음: pip install anthropic")
        sys.exit(1)

    ant_key = os.environ.get("ANTHROPIC_API_KEY")
    if not ant_key:
        print("❌ ANTHROPIC_API_KEY 없음. .env 파일을 확인하세요.")
        sys.exit(1)

    client = _anthropic_module.Anthropic(api_key=ant_key)

    # ── 융합 인사이트 생성
    print(f"\n🤖 AI 융합 인사이트 생성 중... (모델: {AI_MODEL})")
    fusion_text = generate_fusion_insight(client, vault)

    if not fusion_text:
        print("❌ AI 인사이트 생성 실패 — vault는 유지됩니다.")
        sys.exit(1)

    # ── 출력 파일 저장
    out_path = save_fusion_output(vault, fusion_text, dry_run=args.dry_run)

    if out_path:
        print(f"\n✅ 융합 인사이트 저장 완료: {out_path}")

    # ── vault 초기화 (융합 완료 후 다음 사이클 준비)
    clear_vault(dry_run=args.dry_run)

    print("\n" + "━" * 60)
    print("  Zettelkasten 융합 완료")
    print(f"  출처 수: {unique_sources}개 | 하이라이트 수: {total_in_vault}개")
    if out_path:
        print(f"  출력: {out_path}")
    print("━" * 60)


if __name__ == "__main__":
    main()
