"""
morning_report.py — Thought Factory 아침 리포트 엔진 v3.6 (STABLE)

핵심 목표(헌법):
- "매일 아침보고서는 반드시 1개" (신규가 없어도 기존 풀에서 뽑아서 생성)
- 구조 고정: 인사 → 출처(압축) → 1/2/3/10 + [치명적 이유] → 🎯 링크
- 구글챗: 링크는 숨김(<URL|텍스트>) 형태, 원문(Drive) 링크 제거
- 출처 중복 제거: 모델 출력에는 출처 섹션 금지, 헤더에서만 제공
- 웹훅 길이 제한 대응: "뒤(10 + 치명적 이유 + 🎯)" 무조건 보존하며 절단

실행:
- python3 morning_report.py
- python3 morning_report.py --dry-run
"""

import os, json, glob, re, sys, time
from collections import defaultdict
from datetime import datetime, timedelta
from urllib import request as urllib_request
from urllib.error import URLError

# retriever 연결 (선택적 — Qdrant 없어도 동작)
try:
    from retriever import search_by_questions as _search_by_q
    _RETRIEVER = True
except ImportError:
    _RETRIEVER = False

# action_tracker 연결 (선택적 — 없어도 동작)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
try:
    from action_tracker import get_yesterday_action, save_today_action as _save_action
    _ACTION_TRACKER = True
except ImportError:
    _ACTION_TRACKER = False

# devonthink_sync 연결 (선택적 — 없어도 동작)
try:
    from devonthink_sync import save_morning_report as _dt_save
    _DEVONTHINK = True
except ImportError:
    _DEVONTHINK = False

from google import genai
from dotenv import load_dotenv

# ── 설정 ───────────────────────────────────────────────────
BASE_DIR   = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH   = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")

ARCHIVE   = os.path.join(BASE_DIR, "02_Archive")
SOURCES   = os.path.join(ARCHIVE, "sources")

MODEL_ID        = os.getenv("MORNING_MODEL_ID", "gemini-2.5-pro")
REPORT_LIMIT    = int(os.getenv("MORNING_REPORT_LIMIT", "3000"))

# NotebookLM "소스 등록 화면" 링크를 여기에 넣어두면 가장 안전합니다.
# (기본값은 기존에 쓰던 링크로 두되, 운영에서는 .env로 교체 권장)
NOTEBOOKLM_SOURCES_URL = os.getenv(
    "NOTEBOOKLM_SOURCES_URL",
    "https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1"
)

FOOTER = f"\n\n🎯 <{NOTEBOOKLM_SOURCES_URL}|제텔카스텐 전략실로 바로가기>"

