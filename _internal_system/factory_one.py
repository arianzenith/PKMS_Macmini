import os, shutil, time, json, glob
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError
from google import genai
from dotenv import load_dotenv

# ── 경로 설정 ──────────────────────────────────────────────
BASE_DIR  = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH  = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
INBOX          = os.path.join(BASE_DIR, "01_Inbox")
AI_INBOX       = os.path.join(INBOX, "AI_Inbox")   # 회사GD → 개인GD 업무파일 경로
ARCHIVE        = os.path.join(BASE_DIR, "02_Archive")
SOURCES        = os.path.join(ARCHIVE, "sources")
MODEL_ID       = "gemini-2.5-pro"

if not GOOGLE_API_KEY:
    print(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")
    exit(1)

os.makedirs(SOURCES, exist_ok=True)
client = genai.Client(api_key=GOOGLE_API_KEY)

# ── Webhook ────────────────────────────────────────────────
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
        try:
            err_data = json.dumps({"text": f"⚠️ 생각공장 에러 [{datetime.now().strftime('%H:%M')}]\n{str(e)}"}).encode("utf-8")
            err_req  = urllib_request.Request(WEBHOOK_URL, data=err_data, headers={"Content-Type": "application/json"}, method="POST")
            urllib_request.urlopen(err_req, timeout=10)
        except Exception:
            pass

# ── 소스 유형 판별 ─────────────────────────────────────────
def detect_source(fname: str) -> str:
    upper = fname.upper()
    if "READWISE" in upper:
        return "A"   # 외부 지성
    if "APPLENOTES" in upper or "APPLE_NOTES" in upper:
        return "B"   # 내부 생각
    if "HEPTABASE" in upper:
        return "B"   # 내부 생각
    return "C"       # 업무파일 (현장 데이터)

# ── 아카이브에서 최근 A/B 소스 가져오기 ───────────────────
def fetch_archive_ab(max_chars_per_file: int = 600) -> tuple:
    a_files = sorted(glob.glob(os.path.join(SOURCES, "*_Readwise_*.txt")))[-5:]
    b_files = sorted(
        glob.glob(os.path.join(SOURCES, "*_AppleNotes_*.txt")) +
        glob.glob(os.path.join(SOURCES, "*_Heptabase_*.txt"))
    )[-5:]

    def read(files):
        names, contents = [], []
        for fpath in files:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                fname = os.path.basename(fpath)
                names.append(fname)
                contents.append(f"[{fname}]\n{content[:max_chars_per_file]}")
            except Exception:
                continue
        return names, "\n\n".join(contents)

    a_names, a_text = read(a_files)
    b_names, b_text = read(b_files)
    return a_names, a_text, b_names, b_text

# ── 프롬프트 빌더 ──────────────────────────────────────────
def build_prompt_ab(title: str, body: str) -> str:
    """Source A/B 단독 입고 — 개별 제텔카스텐 생성"""
    return f"""당신은 생각공장의 지식 제련 엔진입니다. 아래 소스를 분석하여 제텔카스텐 융합 인사이트 리포트를 작성하세요.

[절대 규칙 — 위반 시 실패]
1. 두루뭉술함 금지: 단순 요약, 뻔한 연결 완전 배제. 날카로운 충돌과 가설만 허용.
2. 억지 조합 금지: 논리적 충돌이나 날카로운 연결고리 없으면 과감히 생략. 양보다 질 우선.
3. 형식적 갯수 채우기 금지: 3:3:3:1은 가이드일 뿐. 통찰 강도 낮으면 단 1개만 출력.
4. 제로 마크다운: 볼드체(**), 이탤릭(*), 구분선(───) 등 모든 강조 기호 완전 제거.
5. 제로 구분선: 줄바꿈으로만 섹션 구분.
6. 글자 수: 반드시 2,500~3,500자 이내 단일 메시지.
7. 사유 금지: 반드시 생각으로 치환.
8. 출처 섹션 필수: 생략 시 미완성본으로 간주.

[출력 순서 엄수]

공장장님, 오늘 이 생각은 어떠세요? 제텔카스텐의 새로운 조각이 도착했습니다.

📚 오늘의 출처
• {title}

1. 거대 가설 (최대 3개, 통찰 강도 낮으면 1개만)
전체 소스를 관통하는 날카로운 가설. 공장장의 비즈니스/삶에 미칠 단기·장기 영향 반드시 포함.

2. 충돌 지점 (최대 3개)
이 소스 내의 모순, 혹은 기존 지식과의 시각 차이를 비판적으로 대조. 상충 관점 없으면 생략.

3. 창발 아이디어 (최대 3개)
가설과 충돌에서 도출된 구체적 실행 방안. 오늘 바로 실행할 단 한 가지 반드시 포함.

10. 파괴적 질문
오늘 리포트에서 가장 파괴력이 큰 지점을 골라, 공장장의 허를 찌르는 단 하나의 맞춤형 질문. 고정 문구 절대 금지.

---
소스 내용:
{body}
"""

def build_prompt_c(title: str, body: str, a_names, a_text, b_names, b_text) -> str:
    """Source C 업무파일 입고 — A/B 아카이브와 5:5 융합"""
    all_sources = [title] + a_names + b_names
    source_list = "\n".join(f"• {n}" for n in all_sources[:10])

    return f"""당신은 생각공장의 실시간 업무 융합 엔진입니다. 업무 파일(Source C)과 과거 지식 아카이브(Source A/B)를 5:5로 충돌시켜 제텔카스텐 융합 인사이트 리포트를 작성하세요.

[절대 규칙 — 위반 시 실패]
1. 두루뭉술함 금지: 단순 요약, 뻔한 연결 완전 배제. 날카로운 충돌과 가설만 허용.
2. 억지 조합 금지: 논리적 충돌이나 날카로운 연결고리 없으면 과감히 생략. 양보다 질 우선.
3. 형식적 갯수 채우기 금지: 3:3:3:1은 가이드일 뿐. 통찰 강도 낮으면 단 1개만 출력.
4. 제로 마크다운: 볼드체(**), 이탤릭(*), 구분선(───) 등 모든 강조 기호 완전 제거.
5. 제로 구분선: 줄바꿈으로만 섹션 구분.
6. 글자 수: 반드시 2,500~3,500자 이내 단일 메시지.
7. 사유 금지: 반드시 생각으로 치환.
8. 출처 섹션 필수: 생략 시 미완성본으로 간주.

[출력 순서 엄수]

공장장님, 오늘 이 생각은 어떠세요? 제텔카스텐의 새로운 조각이 도착했습니다.

📚 오늘의 출처
{source_list}

1. 거대 가설 (최대 3개, 통찰 강도 낮으면 1개만)
업무 현실(Source C)과 과거 지식(Source A/B)을 관통하는 날카로운 가설. 공장장의 비즈니스/삶에 미칠 단기·장기 영향 반드시 포함.

2. 충돌 지점 (최대 3개)
Source C(업무 현실)와 Source A/B(외부 지성 + 내부 생각)의 모순과 시각 차이를 비판적으로 대조. 5:5 충돌 필수. 상충 관점 없으면 생략.

3. 창발 아이디어 (최대 3개)
충돌과 가설에서 도출된 구체적 실행 방안. 오늘 바로 실행할 단 한 가지 반드시 포함.

10. 파괴적 질문
오늘 리포트에서 가장 파괴력이 큰 지점을 골라, 공장장의 허를 찌르는 단 하나의 맞춤형 질문. 고정 문구 절대 금지.

---
Source C (업무파일 — 현장 데이터 50%):
[{title}]
{body}

Source A (외부 지성 — Readwise 아카이브 25%):
{a_text}

Source B (내부 생각 — 메모 아카이브 25%):
{b_text}
"""

# ── Gemini 호출 ────────────────────────────────────────────
def call_gemini(prompt: str, label: str) -> str | None:
    for attempt in range(3):
        try:
            print(f"  🔄 [{label}] Gemini 분석 중... (시도 {attempt + 1})")
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            return resp.text
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"  ⚠️ 429 한도 초과 — {wait}초 대기...")
                send_webhook(f"⚠️ 생각공장 API 한도 [{datetime.now().strftime('%H:%M')}]\n{wait}초 후 자동 재시도합니다.")
                time.sleep(wait)
            else:
                print(f"  ❌ 실패: {e}")
                send_webhook(f"❌ 생각공장 에러 [{datetime.now().strftime('%H:%M')}]\n{str(e)}")
                return None
    return None

