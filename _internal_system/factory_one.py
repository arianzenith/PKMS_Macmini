import gc, os, re, shutil, time, json, glob
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError
from google import genai
from dotenv import load_dotenv

# ── 경로 설정 ──────────────────────────────────────────────
BASE_DIR  = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH  = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL       = os.getenv("WEBHOOK_URL")
INBOX             = os.path.join(BASE_DIR, "01_Inbox")
AI_INBOX          = os.path.join(INBOX, "AI_Inbox")
ARCHIVE           = os.path.join(BASE_DIR, "02_Archive")
SOURCES           = os.path.join(ARCHIVE, "sources")
MODEL_ID          = "gemini-2.5-pro"
CONTEXT_THRESHOLD = 7   # 맥락 점수 ≥ 7 이면 즉시 웹훅

# ── 파일 형식 분류 ─────────────────────────────────────────
MIME_MAP = {
    ".pdf":  "application/pdf",
    ".mp3":  "audio/mpeg",
    ".m4a":  "audio/mp4",
    ".wav":  "audio/wav",
    ".qta":  "audio/mp4",      # Heptabase 오디오 → mp4 스트림
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
}
FILE_API_EXTS  = set(MIME_MAP.keys())
TEXT_EXTS      = {".txt", ".md", ".csv"}
OFFICE_EXTS    = {".xlsx", ".docx"}
SUPPORTED_EXTS = FILE_API_EXTS | TEXT_EXTS | OFFICE_EXTS

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
        return "A"
    if "APPLENOTES" in upper or "APPLE_NOTES" in upper:
        return "B"
    if "HEPTABASE" in upper:
        return "B"
    return "C"


def source_label(source_type: str) -> str:
    return {
        "A": "Readwise·외부지성",
        "B": "메모·내부생각",
        "C": "업무파일·현장데이터",
    }.get(source_type, "기타")


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
                fn = os.path.basename(fpath)
                names.append(fn)
                contents.append(f"[{fn}]\n{content[:max_chars_per_file]}")
            except Exception:
                continue
        return names, "\n\n".join(contents)

    a_names, a_text = read(a_files)
    b_names, b_text = read(b_files)
    return a_names, a_text, b_names, b_text


# ── File API 업로드 ─────────────────────────────────────────
def upload_to_gemini(fpath: str, mime_type: str):
    """파일 업로드 후 ACTIVE 상태 대기. 실패 시 None 반환."""
    # ── 업로드 (OSError 11 데드락 재시도) ─────────────────────
    uploaded = None
    for attempt in range(3):
        try:
            print(f"  📤 File API 업로드 중... ({mime_type})")
            with open(fpath, "rb") as fh:
                uploaded = client.files.upload(file=fh, config={"mime_type": mime_type})
            break
        except OSError as e:
            if e.errno == 11:   # EDEADLK — Resource deadlock avoided
                gc.collect()
                wait = 1 * (attempt + 1)
                print(f"  ⚠️ OSError 11 — {wait}초 후 재시도 ({attempt + 1}/3)")
                time.sleep(wait)
            else:
                msg = f"❌ File API 업로드 오류 [{datetime.now().strftime('%H:%M')}]\n{str(e)}"
                print(f"  {msg}")
                send_webhook(msg)
                return None
        except Exception as e:
            msg = f"❌ File API 업로드 오류 [{datetime.now().strftime('%H:%M')}]\n{str(e)}"
            print(f"  {msg}")
            send_webhook(msg)
            return None

    if uploaded is None:
        msg = f"❌ File API 업로드 3회 실패 (OSError 11): {fpath}"
        print(f"  {msg}")
        send_webhook(msg)
        return None

    # ── ACTIVE 상태 대기 ──────────────────────────────────────
    for _ in range(30):
            fi = client.files.get(name=uploaded.name)
            if fi.state.name == "ACTIVE":
                print(f"  ✅ File API ACTIVE: {fi.name}")
                return fi
            if fi.state.name == "FAILED":
                print(f"  ❌ File API 처리 실패: {fpath}")
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:
                    pass
                return None
            time.sleep(2)
        print(f"  ❌ File API 타임아웃: {fpath}")
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass
        return None
    except Exception as e:
        msg = f"❌ File API 업로드 오류 [{datetime.now().strftime('%H:%M')}]\n{str(e)}"
        print(f"  {msg}")
        send_webhook(msg)
        return None


