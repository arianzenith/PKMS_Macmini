import os, json, time, requests
from datetime import datetime, timezone
from urllib import request as urllib_request
from urllib.error import URLError
from dotenv import load_dotenv

# ── 경로 설정 ──────────────────────────────────────────────
BASE_DIR  = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH  = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

READWISE_TOKEN = os.getenv("READWISE_TOKEN")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
INBOX          = os.path.join(BASE_DIR, "01_Inbox")
STATE_FILE     = os.path.join(BASE_DIR, "_internal_system/pkms/readwise_last_sync.txt")

if not READWISE_TOKEN:
    print(f"❌ READWISE_TOKEN 없음. 확인: {ENV_PATH}")
    exit(1)

os.makedirs(INBOX, exist_ok=True)

HEADERS = {"Authorization": f"Token {READWISE_TOKEN}"}


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


def load_last_sync() -> str | None:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return f.read().strip()
    return None


def save_last_sync(ts: str):
    with open(STATE_FILE, "w") as f:
        f.write(ts)


def paginate(url: str, params: dict) -> list:
    """429 retry 포함 페이지네이션."""
    results = []
    while url:
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                print(f"  ⏳ Rate limit — {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            url    = data.get("next")
            params = {}          # next URL에 이미 포함됨
        except requests.HTTPError as e:
            print(f"  ❌ API 오류: {e}")
            break
    return results


def fetch_books() -> dict:
    """book_id → {title, author, source_url} 매핑."""
    raw = paginate("https://readwise.io/api/v2/books/", {"page_size": 100})
    return {
        b["id"]: {
            "title":  b.get("title") or "Unknown",
            "author": b.get("author") or "",
            "url":    b.get("source_url") or b.get("asin") or "",
        }
        for b in raw
    }


def fetch_highlights(updated_after: str | None = None) -> list:
    params = {"page_size": 100}
    if updated_after:
        params["updated__gt"] = updated_after
    return paginate("https://readwise.io/api/v2/highlights/", params)


def highlights_to_md(highlights: list, books: dict) -> dict:
    grouped = {}
    for h in highlights:
        book_id = h.get("book_id")
        book    = books.get(book_id, {})
        title   = book.get("title") or "Unknown"
        author  = book.get("author", "")
        src_url = book.get("url", "")
        text    = h.get("text", "").strip()
        note    = h.get("note", "").strip()

        if not text:
            continue

        if title not in grouped:
            grouped[title] = {"author": author, "url": src_url, "highlights": []}

        entry = f"- {text}"
        if note:
            entry += f"\n  > 📝 {note}"
        grouped[title]["highlights"].append(entry)

    return grouped


def save_to_inbox(books: dict) -> list:
    saved = []
    ts    = datetime.now().strftime("%y%m%d_%H%M%S")

    for title, data in books.items():
        date_tag   = datetime.now().strftime("%y%m%d")
        time_tag   = datetime.now().strftime("%H%M%S")
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-가-힣")[:30].strip()
        fname      = f"{date_tag}_Readwise_{time_tag}_{safe_title}.txt"
        fpath      = os.path.join(INBOX, fname)

        lines = [
            f"# {title}",
            f"저자: {data['author']}",
            f"출처: {data['url']}",
            f"수집일: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 하이라이트",
            "",
        ]
        lines.extend(data["highlights"])

        with open(fpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"  ✅ {fname} ({len(data['highlights'])}개)")
        saved.append(fname)

    return saved


def run():
    ts_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts_start}] ── Readwise 동기화 시작")

    last_sync = load_last_sync()
    print(f"  📅 마지막 동기화: {last_sync or '없음 (전체 가져오기)'}")

    print("  📖 Books 목록 조회 중…")
    books_map = fetch_books()
    print(f"     → {len(books_map)}개 소스")

    highlights = fetch_highlights(updated_after=last_sync)
    print(f"  📥 하이라이트: {len(highlights)}개")

    if not highlights:
        print("  ─ 신규 하이라이트 없음")
        return

    grouped = highlights_to_md(highlights, books_map)
    saved   = save_to_inbox(grouped)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_last_sync(now_utc)

    if saved:
        msg = (
            f"📚 Readwise 동기화 완료 [{datetime.now().strftime('%H:%M')}]\n"
            f"소스 {len(grouped)}개 / 하이라이트 {len(highlights)}개\n"
            + "\n".join(f"  • {f}" for f in saved[:10])
            + (f"\n  … 외 {len(saved)-10}개" if len(saved) > 10 else "")
        )
        send_webhook(msg)
        print(f"  📡 Webhook 전송")

if __name__ == "__main__":
    run()