if not GOOGLE_API_KEY:
    print(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")
    raise SystemExit(1)

client = genai.Client(api_key=GOOGLE_API_KEY)

# ───────────────────────────────────────────────────────────
# 1) 소스 로드
# ───────────────────────────────────────────────────────────

def load_source_files_by_prefix(prefix: str) -> list[dict]:
    """
    sources/ 에서 특정 날짜(prefix=YYMMDD) 파일 로드.
    """
    pattern = os.path.join(SOURCES, f"{prefix}_*.txt")
    files = sorted(glob.glob(pattern))
    out = []
    for fpath in files:
        fname = os.path.basename(fpath)
        upper = fname.upper()
        if "READWISE" in upper:
            stype = "readwise"
        elif "APPLENOTES" in upper or "APPLE_NOTES" in upper or "HEPTABASE" in upper:
            stype = "memo"
        else:
            stype = "other"
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                out.append({"fname": fname, "content": content, "source_type": stype})
        except Exception as e:
            print(f"  ❌ 읽기 실패 {fpath}: {e}")
    return out


def load_pool_files(max_days: int = 60, max_files: int = 20000) -> list[dict]:
    """
    신규가 없거나 부족할 때를 대비해 sources/ 전체를 풀로 로드.
    (실제로는 glob로 전부 읽을 수 있으므로, max_files로 컷)
    """
    files = sorted(glob.glob(os.path.join(SOURCES, "*.txt")))
    if not files:
        return []

    # 너무 많으면 뒤쪽(최신) 중심으로 컷
    files = files[-max_files:]

    out = []
    for fpath in files:
        fname = os.path.basename(fpath)
        upper = fname.upper()
        if "READWISE" in upper:
            stype = "readwise"
        elif "APPLENOTES" in upper or "APPLE_NOTES" in upper or "HEPTABASE" in upper:
            stype = "memo"
        else:
            stype = "other"
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
            if content:
                out.append({"fname": fname, "content": content, "source_type": stype})
        except Exception:
            pass
    return out


# ───────────────────────────────────────────────────────────
# 2) 선택 규칙: "매일 1개" 보장 + 균형 샘플링
#   - 기본: Readwise 3 + Memo 2 + Other 1 (총 6)
# ───────────────────────────────────────────────────────────

def pick_balanced_sources(pool: list[dict], rw_n=3, memo_n=2, other_n=1) -> tuple[list[dict], list[dict], list[dict]]:
    rw   = [x for x in pool if x["source_type"] == "readwise"]
    memo = [x for x in pool if x["source_type"] == "memo"]
    oth  = [x for x in pool if x["source_type"] == "other"]

    # 최신 우선 (파일명이 날짜 기반이므로 대체로 정렬이 의미 있음)
    rw_pick   = rw[-rw_n:] if len(rw) >= rw_n else rw
    memo_pick = memo[-memo_n:] if len(memo) >= memo_n else memo
    oth_pick  = oth[-other_n:] if len(oth) >= other_n else oth

    return rw_pick, memo_pick, oth_pick


def compact_source_header(rw_pick: list[dict], memo_pick: list[dict], oth_pick: list[dict]) -> str:
    """
    구글챗에서 출처가 길어져 본문이 잘리는 문제 방지:
    - 외부지성: 대표 2개 + 외 n개
    - 내부메모: 대표 2개 + 외 n개
    - 기타: 대표 1개 + 외 n개
    """
    def head_items(items: list[dict], k: int) -> tuple[list[str], int]:
        names = [x["fname"] for x in items]
        shown = names[:k]
        rest = max(0, len(names) - len(shown))
        return shown, rest

    rw_shown, rw_rest = head_items(rw_pick, 2)
    memo_shown, memo_rest = head_items(memo_pick, 2)
    oth_shown, oth_rest = head_items(oth_pick, 1)

    lines = ["📚 출처"]
    if rw_shown:
        lines.append(f"• {rw_shown[0]}")
        if len(rw_shown) > 1:
            lines.append(f"• {rw_shown[1]}")
        if rw_rest:
            lines.append(f"• 외 {rw_rest}개 외부소스")
    else:
        lines.append("• 외부소스 0개")

    if memo_shown:
        for n in memo_shown:
            lines.append(f"• {n}")
        if memo_rest:
            lines.append(f"• 외 {memo_rest}개 내부메모")
    else:
        lines.append("• 내부메모 0개")

    if oth_shown:
        lines.append(f"• {oth_shown[0]}")
        if oth_rest:
            lines.append(f"• 외 {oth_rest}개 기타")
    # 기타가 없으면 굳이 표시 안 함

    return "\n".join(lines)


# ───────────────────────────────────────────────────────────
# 3) 프롬프트 (출처 섹션 금지 + 1/2/3/10 강제)
# ───────────────────────────────────────────────────────────

_RULES = """[절대 규칙 — 위반 시 실패]
1) 반드시 아래 순서/라벨을 지켜라: 오늘의 핵심 판단 → 1. 거대 가설 → 2. 충돌 지점 → 3. 창발 아이디어 → 오늘 바로 실행 → 10. 파괴적 질문 → [이 질문이 치명적인 이유]
2) '📚 출처' 섹션은 절대 출력하지 마라. (출처는 시스템이 헤더에서 제공한다)
3) '오늘 바로 실행'은 반드시 독립 섹션으로 분리하여 아래 4요소를 모두 포함하라:
   - 다음 행동: (구체적 행동 1개)
   - 완료 조건: (언제 끝난 것으로 볼 것인가)
   - 소요 시간: (예상 소요 시간)
   - 리스크: (실행 시 주의할 점)
4) 10. 파괴적 질문은 질문 1개(완전한 문장)만 출력하라.
5) [이 질문이 치명적인 이유]는 2~4문장으로, 왜 치명적인지 의미를 설명하라.
6) 과장/환각 금지: 소스에 없는 사실을 단정하지 말고, 불확실하면 "가정"으로 표시하라.
7) 문체: 아침에 읽기 편한 간결한 문장. 불필요한 학술 표현 금지. 설명형·직관적으로 작성하라.
"""

_TEMPLATE = """오늘의 핵심 판단
→ (오늘 전략적으로 판단해야 할 것)
→ (고려할 리스크 또는 기회)

1. 거대 가설
(여러 자료를 통합한 가장 중요한 구조적 가설 — 최대 3개)

2. 충돌 지점
(자료 사이의 긴장 관계 또는 상충 논리 — 최대 3개)

3. 창발 아이디어
(분석 기반 새로운 전략적 아이디어 — 최대 3개)

오늘 바로 실행
다음 행동: (구체적 행동 1개)
완료 조건: (언제 끝난 것으로 볼 것인가)
소요 시간: (예상 소요 시간)
리스크: (실행 시 주의할 점)

10. 파괴적 질문
(현재 사고 구조를 흔드는 질문 1개)

[이 질문이 치명적인 이유]
(2~4문장)
"""

def build_prompt_with_questions(q_sources: list[dict], yesterday_action: str = "") -> str:
    """질문 레지스트리 기반 프롬프트 — 질문별 소스 충돌 구조"""
    # 질문별 그룹핑
    groups: dict[str, list] = defaultdict(list)
    for s in q_sources:
        groups[s["question"]].append(s)

    q_list = "\n".join(f"• {q}" for q in groups)
    sources_block = ""
    for q, sources in groups.items():
        sources_block += f"\n[검색 질문: {q}]\n"
        for s in sources:
            sources_block += f"— {s['fname']} ({s['source_type']}, 유사도 {s['score']:.3f})\n"
            sources_block += f"{s['content'][:900].strip()}\n\n"

    yesterday_block = (
        f"\n[어제 실행 Action — 오늘 분석에 연속성 반영]\n{yesterday_action}"
        if yesterday_action else ""
    )

    return (
        "당신은 '생각공장 Thought Factory'의 전략 분석 엔진이다.\n"
        "아래 전략 질문들을 렌즈로 삼아 관련 소스를 충돌시키고 "
        "'실행 가능한 의사결정 시스템'을 위한 보고서를 작성하라.\n\n"
        f"{_RULES}\n\n"
        f"{_TEMPLATE}\n\n"
        f"[오늘의 전략 질문]\n{q_list}\n\n"
        f"[질문별 관련 소스 — 서로 다른 질문의 소스끼리 충돌시킬 것]\n"
        f"{sources_block}"
        f"{yesterday_block}"
    )


def build_prompt(rw_pick: list[dict], memo_pick: list[dict], oth_pick: list[dict],
                 yesterday_action: str = "") -> str:
    rw_block = "\n\n".join(x["content"][:1200] for x in rw_pick) or "(외부소스 없음)"
    memo_block = "\n\n".join(x["content"][:1200] for x in memo_pick) or "(내부메모 없음)"
    oth_block = "\n\n".join(x["content"][:900] for x in oth_pick) or ""

    yesterday_block = (
        f"\n\n[어제 실행 Action — 오늘 분석에 연속성 반영]\n{yesterday_action}"
        if yesterday_action else ""
    )

    return (
        "당신은 '생각공장 Thought Factory'의 전략 분석 엔진이다.\n"
        "다음 자료(Readwise, 메모, 업무파일)를 분석하여 "
        "'실행 가능한 의사결정 시스템'을 위한 보고서를 작성하라.\n\n"
        f"{_RULES}\n\n"
        f"{_TEMPLATE}\n\n"
        "[Source A — 외부지성(Readwise)]\n"
        f"{rw_block}\n\n"
        "[Source B — 내부생각(메모)]\n"
        f"{memo_block}\n\n"
        "[Source C — 기타(참고)]\n"
        f"{oth_block}"
        f"{yesterday_block}"
    )


# ───────────────────────────────────────────────────────────
# 4) Gemini 호출
# ───────────────────────────────────────────────────────────

def call_gemini(prompt: str) -> str | None:
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            return (resp.text or "").strip()
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"  ⚠️ 429 한도 — {wait}s 대기 ({attempt+1}/3)")
                time.sleep(wait)
                continue
            print(f"  ❌ Gemini 오류: {e}")
            return None
    return None