# ── 텍스트 추출 (txt/md/csv/xlsx/docx) ─────────────────────
def extract_text(fpath: str, ext: str) -> str:
    if ext in TEXT_EXTS:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"  ❌ 텍스트 읽기 실패: {e}")
            return ""

    if ext == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(fpath, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"[시트: {ws.title}]")
                for row in ws.iter_rows(values_only=True):
                    line = "\t".join(str(v) if v is not None else "" for v in row)
                    if line.strip():
                        lines.append(line)
            return "\n".join(lines).strip()
        except ImportError:
            print("  ⚠️ openpyxl 미설치. pip install openpyxl")
            return ""
        except Exception as e:
            print(f"  ❌ xlsx 읽기 실패: {e}")
            return ""

    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(fpath)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip()).strip()
        except ImportError:
            print("  ⚠️ python-docx 미설치. pip install python-docx")
            return ""
        except Exception as e:
            print(f"  ❌ docx 읽기 실패: {e}")
            return ""

    return ""


# ── 맥락 점수 파싱 ────────────────────────────────────────
def parse_context_score(text: str) -> int:
    """Gemini 응답에서 '맥락 형성 점수: X/10' 추출."""
    m = re.search(r'맥락\s*형성\s*점수\s*[:：]\s*(\d+)\s*/\s*10', text)
    if m:
        return min(int(m.group(1)), 10)
    return 0


# ── 융합 프롬프트 빌더 ────────────────────────────────────
FUSION_RULES = """[절대 규칙 — 위반 시 실패]
1. 핵심 임무: 소스들 사이의 연결고리를 찾아라 — 서로를 보완하거나 반박하는 지점.
2. 연결고리 없으면 억지로 만들지 마라: '융합 불가 — 독립 처리 권장' 선언 후 맥락 점수 0 기재.
3. 두루뭉술함 금지: 단순 요약, 뻔한 연결 완전 배제. 날카로운 충돌과 가설만 허용.
4. 형식적 갯수 채우기 금지: 통찰 강도 낮으면 단 1개만 출력.
5. 제로 마크다운: 볼드체(**), 이탤릭(*), 구분선(───) 등 모든 강조 기호 완전 제거.
6. 제로 구분선: 줄바꿈으로만 섹션 구분.
7. 글자 수: 2,500~3,500자 이내.
8. 사유 금지: 반드시 생각으로 치환.
9. 출처 섹션 필수: 생략 시 미완성본.
10. 마지막 줄: 반드시 '맥락 형성 점수: X/10' 형식으로 기재. (7 이상 = 새로운 맥락 형성)"""


