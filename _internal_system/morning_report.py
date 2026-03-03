"""
morning_report.py — Thought Factory 아침 리포트 엔진 v2
Constitution: Master Rule Set v2.6.9

Patch 1: Fusion Gate         — 이질적 소스 필수 조건 검사
Patch 2: Report Builder      — Queue 기반 5:5 매칭 + 3:3:3:1 구조
Patch 3: Compressor          — 3000자 초과 시 상단부터 압축, 창발·질문 절대 보존
Patch 4: Google Chat Formatter — **, ──, 사유 제거
Patch 5: End-to-End daily job  — dry-run 포함 (앞 15줄 + 뒤 10줄)
"""

import os, json, glob, re, sys, time
from datetime import datetime, timedelta
from urllib import request as urllib_request
from urllib.error import URLError
from google import genai
from dotenv import load_dotenv

# ── 설정 ───────────────────────────────────────────────────
BASE_DIR     = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH     = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
ARCHIVE        = os.path.join(BASE_DIR, "02_Archive")
SOURCES        = os.path.join(ARCHIVE, "sources")
MODEL_ID       = "gemini-2.5-pro"
REPORT_LIMIT   = 3000
FOOTER         = (
    "\n\n🔗 <https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1|제텔카스텐 전략실 바로가기>"
    "\n📂 <https://drive.google.com/drive/u/1/folders/1TmwPlc6JCtYbSwXzRonI3BeehLX059Vg|오늘자 원문 (Google Drive)>"
)