# ───────────────────────────────────────────────────────────
# 5) 포맷/검증/절단 (뒤 보존)
# ───────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    # 마크다운 기호 제거 (구글챗 호환)
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)   # **bold**, *italic*
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)      # __bold__, _italic_
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # ### 헤딩
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def ensure_sections(text: str) -> str:
    """
    모델이 섹션을 비우는 경우 최소 안전장치.
    """
    needed = ["오늘의 핵심 판단", "1. 거대 가설", "2. 충돌 지점", "3. 창발 아이디어", "오늘 바로 실행", "10. 파괴적 질문", "[이 질문이 치명적인 이유]"]
    for k in needed:
        if k not in text:
            text += f"\n\n{k}\n(누락됨 — 다음 실행에서 재생성 필요)"
    return text


def tail_preserving_trim(full: str, limit: int) -> str:
    """
    길이 제한이 걸리면 '뒤(10 + 치명적 이유 + 🎯)'를 살리고,
    앞부분(가설/충돌)을 먼저 줄인다.
    """
    if len(full) <= limit:
        return full

    # 보존 시작점: 10번부터 무조건 살림
    idx = full.find("10. 파괴적 질문")
    preserve_start = idx if idx != -1 else int(len(full) * 0.6)

    preserved = full[preserve_start:]
    head = full[:preserve_start]

    # preserved 자체가 이미 초과면, preserved만 잘라서라도 보내기
    if len(preserved) >= limit:
        return preserved[:limit]

    target_head = limit - len(preserved)

    # head를 줄이되, "공장장님" 인사/출처 헤더 다음은 어느 정도 남기기
    # 가장 단순: head를 뒤에서 잘라낸다 (앞의 인사/구조 보존)
    head = head[:target_head]
    return head + preserved


