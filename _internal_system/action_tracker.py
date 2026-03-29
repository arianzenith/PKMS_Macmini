#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
action_tracker.py — Thought Factory Action 추적 엔진

기능:
- 아침 리포트의 '오늘 바로 실행' 블록을 추출해 날짜별 JSON에 누적
- morning_report.py에서 어제 Action을 프롬프트에 주입하기 위해 조회 제공
- 단독 실행 시 최근 이력 출력
"""

import os
import json
import glob
import re
from datetime import datetime, timedelta

BASE_DIR   = "/Users/arian/GDrive/NotebookLM_Staging"
ARCHIVE    = os.path.join(BASE_DIR, "02_Archive")
ACTION_FILE = os.path.join(BASE_DIR, "_internal_system/pkms/action_history.json")


# ── 이력 로드/저장 ────────────────────────────────────────

def load_history() -> dict:
    if os.path.exists(ACTION_FILE):
        with open(ACTION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history: dict):
    with open(ACTION_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ── 블록 추출 ─────────────────────────────────────────────

def extract_action_block(text: str) -> str:
    """리포트 텍스트에서 '오늘 바로 실행' 4요소 블록 추출"""
    match = re.search(
        r'오늘 바로 실행\n(.*?)(?=\n10\. 파괴적 질문|\n\[이 질문이|\Z)',
        text, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    return ""


# ── 저장/조회 API ─────────────────────────────────────────

def save_today_action(report_text: str, date_key: str = None):
    """오늘 리포트에서 Action 블록 추출 후 JSON에 누적 저장"""
    if date_key is None:
        date_key = datetime.now().strftime("%Y-%m-%d")

    block = extract_action_block(report_text)
    if not block:
        print("  ⚠️ 'action_tracker': '오늘 바로 실행' 블록을 찾지 못했습니다.")
        return

    history = load_history()
    history[date_key] = {
        "date": date_key,
        "action": block,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_history(history)
    print(f"  ✅ Action 저장: {date_key}")


def get_yesterday_action() -> str:
    """어제 날짜의 Action 블록 반환 (없으면 빈 문자열)"""
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    history = load_history()
    entry = history.get(yesterday, {})
    return entry.get("action", "")


# ── 이력 출력 ─────────────────────────────────────────────

def print_history(n: int = 7):
    history = load_history()
    if not history:
        print("  이력 없음")
        return

    keys = sorted(history.keys(), reverse=True)[:n]
    for k in keys:
        entry = history[k]
        print(f"\n{'='*54}")
        print(f"📅 {k}  (저장: {entry.get('saved_at', '-')})")
        print(f"{entry['action']}")
    print(f"\n{'='*54}")
    print(f"총 {len(history)}일 이력 보관 중")


# ── 단독 실행 ─────────────────────────────────────────────

if __name__ == "__main__":
    today = datetime.now().strftime("%y%m%d")
    pattern = os.path.join(ARCHIVE, f"{today}_Zettelkasten_*_아침융합리포트v3.txt")
    files = sorted(glob.glob(pattern))

    if files:
        latest = files[-1]
        with open(latest, "r", encoding="utf-8") as f:
            text = f.read()
        print(f"\n📄 리포트: {os.path.basename(latest)}")
        save_today_action(text)
    else:
        print(f"  ℹ️ 오늘({today}) 리포트 없음 — 이력만 출력합니다")

    print("\n📋 최근 Action 이력:")
    print_history()