# ── 저장 ───────────────────────────────────────────────────
def save_zettelkasten(content: str, label: str) -> str:
    date_tag   = datetime.now().strftime("%y%m%d")
    time_tag   = datetime.now().strftime("%H%M%S")
    safe_label = "".join(c for c in label if c.isalnum() or c in " _-가-힣")[:30].strip()
    out_name   = f"{date_tag}_Zettelkasten_{time_tag}_{safe_label}.txt"
    out_path   = os.path.join(ARCHIVE, out_name)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  ✅ 저장: {out_name}")
    return out_name

# ── PDF 텍스트 추출 ────────────────────────────────────────
def read_pdf(fpath: str) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(fpath) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except ImportError:
        print("  ⚠️ pdfplumber 미설치. pip install pdfplumber")
        return ""
    except Exception as e:
        print(f"  ❌ PDF 읽기 실패: {e}")
        return ""


# ── 파일 수집 (루트 + AI_Inbox) ────────────────────────────
def collect_inbox_files() -> list[tuple[str, str, str]]:
    """(fpath, fname, inbox_dir) 튜플 목록 반환"""
    import unicodedata
    items = []

    # 01_Inbox 루트: .md / .txt
    if os.path.isdir(INBOX):
        for fname in sorted(os.listdir(INBOX)):
            fpath = os.path.join(INBOX, fname)
            if os.path.isfile(fpath) and fname.endswith((".md", ".txt")):
                items.append((fpath, fname, INBOX))

    # AI_Inbox 서브폴더: .md / .txt / .pdf (항상 Source C)
    if os.path.isdir(AI_INBOX):
        for fname in sorted(os.listdir(AI_INBOX)):
            fpath = os.path.join(AI_INBOX, fname)
            if os.path.isfile(fpath) and fname.endswith((".md", ".txt", ".pdf")):
                items.append((fpath, fname, AI_INBOX))

    return items


