import os, json, glob, time
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
MODEL_ID       = "gemini-2.5-pro"
_TOTAL_LIMIT   = 3500

if not GOOGLE_API_KEY:
    print(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")
    exit(1)

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


# ── 어제 Archive 파일 로드 ──────────────────────────────────
def load_archive_files(prefix: str) -> list[dict]:
    files = sorted(glob.glob(os.path.join(ARCHIVE, f"{prefix}_Zettelkasten_*.txt")))
    result = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            source = "Unknown"
            for line in content.splitlines():
                if line.startswith("출처:"):
                    source = line.replace("출처:", "").strip()
                    break
            result.append({
                "fname":   os.path.basename(fpath),
                "source":  source,
                "content": content,
            })
        except Exception as e:
            print(f"  ❌ 읽기 실패 {fpath}: {e}")
    return result


# ── 5:5 소스 분리 ──────────────────────────────────────────
def split_sources(files: list[dict]) -> tuple[list[dict], list[dict]]:
    readwise = [f for f in files if f["source"] == "Readwise"]
    memo     = [f for f in files if f["source"] != "Readwise"]
    return readwise, memo


# ── 프롬프트 빌드 ───────────────────────────────────────────
def build_prompt(readwise_files: list[dict], memo_files: list[dict]) -> str:
    readwise_block = "\n\n".join(f["content"] for f in readwise_files) or "(없음)"
    memo_block     = "\n\n".join(f["content"] for f in memo_files)     or "(없음)"
    source_list    = "\n".join(f"- {f['fname']}" for f in readwise_files + memo_files)

    return f"""아래 두 그룹의 지식 노트를 5:5 비율로 융합한 아침 리포트를 작성하라.

절대 규칙:
1. 두루뭉술한 표현 금지. 모든 문장은 구체적 사실이나 논리적 주장이어야 함.
2. 억지 조합 금지. 두 소스 사이에 실제 긴장·충돌·보완 관계가 없으면 억지로 연결하지 말고 솔직히 분리해서 서술.
3. 형식적 갯수 채우기 금지. 섹션 수·항목 수를 맞추기 위한 내용 없는 문장 금지.
4. 마크다운 완전 금지. #, ##, **, __, - 기호 사용 금지.
5. 구분선 완전 금지. ---, ***, === 사용 금지.
7. '사유' 대신 '생각'을 사용할 것.
8. 마지막에 출처 섹션을 반드시 포함할 것. 아래 소스 목록을 그대로 사용.

글자 수: 2,500자 이상 3,500자 이하. 이 범위를 벗어나면 실패.

파괴적 질문: 리포트 끝, 출처 섹션 앞에 이 융합에서 도출한 맞춤형 질문 1개를 제시. 고정 문구·형식 사용 금지.

출처:
{source_list}

[Source A — Readwise]
{readwise_block}

[Source B — 메모]
{memo_block}
"""


# ── Gemini 호출 (429 재시도 + 에러 즉시 웹훅) ──────────────
def call_gemini(prompt: str) -> str | None:
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            return resp.text
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                msg  = f"⚠️ 아침 융합 429 한도 초과 — {wait}초 대기 후 재시도 ({attempt+1}/3)"
                print(f"  {msg}")
                send_webhook(msg)
                time.sleep(wait)
            else:
                msg = f"❌ 아침 융합 오류: {e}"
                print(f"  {msg}")
                send_webhook(msg)
                return None
    msg = "❌ 아침 융합 3회 재시도 실패. 수동 확인 요망."
    send_webhook(msg)
    return None


# ── 메인 ──────────────────────────────────────────────────
def run():
    now       = datetime.now()
    yesterday = now - timedelta(days=1)
    prefix    = yesterday.strftime("%y%m%d")
    date_tag  = now.strftime("%y%m%d")
    time_tag  = now.strftime("%H%M%S")

    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] ── 아침 융합 리포트 시작")

    files = load_archive_files(prefix)
    if not files:
        msg = f"📋 아침 보고 [{now.strftime('%Y-%m-%d %H:%M')}]\n어제({prefix}) 생산된 노트 없음."
        print(msg)
        send_webhook(msg)
        return

    readwise_files, memo_files = split_sources(files)
    print(f"  Readwise: {len(readwise_files)}개 / 메모: {len(memo_files)}개")

    prompt = build_prompt(readwise_files, memo_files)
    result = call_gemini(prompt)
    if not result:
        return

    # 3500자 초과 시 절단
    if len(result) > _TOTAL_LIMIT:
        result = result[:_TOTAL_LIMIT]

    out_name = f"{date_tag}_Zettelkasten_{time_tag}_아침융합리포트.txt"
    out_path = os.path.join(ARCHIVE, out_name)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"  ✅ 저장: {out_name} ({len(result)}자)")

    preview = result[:300].replace("\n", " ")
    msg = (
        f"🌅 아침 융합 리포트 [{now.strftime('%H:%M')}]\n"
        f"소스: Readwise {len(readwise_files)}개 + 메모 {len(memo_files)}개\n"
        f"글자 수: {len(result)}자\n"
        f"파일: {out_name}\n\n"
        f"{preview}…"
    )
    footer = "\n\n🔗 <https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1|제텔카스텐 전략실 바로가기>\n📂 <https://drive.google.com/drive/u/1/folders/1TmwPlc6JCtYbSwXzRonI3BeehLX059Vg|오늘자 원문 (Google Drive)>"
    send_webhook(msg + footer)
    print(f"  📡 Webhook 전송")


if __name__ == "__main__":
    run()