def build_fusion_prompt(sources: list, a_names=None, a_text="", b_names=None, b_text="") -> str:
    n = len(sources)
    source_list = "\n".join(
        f"• {s['title']} ({source_label(s['source_type'])})" for s in sources
    )

    # 텍스트 소스 본문 (최대 2000자씩)
    text_blocks = []
    for s in sources:
        if s["body"]:
            text_blocks.append(
                f"[{s['title']} — {source_label(s['source_type'])}]\n{s['body'][:2000]}"
            )

    file_count = sum(1 for s in sources if s["file_obj"])
    file_note  = f"\n첨부 파일 {file_count}개는 위에 직접 첨부됨." if file_count else ""

    # C 소스 존재 시 A/B 아카이브 컨텍스트 추가
    archive_section = ""
    if any(s["source_type"] == "C" for s in sources) and (a_text or b_text):
        archive_section = (
            f"\n\n과거 지식 아카이브 (참고용):\n"
            f"Source A (Readwise):\n{a_text or '(없음)'}\n\n"
            f"Source B (메모):\n{b_text or '(없음)'}"
        )

    body = "\n\n".join(text_blocks)

    return (
        f"당신은 생각공장의 이질적 소스 융합 엔진입니다.\n"
        f"아래 {n}개의 서로 다른 소스를 동시에 분석하여 제텔카스텐 융합 노드를 생성하세요.{file_note}\n\n"
        f"{FUSION_RULES}\n\n"
        f"[출력 순서 엄수]\n\n"
        f"공장장님, {n}개의 이질적 소스에서 연결고리를 찾았습니다.\n\n"
        f"📚 투입 소스\n{source_list}\n\n"
        f"1. 거대 가설 (최대 3개, 통찰 강도 낮으면 1개만)\n"
        f"소스들을 관통하는 날카로운 가설. 각 소스가 이 가설을 어떻게 지지하거나 반박하는지 명시.\n\n"
        f"2. 충돌·보완 지점 (최대 3개)\n"
        f"소스들이 서로를 보완하거나 반박하는 구체적 지점. 연결 없으면 생략.\n\n"
        f"3. 창발 아이디어 (최대 3개)\n"
        f"충돌과 가설에서 도출된 구체적 실행 방안. 오늘 바로 실행할 단 한 가지 반드시 포함.\n\n"
        f"10. 파괴적 질문\n"
        f"이 융합에서 가장 파괴력이 큰 지점을 골라 공장장의 허를 찌르는 단 하나의 질문.\n\n"
        f"맥락 형성 점수: X/10\n"
        f"(이 줄을 실제 점수로 채워라. 7 이상 = 새로운 맥락 형성 확인)\n\n"
        f"---\n"
        f"{body}"
        f"{archive_section}"
    )


# ── Gemini 호출 ──────────────────────────────────────────
def call_gemini(contents, label: str) -> str | None:
    """contents: str (텍스트) 또는 list [file_obj..., prompt_str] (File API)"""
    for attempt in range(3):
        try:
            print(f"  🔄 [{label}] Gemini 분석 중... (시도 {attempt + 1})")
            resp = client.models.generate_content(model=MODEL_ID, contents=contents)
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


# ── 파일 수집 (01_Inbox 루트 + AI_Inbox) ──────────────────
def collect_inbox_files() -> list[tuple[str, str, str]]:
    """(fpath, fname, inbox_dir) 튜플 목록 반환"""
    items = []
    if os.path.isdir(INBOX):
        for fname in sorted(os.listdir(INBOX)):
            fpath = os.path.join(INBOX, fname)
            ext   = os.path.splitext(fname)[1].lower()
            if os.path.isfile(fpath) and ext in SUPPORTED_EXTS:
                items.append((fpath, fname, INBOX))
    if os.path.isdir(AI_INBOX):
        for fname in sorted(os.listdir(AI_INBOX)):
            fpath = os.path.join(AI_INBOX, fname)
            ext   = os.path.splitext(fname)[1].lower()
            if os.path.isfile(fpath) and ext in SUPPORTED_EXTS:
                items.append((fpath, fname, AI_INBOX))
    return items


