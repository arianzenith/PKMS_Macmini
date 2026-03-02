import os, shutil, time, json
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
ARCHIVE        = os.path.join(BASE_DIR, "02_Archive")
SOURCES        = os.path.join(ARCHIVE, "sources")
MODEL_ID       = "gemini-2.5-pro"
POLL_INTERVAL  = 300  # 5분

if not GOOGLE_API_KEY:
    print(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")
    exit(1)

os.makedirs(SOURCES, exist_ok=True)
client = genai.Client(api_key=GOOGLE_API_KEY)

# ── Webhook ────────────────────────────────────────────────
def send_webhook(text: str):
    if not WEBHOOK_URL:
        return
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req  = urllib_request.Request(
            WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib_request.urlopen(req, timeout=10)
    except URLError as e:
        print(f"  ⚠️ Webhook 실패: {e}")

# ── 분석 + 저장 ────────────────────────────────────────────
def analyze(title: str, body: str, source: str) -> str | None:
    now       = datetime.now()
    date_tag  = now.strftime("%y%m%d")
    time_tag  = now.strftime("%H%M%S")
    out_name  = f"{date_tag}_Zettelkasten_{time_tag}.txt"

    prompt = (
        f"다음 내용을 제텔카스텐 방식으로 분석해줘.\n"
        f"영구 노트(Permanent Note) 형식으로, 핵심 아이디어를 자신의 언어로 재서술하고 "
        f"연결 가능한 개념을 명시해.\n\n"
        f"제목: {title}\n\n{body}"
    )

    for attempt in range(3):
        try:
            print(f"  🔄 [{source}] '{title}' 분석 중...")
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)

            with open(os.path.join(ARCHIVE, out_name), "w", encoding="utf-8") as f:
                f.write(f"# {title}\n출처: {source}\n\n{resp.text}")

            print(f"  ✅ 저장: {out_name}")
            return out_name

        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                print(f"  ⚠️ 429 한도 초과 — {wait}초 대기...")
                time.sleep(wait)
            else:
                print(f"  ❌ 실패: {e}")
                return None

    return None

def detect_source(fname: str) -> str:
    """파일명 키워드로 소스 구분 (하위 폴더 없음)"""
    upper = fname.upper()
    if "READWISE" in upper:
        return "Readwise"
    if "APPLENOTES" in upper or "APPLE_NOTES" in upper:
        return "AppleNotes"
    return "Unknown"

# ── 01_Inbox 루트 처리 ─────────────────────────────────────
def process_inbox() -> list:
    results = []
    if not os.path.isdir(INBOX):
        return results

    for fname in sorted(f for f in os.listdir(INBOX)
                        if os.path.isfile(os.path.join(INBOX, f))
                        and f.endswith((".md", ".txt"))):
        fpath  = os.path.join(INBOX, fname)
        title  = os.path.splitext(fname)[0]
        source = detect_source(fname)

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                body = f.read().strip()
        except Exception as e:
            print(f"  ❌ 읽기 실패 {fname}: {e}")
            continue

        if not body:
            continue

        out = analyze(title, body, source)
        if out:
            shutil.move(fpath, os.path.join(SOURCES, fname))
            results.append(out)
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
        send_webhook(msg)
        print(f"  📡 Webhook 전송")
    else:
        print("  ─ 신규 항목 없음")

# ── 진입점 (1회 실행 후 종료 — cron이 스케줄 담당) ──────────
if __name__ == "__main__":
    run_cycle()
