import os, json, glob
from datetime import datetime, timedelta
from urllib import request as urllib_request
from urllib.error import URLError
from dotenv import load_dotenv

# ── 설정 ───────────────────────────────────────────────────
BASE_DIR = "/Users/arian/GDrive/NotebookLM_Staging"
load_dotenv(os.path.join(BASE_DIR, "_internal_system/pkms/.env"))

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ARCHIVE     = os.path.join(BASE_DIR, "02_Archive")


def send_webhook(text: str):
    if not WEBHOOK_URL:
        print("⚠️ WEBHOOK_URL 없음")
        return
    try:
        data = json.dumps({"text": text}).encode("utf-8")
        req  = urllib_request.Request(
            WEBHOOK_URL, data=data,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib_request.urlopen(req, timeout=10)
        print("📡 아침 보고 전송 완료")
    except URLError as e:
        print(f"⚠️ Webhook 실패: {e}")


def build_report() -> str:
    today     = datetime.now()
    yesterday = today - timedelta(days=1)
    prefix    = yesterday.strftime("%y%m%d")

    files = sorted(glob.glob(os.path.join(ARCHIVE, f"{prefix}_Zettelkasten_*.txt")))

    if not files:
        return (
            f"📋 아침 보고 [{today.strftime('%Y-%m-%d %H:%M')}]\n"
            f"어제({prefix}) 생산된 제텔카스텐 노트가 없습니다."
        )

    lines = [
        f"📋 아침 보고 [{today.strftime('%Y-%m-%d %H:%M')}]",
        f"어제 생산된 노트: {len(files)}개\n",
    ]

    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            # 첫 번째 제목 줄 추출
            title_line = next(
                (l.lstrip("# ").strip() for l in content.splitlines() if l.strip()),
                fname
            )
            lines.append(f"  • {title_line}")
        except Exception:
            lines.append(f"  • {fname}")

    return "\n".join(lines)


if __name__ == "__main__":
    report = build_report()
    print(report)
    send_webhook(report)
