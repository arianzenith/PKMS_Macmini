#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heptabase_sync.py — Heptabase → sources/ 동기화

hepta.db의 card 테이블에서 신규/수정 카드를 읽어
sources/ 폴더에 .txt 파일로 저장.

실행:
  python3 _internal_system/heptabase_sync.py
"""

import os
import json
import re
import sqlite3
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError
from dotenv import load_dotenv

BASE_DIR   = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH   = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

WEBHOOK_URL  = os.getenv("WEBHOOK_URL")
DB_PATH      = os.getenv("HEPTABASE_DB_PATH",
               "/Users/arian/Library/Application Support/project-meta/hepta.db")
SOURCES_DIR  = os.path.join(BASE_DIR, "02_Archive/sources")
STATE_FILE   = os.path.join(BASE_DIR, "_internal_system/pkms/heptabase_last_sync.txt")

os.makedirs(SOURCES_DIR, exist_ok=True)


# ── 상태 파일 ──────────────────────────────────────────────
def load_last_sync() -> str:
    """마지막 동기화 시각 (ISO 문자열). 없으면 '1970-01-01T00:00:00'"""
    if os.path.exists(STATE_FILE):
        try:
            return open(STATE_FILE).read().strip()
        except Exception:
            pass
    return "1970-01-01T00:00:00"


def save_last_sync(ts: str):
    with open(STATE_FILE, "w") as f:
        f.write(ts)


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


# ── 카드 내용 파싱 ─────────────────────────────────────────
def extract_text(content_json: str) -> str:
    """
    Heptabase card.content는 ProseMirror JSON.
    텍스트 노드만 추출해 평문으로 변환.
    """
    if not content_json:
        return ""
    try:
        doc = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return str(content_json)

    lines = []

    def walk(node):
        ntype = node.get("type", "")
        if ntype == "text":
            lines.append(node.get("text", ""))
        elif ntype in ("hardBreak", "paragraph"):
            lines.append("\n")
        for child in node.get("content", []):
            walk(child)

    walk(doc)
    text = "".join(lines)
    # 연속 빈줄 정리
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


# ── DB 쿼리 ────────────────────────────────────────────────
def fetch_updated_cards(last_sync: str) -> list[dict]:
    """last_sync 이후 수정된 비삭제 카드 반환"""
    if not os.path.exists(DB_PATH):
        print(f"  ❌ hepta.db 없음: {DB_PATH}")
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            """SELECT id, title, content, last_edited_time
               FROM card
               WHERE is_trashed = 0
                 AND last_edited_time > ?
               ORDER BY last_edited_time ASC""",
            (last_sync,)
        )
        rows = cur.fetchall()
        con.close()
        return [
            {"id": r[0], "title": r[1] or "(제목 없음)",
             "content": r[2] or "", "edited": r[3]}
            for r in rows
        ]
    except Exception as e:
        print(f"  ❌ DB 읽기 오류: {e}")
        return []


# ── sources/ 저장 ──────────────────────────────────────────
def save_card(card: dict) -> str:
    """카드를 sources/ 에 txt 파일로 저장. 파일명 반환."""
    date_tag  = datetime.now().strftime("%y%m%d")
    time_tag  = datetime.now().strftime("%H%M%S")
    safe_title = "".join(
        c for c in card["title"] if c.isalnum() or c in " _-가-힣"
    )[:30].strip()
    fname  = f"{date_tag}_Heptabase_{time_tag}_{safe_title}.txt"
    fpath  = os.path.join(SOURCES_DIR, fname)

    # 중복 방지
    counter = 1
    while os.path.exists(fpath):
        fname  = f"{date_tag}_Heptabase_{time_tag}_{safe_title}_{counter}.txt"
        fpath  = os.path.join(SOURCES_DIR, fname)
        counter += 1

    body = extract_text(card["content"])
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(
            f"# {card['title']}\n"
            f"출처: Heptabase\n"
            f"수집일: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"수정시각: {card['edited']}\n\n"
            f"{body}"
        )
    return fname


# ── 메인 ──────────────────────────────────────────────────
def run():
    now = datetime.now()
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S')}] ── Heptabase 동기화 시작")

    last_sync = load_last_sync()
    print(f"  📅 마지막 동기화: {last_sync}")

    cards = fetch_updated_cards(last_sync)
    print(f"  🃏 신규/수정 카드: {len(cards)}개")

    if not cards:
        print("  ─ 신규 카드 없음")
        return

    saved = []
    latest_ts = last_sync
    for card in cards:
        fname = save_card(card)
        saved.append(fname)
        print(f"  ✅ {fname}")
        if card["edited"] > latest_ts:
            latest_ts = card["edited"]

    save_last_sync(latest_ts)

    msg = (
        f"🃏 Heptabase 동기화 완료 [{now.strftime('%H:%M')}]\n"
        f"신규/수정 {len(saved)}개\n"
        + "\n".join(f"  • {f}" for f in saved[:10])
        + (f"\n  … 외 {len(saved)-10}개" if len(saved) > 10 else "")
    )
    send_webhook(msg)
    print(f"  📡 Webhook 전송")


if __name__ == "__main__":
    run()