# ───────────────────────────────────────────────────────────
# 6) 웹훅
# ───────────────────────────────────────────────────────────

def send_webhook(text: str):
    if not WEBHOOK_URL:
        return
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req = urllib_request.Request(
            WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib_request.urlopen(req, timeout=10)
    except URLError as e:
        print(f"  ⚠️ Webhook 실패: {e}")


# ───────────────────────────────────────────────────────────
# 7) run
# ───────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    now = datetime.now()
    yesterday = now - timedelta(days=1)

    # 1) 어제 prefix로 먼저 로드 (있으면 그걸 우선)
    prefix = yesterday.strftime("%y%m%d")
    day_files = load_source_files_by_prefix(prefix)

    # 2) 없다/부족하면 풀에서 뽑아서라도 생성 ("매일 1개" 보장)
    pool = day_files
    if len(pool) < 6:
        pool = load_pool_files()
    if not pool:
        msg = f"📋 아침 보고 [{now.strftime('%Y-%m-%d %H:%M')}]\nsources/에 처리 가능한 소스가 없습니다."
        print(msg)
        if not dry_run:
            send_webhook(msg)
        return

    # 3) 균형 선택 (총 6개를 목표)
    rw_pick, memo_pick, oth_pick = pick_balanced_sources(pool, 3, 2, 1)

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] ── 아침 융합 리포트 v3.6 시작{'  [DRY-RUN]' if dry_run else ''}")
    print(f"  📂 선택: Readwise {len(rw_pick)} / 메모 {len(memo_pick)} / 기타 {len(oth_pick)}  (pool {len(pool)})")

    # 4) 어제 Action 조회
    yesterday_action = get_yesterday_action() if _ACTION_TRACKER else ""
    if yesterday_action:
        print(f"  📎 어제 Action 주입됨")

    # 5) 질문 레지스트리 기반 검색 (우선) → 폴백: 기존 풀 방식
    prompt = None
    if _RETRIEVER:
        try:
            q_sources = _search_by_q(top_per_q=3)
            if q_sources:
                q_count = len(set(s["question_id"] for s in q_sources))
                print(f"  🎯 질문 기반 검색: 소스 {len(q_sources)}개 (질문 {q_count}개)")
                prompt = build_prompt_with_questions(q_sources, yesterday_action)
        except Exception as e:
            print(f"  ⚠️ 질문 검색 실패, 기본 풀 사용: {e}")

    if prompt is None:
        prompt = build_prompt(rw_pick, memo_pick, oth_pick, yesterday_action)
    print("  🔄 Gemini 호출 중...")
    body = call_gemini(prompt)
    if not body:
        print("  ❌ 생성 실패")
        return

    body = normalize(body)
    body = ensure_sections(body)

    # 5) 헤더(출처 압축) + 본문 + FOOTER
    time_tag = now.strftime("%H:%M")
    date_tag = now.strftime("%y%m%d")
    file_tag = now.strftime("%H%M%S")
    out_name = f"{date_tag}_Zettelkasten_{file_tag}_아침융합리포트v3.txt"
    out_path = os.path.join(ARCHIVE, out_name)

    header = (
        f"🌅 아침 융합 리포트 v3 [{time_tag}]\n"
        f"소스: Readwise {len(rw_pick)}개 + 메모 {len(memo_pick)}개 + 기타 {len(oth_pick)}개\n"
    )
    source_header = compact_source_header(rw_pick, memo_pick, oth_pick)

    full = header + "\n" + source_header + "\n\n" + body + FOOTER

    # 6) 길이 제한(구글챗)을 고려한 절단: 뒤 보존 (DEVONthink엔 전문 저장)
    full_uncut = full
    full = tail_preserving_trim(full, REPORT_LIMIT)

    # 7) 저장
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full)
        print(f"  ✅ 저장: {out_name} ({len(full)}자)")
    except Exception as e:
        print(f"  ❌ 저장 실패: {e}")

    # 8) dry-run 프리뷰: head + tail 같이 보여주기 (오해 방지)
    if dry_run:
        lines = full.splitlines()
        print("\n" + "-" * 60)
        print("[DRY-RUN] webhook payload preview (head 18 + tail 18 lines)")
        print("-" * 60)
        print("\n".join(lines[:18]))
        if len(lines) > 36:
            print("\n  ...(중략)...\n")
            print("\n".join(lines[-18:]))
        elif len(lines) > 18:
            print("\n".join(lines[18:]))
        print("-" * 60)
        return

    # 9) Action 추적 저장
    if _ACTION_TRACKER:
        _save_action(full)

    # 10) DEVONthink 저장 (전문 보존 — 웹훅 절단 전 원본)
    if _DEVONTHINK:
        try:
            dt_uuid = _dt_save(full_uncut, date_tag)
            if dt_uuid:
                print(f"  📂 DEVONthink 저장 → UUID: {dt_uuid}")
            else:
                print("  ⚠️ DEVONthink 저장 실패 (계속 진행)")
        except Exception as e:
            print(f"  ⚠️ DEVONthink 오류: {e} (계속 진행)")

    # 11) 웹훅 전송: 저장된 텍스트 그대로 (프리뷰만 보내지 않음)
    send_webhook(full)
    print("  📡 Webhook 전송")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    # --force: 소스 부족 여부와 무관하게 강제 실행 (기본 동작과 동일, 플래그만 인식)
    run(dry_run=dry_run)