if not GOOGLE_API_KEY:
    print(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")
    exit(1)

client = genai.Client(api_key=GOOGLE_API_KEY)


# ═══════════════════════════════════════════════════════════
# Patch 1: Fusion Gate
# 역할: 외부 소스 ≥1 AND 내부 메모 ≥1 검증. 미충족 시 실행 차단.
# ═══════════════════════════════════════════════════════════

def load_source_files(prefix: str) -> list[dict]:
    """
    sources/ 에서 어제 날짜(prefix=YYMMDD) 원본 파일 로드.
    파일명 키워드로 소스 유형 분류 (Readwise / 메모 / 기타).
    """
    pattern = os.path.join(SOURCES, f"{prefix}_*.txt")
    files   = sorted(glob.glob(pattern))
    result  = []
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
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            result.append({"fname": fname, "content": content, "source_type": stype})
        except Exception as e:
            print(f"  ❌ 읽기 실패 {fpath}: {e}")
    return result


def fusion_gate(readwise_files: list, memo_files: list) -> bool:
    """
    Heterogeneous Fusion 필수 조건:
      - 외부 소스(Readwise) ≥ 1
      - 내부 메모(AppleNotes/Heptabase) ≥ 1
    둘 다 없으면 단일 군집 요약이므로 차단.
    """
    if len(readwise_files) < 1:
        print("  🚫 [Fusion Gate BLOCK] 외부 소스(Readwise) 0개 — 단일 군집 요약 금지")
        return False
    if len(memo_files) < 1:
        print("  🚫 [Fusion Gate BLOCK] 내부 메모 0개 — 단일 군집 요약 금지")
        return False
    print(f"  ✅ [Fusion Gate PASS] Readwise {len(readwise_files)}개 × 메모 {len(memo_files)}개")
    return True


# ═══════════════════════════════════════════════════════════
# Patch 2: Report Builder
# 역할: Queue 기반 5:5 매칭 후 3:3:3:1 구조 프롬프트 생성.
# ═══════════════════════════════════════════════════════════

_RULES = """[절대 규칙 — 위반 시 실패]
1. 이질적 융합 강제: Readwise(외부)와 메모(내부) 간의 충돌·보완만 허용. 단일 군집 요약 금지.
2. 억지 조합 금지: 실제 긴장·충돌·보완 관계 없으면 억지 연결 말고 솔직히 분리.
3. 형식적 갯수 채우기 금지: 통찰 강도 낮으면 1개만 출력.
4. 마크다운 강조 완전 금지: **볼드**, *이탤릭*, ─── 구분선 일절 금지.
5. 구분선 완전 금지: 줄바꿈으로만 섹션 구분.
6. '사유' 대신 '생각' 사용.
7. 링크 문법: <URL|표시텍스트> (구글챗 전용).
8. 출처: 파일명/제목만 (설명 없음).
9. 창발 아이디어(3번)와 파괴적 질문(10번)은 어떤 압축에도 절대 누락 금지."""

_TEMPLATE = """[출력 순서 엄수]

공장장님, 어제의 지식 충돌 결과입니다.

📚 출처
{source_list}

1. 거대 가설 (최대 3개)
Readwise(외부)와 메모(내부)를 관통하는 날카로운 가설. 각 소스가 가설을 어떻게 지지·반박하는지 명시.

2. 충돌 지점 (최대 3개)
Readwise(외부)와 메모(내부)의 모순·시각 차이를 비판적 대조. 억지 연결 금지 — 없으면 생략.

3. 창발 아이디어 (최대 3개) ← 절대 누락 금지
충돌에서 도출된 구체적 실행 방안. 오늘 바로 실행할 단 한 가지 반드시 포함.

10. 파괴적 질문 ← 절대 누락 금지
이 융합에서 가장 파괴력이 큰 지점을 골라 공장장의 허를 찌르는 단 하나의 맞춤형 질문. 고정 문구 금지."""


def build_prompt(readwise_files: list, memo_files: list) -> str:
    """
    Queue 기반 5:5 매칭:
      - pair_count = min(len(rw), len(memo)) 기준으로 1:1 페어링
      - 나머지 소스는 참고용으로 추가
    """
    pair_count   = min(len(readwise_files), len(memo_files))
    rw_primary   = readwise_files[:pair_count]
    memo_primary = memo_files[:pair_count]
    rw_extra     = readwise_files[pair_count:]
    memo_extra   = memo_files[pair_count:]

    all_sources = rw_primary + memo_primary + rw_extra + memo_extra
    source_list = "\n".join(f"• {f['fname']}" for f in all_sources[:10])

    rw_block   = "\n\n".join(f["content"][:1500] for f in rw_primary)   or "(없음)"
    memo_block = "\n\n".join(f["content"][:1500] for f in memo_primary) or "(없음)"

    extra_section = ""
    if rw_extra or memo_extra:
        extra_items = rw_extra + memo_extra
        extra_section = (
            "\n\n[추가 소스 — 참고용]\n"
            + "\n\n".join(f["content"][:400] for f in extra_items)
        )

    return (
        f"당신은 생각공장의 아침 융합 엔진입니다. "
        f"외부 지성(Readwise)과 내부 생각(메모)을 5:5로 충돌시켜 아침 리포트를 작성하세요.\n\n"
        f"{_RULES}\n\n"
        f"{_TEMPLATE.format(source_list=source_list)}\n\n"
        f"---\n"
        f"[Source A — Readwise (외부지성 50%)]\n{rw_block}\n\n"
        f"[Source B — 메모 (내부생각 50%)]\n{memo_block}"
        f"{extra_section}"
    )


# ═══════════════════════════════════════════════════════════
# Patch 3: Compressor
# 역할: 3000자 초과 시 상단(가설→충돌)부터 압축. 창발·질문 절대 보존.
# ═══════════════════════════════════════════════════════════

# 보존 구간 시작 마커 (창발 or 파괴적질문 중 먼저 나오는 쪽)
_PRESERVE_MARKERS = [
    "3. 창발 아이디어",
    "창발 아이디어",
    "10. 파괴적 질문",
    "파괴적 질문",
    "🔟",
]


def compress_report(text: str, limit: int = REPORT_LIMIT) -> str:
    """
    3000자 초과 시 상단부터 압축.
    전략: preserve_start 이전(출처·가설·충돌)을 문장 단위로 트리밍.
    창발(3번)·파괴적질문(10번)은 절대 건드리지 않음.
    """
    if len(text) <= limit:
        return text

    preserve_start = len(text)
    for marker in _PRESERVE_MARKERS:
        idx = text.find(marker)
        if idx != -1 and idx < preserve_start:
            preserve_start = idx

    compressible = text[:preserve_start]
    preserved    = text[preserve_start:]
    target       = limit - len(preserved)

    if target <= 0:
        print(f"  ⚠️ 압축 한계: 보존 구간({len(preserved)}자) 자체가 {limit}자 초과 — 보존 구간만 유지")
        return preserved[:limit]

    if len(compressible) > target:
        # 문장 단위 트리밍 (마지막 문장부터 제거 → 상단 우선 보존)
        sentences = re.split(r'(?<=[.!?。\n])', compressible)
        while sentences and len("".join(sentences)) > target:
            sentences.pop()
        compressible = "".join(sentences)
        # 여전히 초과 시 단순 절단
        if len(compressible) > target:
            compressible = compressible[:target]

    result = compressible + preserved
    print(f"  ✂️ 압축: {len(text)}자 → {len(result)}자 (한도 {limit}자)")
    return result


# ═══════════════════════════════════════════════════════════
# Patch 4: Google Chat Formatter
# 역할: 구글챗 포맷 강제 적용.
# ═══════════════════════════════════════════════════════════

def format_for_google_chat(text: str) -> str:
    """
    - **볼드** 제거
    - *이탤릭* 제거 (단, <URL|text> 내부 제외)
    - ─── / --- 구분선 제거
    - '사유' → '생각'
    - 연속 빈 줄 정리 (3줄 이상 → 2줄)
    - <URL|text> 링크는 그대로 유지 (구글챗 전용 문법)
    """
    # **볼드** → 텍스트
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    # *이탤릭* → 텍스트 (< > 내부 보호)
    text = re.sub(r'(?<![<|])\*([^*\n<>]+?)\*(?![>|])', r'\1', text)
    # 구분선 제거 (─, ━, -, = 3개 이상 반복)
    text = re.sub(r'[─━\-=]{3,}', '', text)
    # '사유' → '생각'
    text = text.replace('사유', '생각')
    # 빈 줄 중복 정리
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ═══════════════════════════════════════════════════════════
# Patch 5: End-to-End daily job
# 실행: python3 morning_report.py          (실제 실행)
#       python3 morning_report.py --dry-run (앞 15줄 + 뒤 10줄 미리보기)
# ═══════════════════════════════════════════════════════════

def send_webhook(text: str):
    if not WEBHOOK_URL:
        return
    if len(text) > 3500:
        text = text[:3480] + "\n\n(이하 생략)"
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req  = urllib_request.Request(
            WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib_request.urlopen(req, timeout=10)
    except URLError as e:
        print(f"  ⚠️ Webhook 실패: {e}")


def call_gemini(prompt: str) -> str | None:
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            return resp.text
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                msg  = f"⚠️ 아침 융합 429 한도 초과 — {wait}초 대기 ({attempt+1}/3)"
                print(f"  {msg}")
                send_webhook(msg)
                time.sleep(wait)
            else:
                msg = f"❌ 아침 융합 오류: {e}"
                print(f"  {msg}")
                send_webhook(msg)
                return None
    send_webhook("❌ 아침 융합 3회 실패. 수동 확인 요망.")
    return None


def run(dry_run: bool = False):
    now       = datetime.now()
    yesterday = now - timedelta(days=1)
    prefix    = yesterday.strftime("%y%m%d")
    date_tag  = now.strftime("%y%m%d")
    time_tag  = now.strftime("%H%M%S")

    label = "  [DRY-RUN]" if dry_run else ""
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] ── 아침 융합 리포트 v2 시작{label}")

    # ── Step 1: 소스 파일 로드 (sources/ 디렉토리 기준) ──
    all_files = load_source_files(prefix)
    if not all_files:
        msg = f"📋 아침 보고 [{now.strftime('%Y-%m-%d %H:%M')}]\n어제({prefix}) 처리된 소스 파일 없음."
        print(f"  {msg}")
        if not dry_run:
            send_webhook(msg)
        return

    readwise_files = [f for f in all_files if f["source_type"] == "readwise"]
    memo_files     = [f for f in all_files if f["source_type"] == "memo"]
    print(f"  📂 로드: Readwise {len(readwise_files)}개 / 메모 {len(memo_files)}개 / 기타 {len(all_files)-len(readwise_files)-len(memo_files)}개")

    # ── Step 2: Fusion Gate ────────────────────────────────
    if not fusion_gate(readwise_files, memo_files):
        msg = (
            f"🚫 아침 융합 중단 [{now.strftime('%H:%M')}]\n"
            f"이질적 융합 조건 미충족 — Readwise {len(readwise_files)}개, 메모 {len(memo_files)}개\n"
            f"단일 군집 요약 금지 원칙 적용."
        )
        print(f"  {msg}")
        if not dry_run:
            send_webhook(msg)
        return

    # ── Step 3: 프롬프트 빌드 + Gemini 호출 ───────────────
    prompt = build_prompt(readwise_files, memo_files)
    print(f"  🔄 Gemini 호출 중...")
    result = call_gemini(prompt)
    if not result:
        return

    # ── Step 4: 압축 ───────────────────────────────────────
    result = compress_report(result, limit=REPORT_LIMIT)

    # ── Step 5: 구글챗 포맷 ────────────────────────────────
    result = format_for_google_chat(result)

    # ── Step 6: dry-run 미리보기 (앞 15줄 + 뒤 10줄) ───────
    lines = result.split('\n')
    if dry_run:
        print(f"\n{'─'*50}")
        print(f"[DRY-RUN 미리보기] 총 {len(result)}자 / {len(lines)}줄")
        print(f"{'─'*50}")
        print('\n'.join(lines[:15]))
        if len(lines) > 25:
            print('\n  ...(중략)...\n')
            print('\n'.join(lines[-10:]))
        elif len(lines) > 15:
            print('\n'.join(lines[15:]))
        print(f"{'─'*50}")
        return

    # ── Step 7: 저장 ───────────────────────────────────────
    out_name = f"{date_tag}_Zettelkasten_{time_tag}_아침융합리포트v2.txt"
    out_path = os.path.join(ARCHIVE, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"  ✅ 저장: {out_name} ({len(result)}자)")

    # ── Step 8: Webhook 발송 ───────────────────────────────
    preview = '\n'.join(lines[:8])
    msg = (
        f"🌅 아침 융합 리포트 v2 [{now.strftime('%H:%M')}]\n"
        f"소스: Readwise {len(readwise_files)}개 × 메모 {len(memo_files)}개\n"
        f"글자 수: {len(result)}자 / 파일: {out_name}\n\n"
        f"{preview}…"
    )
    send_webhook(msg + FOOTER)
    print(f"  📡 Webhook 전송")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