# ── 01_Inbox 처리 ──────────────────────────────────────────
def process_inbox() -> list:
    import unicodedata

    all_files = collect_inbox_files()
    if not all_files:
        return []

    # Source C 파일이 있으면 A/B 아카이브 미리 로드
    c_exists = any(detect_source(fname) == "C" or inbox_dir == AI_INBOX
                   for _, fname, inbox_dir in all_files)
    a_names, a_text, b_names, b_text = [], "", [], ""
    if c_exists:
        a_names, a_text, b_names, b_text = fetch_archive_ab()

    results = []
    for fpath, fname, inbox_dir in all_files:
        title  = os.path.splitext(fname)[0]
        # AI_Inbox 파일은 항상 Source C
        source = "C" if inbox_dir == AI_INBOX else detect_source(fname)

        # macOS NFC/NFD 한글 파일명 인코딩 대응
        actual_fname = None
        for f in os.listdir(inbox_dir):
            if unicodedata.normalize('NFC', f) == unicodedata.normalize('NFC', fname):
                actual_fname = f
                break
        if not actual_fname:
            print(f"  ❌ 파일 없음 {fname}")
            continue
        actual_fpath = os.path.join(inbox_dir, actual_fname)

        # 파일 읽기 (PDF / 텍스트 분기)
        if fname.lower().endswith(".pdf"):
            body = read_pdf(actual_fpath)
        else:
            try:
                with open(actual_fpath, "r", encoding="utf-8") as f:
                    body = f.read().strip()
            except Exception as e:
                print(f"  ❌ 읽기 실패 {fname}: {e}")
                continue

        if not body:
            continue

        if source == "C":
            prompt = build_prompt_c(title, body, a_names, a_text, b_names, b_text)
        else:
            prompt = build_prompt_ab(title, body)

        result = call_gemini(prompt, title)
        if result:
            out_name = save_zettelkasten(result, title)
            try:
                shutil.move(actual_fpath, os.path.join(SOURCES, actual_fname))
            except Exception as e:
                print(f"  ⚠️ 파일 이동 실패 {fname}: {e}")
            results.append(out_name)
            time.sleep(1)

    return results

# ── 메인 사이클 ────────────────────────────────────────────
def run_cycle():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] ── 수집 사이클 시작")

    all_done = process_inbox()

    if all_done:
        msg = (
            f"✅ 공장 보고 [{datetime.now().strftime('%H:%M')}]\n"
            f"처리 완료 {len(all_done)}개\n"
            + "\n".join(f"  • {f}" for f in all_done)
        )
        footer = "\n\n🔗 <https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1|제텔카스텐 전략실 바로가기>\n📂 <https://drive.google.com/drive/u/1/folders/1TmwPlc6JCtYbSwXzRonI3BeehLX059Vg|오늘자 원문 (Google Drive)>"
        send_webhook(msg + footer)
        print(f"  📡 Webhook 전송")
    else:
        print("  ─ 신규 항목 없음")

if __name__ == "__main__":
    run_cycle()
