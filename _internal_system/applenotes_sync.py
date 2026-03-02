import os, json, time, subprocess, re
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError
from dotenv import load_dotenv

# ── 설정 ───────────────────────────────────────────────────
BASE_DIR    = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH    = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
INBOX       = os.path.join(BASE_DIR, "01_Inbox")
STATE_FILE  = os.path.join(BASE_DIR, "_internal_system/pkms/applenotes_last_sync.txt")
FOLDER_NAME = "00_생각공장"

os.makedirs(INBOX, exist_ok=True)


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


# ── 상태 파일 ──────────────────────────────────────────────
def load_last_sync() -> datetime | None:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return datetime.fromisoformat(f.read().strip())
        except ValueError:
            return None
    return None


def save_last_sync(dt: datetime):
    with open(STATE_FILE, "w") as f:
        f.write(dt.isoformat())


# ── AppleScript 날짜 파싱 ──────────────────────────────────
def parse_apple_date(date_str: str) -> datetime | None:
    """
    AppleScript (modification date) as string 출력 파싱.
    한국어 로케일: '2026. 3. 2. 오후 4:30:00'
    영어 로케일:   'Sunday, March 2, 2026 at 4:30:00 PM'
    숫자를 순서대로 추출 후 오전/오후·AM/PM 보정.
    """
    nums = re.findall(r'\d+', date_str)
    if len(nums) < 6:
        return None
    try:
        y, mo, d, h, mn, s = (int(n) for n in nums[:6])
        if '오후' in date_str or 'PM' in date_str.upper():
            if h != 12:
                h += 12
        elif ('오전' in date_str or 'AM' in date_str.upper()) and h == 12:
            h = 0
        return datetime(y, mo, d, h, mn, s)
    except Exception:
        return None


# ── AppleScript 실행 ───────────────────────────────────────
def fetch_notes() -> list[dict]:
    """메모앱 00_생각공장 폴더에서 모든 메모 가져오기"""
    script = f'''
tell application "Notes"
    set theOutput to ""
    set folderName to "{FOLDER_NAME}"
    set theFolder to missing value
    repeat with f in folders
        if name of f is folderName then
            set theFolder to f
            exit repeat
        end if
    end repeat
    if theFolder is missing value then
        return "ERR:FOLDER_NOT_FOUND"
    end if
    repeat with theNote in (notes in theFolder)
        set noteTitle to name of theNote
        set dateStr to (modification date of theNote) as string
        set noteBody to plaintext of theNote
        set theOutput to theOutput & "<<<NOTE>>>" & noteTitle & "<<<DATE>>>" & dateStr & "<<<BODY>>>" & noteBody & "<<<END>>>"
    end repeat
    return theOutput
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout.strip()

        if result.returncode != 0:
            msg = f"❌ AppleNotes AppleScript 오류: {result.stderr.strip()}"
            print(f"  {msg}")
            send_webhook(msg)
            return []

        if output.startswith("ERR:FOLDER_NOT_FOUND"):
            msg = f"❌ AppleNotes: '{FOLDER_NAME}' 폴더를 찾을 수 없음"
            print(f"  {msg}")
            send_webhook(msg)
            return []

        if not output:
            return []

        notes = []
        for block in output.split("<<<NOTE>>>"):
            if not block.strip():
                continue
            try:
                title, rest   = block.split("<<<DATE>>>", 1)
                date_str, rest = rest.split("<<<BODY>>>", 1)
                body, _       = rest.split("<<<END>>>", 1)
                mod_dt = parse_apple_date(date_str.strip())
                if mod_dt is None:
                    print(f"  ⚠️ 날짜 파싱 실패: {date_str.strip()!r}")
                    continue
                notes.append({
                    "title":    title.strip(),
                    "body":     body.strip(),
                    "modified": mod_dt,
                })
            except Exception as e:
                print(f"  ⚠️ 메모 파싱 오류: {e}")
                continue

        return notes

    except subprocess.TimeoutExpired:
        msg = "❌ AppleNotes 스캔 타임아웃 (60초)"
        print(f"  {msg}")
        send_webhook(msg)
        return []
    except Exception as e:
        msg = f"❌ AppleNotes 오류: {e}"
        print(f"  {msg}")
        send_webhook(msg)
        return []


# ── 01_Inbox 저장 ──────────────────────────────────────────
def save_to_inbox(notes: list[dict]) -> list[str]:
    saved = []
    for note in notes:
        date_tag   = datetime.now().strftime("%y%m%d")
        time_tag   = datetime.now().strftime("%H%M%S")
        safe_title = "".join(c for c in note["title"] if c.isalnum() or c in " _-가-힣")[:30].strip()
        fname      = f"{date_tag}_AppleNotes_{time_tag}_{safe_title}.txt"
        fpath      = os.path.join(INBOX, fname)

        # 파일명 중복 방지 (같은 초에 여러 메모 저장 시)
        counter = 1
        while os.path.exists(fpath):
            fname  = f"{date_tag}_AppleNotes_{time_tag}_{safe_title}_{counter}.txt"
            fpath  = os.path.join(INBOX, fname)
            counter += 1

        with open(fpath, "w", encoding="utf-8") as f:
            f.write(
                f"# {note['title']}\n"
                f"수집일: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
                f"{note['body']}"
            )

        print(f"  ✅ {fname}")
        saved.append(fname)

    return saved


# ── 메인 ──────────────────────────────────────────────────
def run():
    now = datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] ── AppleNotes 동기화 시작")

    last_sync = load_last_sync()
    print(f"  📅 마지막 동기화: {last_sync.strftime('%Y-%m-%d %H:%M:%S') if last_sync else '없음 (전체 가져오기)'}")

    all_notes = fetch_notes()
    print(f"  📝 전체 메모: {len(all_notes)}개")

    if not all_notes:
        print("  ─ 메모 없음 또는 스캔 실패")
        return

    new_notes = [n for n in all_notes if not last_sync or n["modified"] > last_sync]
    print(f"  🆕 신규/수정: {len(new_notes)}개")

    if not new_notes:
        print("  ─ 신규 메모 없음")
        return

    saved = save_to_inbox(new_notes)
    save_last_sync(now)

    footer = "\n\n🔗 <https://notebooklm.google.com/notebook/b67639c2-e8f8-4af2-a686-4e91d27875e3?authuser=1|제텔카스텐 전략실 바로가기>\n📂 <https://drive.google.com/drive/u/1/folders/1TmwPlc6JCtYbSwXzRonI3BeehLX059Vg|오늘자 원문 (Google Drive)>"
    msg = (
        f"📝 AppleNotes 동기화 완료 [{now.strftime('%H:%M')}]\n"
        f"신규/수정 {len(saved)}개\n"
        + "\n".join(f"  • {f}" for f in saved[:10])
        + (f"\n  … 외 {len(saved)-10}개" if len(saved) > 10 else "")
    )
    send_webhook(msg + footer)
    print(f"  📡 Webhook 전송")


if __name__ == "__main__":
    run()