# ── 01_Inbox 배치 처리 ─────────────────────────────────────
def process_inbox() -> dict | None:
    """
    inbox의 모든 파일을 한꺼번에 Gemini에 투입.
    반환: {'out_name': str, 'score': int, 'n': int} 또는 None
    """
    import unicodedata

    all_files = collect_inbox_files()
    if not all_files:
        return None

    # C 소스 존재 시 A/B 아카이브 로드
    has_c = any(
        detect_source(fname) == "C" or inbox_dir == AI_INBOX
        for _, fname, inbox_dir in all_files
    )
    a_names, a_text, b_names, b_text = [], "", [], ""
    if has_c:
        a_names, a_text, b_names, b_text = fetch_archive_ab()

    # ── 각 파일 콘텐츠 준비 ──────────────────────────────
    sources      = []   # 성공적으로 준비된 소스
    file_objs    = []   # 나중에 삭제할 File API 오브젝트
    actual_paths = []   # move 용 (actual_fpath, actual_fname) 쌍

    for fpath, fname, inbox_dir in all_files:
        ext    = os.path.splitext(fname)[1].lower()
        title  = os.path.splitext(fname)[0]
        source = "C" if inbox_dir == AI_INBOX else detect_source(fname)

        # macOS NFC/NFD 한글 파일명 인코딩 대응
        actual_fname = None
        for f in os.listdir(inbox_dir):
            if unicodedata.normalize("NFC", f) == unicodedata.normalize("NFC", fname):
                actual_fname = f
                break
        if not actual_fname:
            print(f"  ❌ 파일 없음 {fname}")
            continue
        actual_fpath = os.path.join(inbox_dir, actual_fname)

        if ext in FILE_API_EXTS:
            file_obj = upload_to_gemini(actual_fpath, MIME_MAP[ext])
            if not file_obj:
                continue
            sources.append({
                "fname": fname, "title": title,
                "source_type": source,
                "file_obj": file_obj, "body": None,
            })
            file_objs.append(file_obj)
        else:
            body = extract_text(actual_fpath, ext)
            if not body:
                print(f"  ⚠️ 텍스트 추출 실패 또는 빈 파일: {fname}")
                continue
            sources.append({
                "fname": fname, "title": title,
                "source_type": source,
                "file_obj": None, "body": body,
            })

        actual_paths.append((actual_fpath, actual_fname))

    if not sources:
        return None

    # ── 단일 Gemini 호출 ──────────────────────────────────
    prompt_text = build_fusion_prompt(sources, a_names, a_text, b_names, b_text)
    contents    = file_objs + [prompt_text] if file_objs else prompt_text
    n_label     = f"{len(sources)}개소스융합"

    result = call_gemini(contents, n_label)

    # File API 임시 파일 삭제
    for fo in file_objs:
        try:
            client.files.delete(name=fo.name)
        except Exception:
            pass

    if not result:
        return None

    # ── 저장 ──────────────────────────────────────────────
    out_name = save_zettelkasten(result, n_label)

    # ── 원본 → sources/ 이동 ──────────────────────────────
    for actual_fpath, actual_fname in actual_paths:
        try:
            shutil.move(actual_fpath, os.path.join(SOURCES, actual_fname))
        except Exception as e:
            print(f"  ⚠️ 파일 이동 실패 {actual_fname}: {e}")

    # ── 맥락 점수 파싱 ────────────────────────────────────
    score = parse_context_score(result)
    print(f"  🎯 맥락 형성 점수: {score}/10")

    return {"out_name": out_name, "score": score, "n": len(sources)}


# ── 메인 사이클 ────────────────────────────────────────────
def run_cycle():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts}] ── 수집 사이클 시작")

    outcome = process_inbox()

    if outcome is None:
        print("  ─ 신규 항목 없음")
        return

    score    = outcome["score"]
    n        = outcome["n"]
    out_name = outcome["out_name"]
    footer   = (
        "\n\n🔗 <https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1|제텔카스텐 전략실 바로가기>"
        "\n📂 <https://drive.google.com/drive/u/1/folders/1TmwPlc6JCtYbSwXzRonI3BeehLX059Vg|오늘자 원문 (Google Drive)>"
    )

    if score >= CONTEXT_THRESHOLD:
        msg = (
            f"🔗 융합 노드 생성 [{datetime.now().strftime('%H:%M')}]\n"
            f"소스 {n}개 → 맥락 점수 {score}/10\n"
            f"파일: {out_name}"
        )
        send_webhook(msg + footer)
        print(f"  📡 Webhook 전송 (맥락 점수 {score} ≥ {CONTEXT_THRESHOLD})")
    else:
        print(f"  📁 맥락 점수 {score}/10 — 아침 8시 리포트에 포함")


if __name__ == "__main__":
    run_cycle()
