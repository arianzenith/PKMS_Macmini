#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factory_one.py — Thought Factory 실시간 융합 엔진 v5.0

INPUT/ 폴더에 업무파일이 들어오면:
  1. Qdrant: 업무파일명으로 관련 지식 검색
  2. Gemini File API: 업무파일(주) + 지식DB(참고) 충돌 융합
  3. DEVONthink: /생각공장/보고서/YYYY/ 자동 저장
  4. Google Chat: 실시간 융합 보고서 전송
"""

import os
import json
import random
import shutil
import sys
import time
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError

from dotenv import load_dotenv
from google import genai

# 선택적 — Qdrant 없어도 동작
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from retriever import search as _qdrant_search
    _RETRIEVER = True
except Exception:
    _RETRIEVER = False

# 선택적 — DEVONthink 없어도 동작
try:
    from devonthink_sync import save_report as _dt_save_report
    _DEVONTHINK = True
except Exception:
    _DEVONTHINK = False

# ── 경로 설정 ──────────────────────────────────────────────
BASE_DIR      = "/Users/arian/GDrive/NotebookLM_Staging"
INPUT_DIR     = os.path.join(BASE_DIR, "INPUT")
ARCHIVE_DIR   = os.path.join(BASE_DIR, "02_Archive")
SOURCES_DIR   = os.path.join(ARCHIVE_DIR, "sources")
ORIGINALS_DIR = os.path.join(ARCHIVE_DIR, "originals")
LOG_DIR       = os.path.join(BASE_DIR, "_internal_system/pkms/logs")
INDEX_FILE    = os.path.join(BASE_DIR, "_internal_system/pkms/processed_index_factory_one.json")

ENV_PATH = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
MODEL_ID       = os.getenv("FACTORY_MODEL_ID", "gemini-2.5-pro")

if not GOOGLE_API_KEY:
    raise SystemExit(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")

client = genai.Client(api_key=GOOGLE_API_KEY)

for _d in (INPUT_DIR, SOURCES_DIR, ORIGINALS_DIR, LOG_DIR):
    os.makedirs(_d, exist_ok=True)

# 업무파일 확장자 → MIME type
WORK_EXT = {
    ".pdf":  "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv":  "text/csv",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


# ── 유틸 ──────────────────────────────────────────────────
def log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


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
        log(f"⚠️ Webhook 실패: {e}")


# ── 인덱스 ─────────────────────────────────────────────────
def load_index() -> dict:
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE) as f:
            return json.load(f)
    return {}


def save_index(d: dict):
    with open(INDEX_FILE, "w") as f:
        json.dump(d, f, indent=2)


# ── 파일 수집 ──────────────────────────────────────────────
def is_work_file(fname: str) -> bool:
    return os.path.splitext(fname.lower())[1] in WORK_EXT


def list_work_files() -> list[str]:
    files = [
        os.path.join(INPUT_DIR, f)
        for f in os.listdir(INPUT_DIR)
        if os.path.isfile(os.path.join(INPUT_DIR, f)) and is_work_file(f)
    ]
    return sorted(files, key=lambda x: os.stat(x).st_mtime, reverse=True)


def list_sources_fallback(n: int = 4) -> list[str]:
    """Qdrant 없을 때 폴백 — sources/ 랜덤 샘플"""
    all_files = []
    for root, _, files in os.walk(SOURCES_DIR):
        for f in files:
            all_files.append(os.path.join(root, f))
    random.shuffle(all_files)
    return all_files[:n]


# ── Qdrant 지식 검색 ────────────────────────────────────────
def search_knowledge(work_files: list[str], top: int = 4) -> list[dict]:
    """업무파일명을 쿼리로 Qdrant에서 관련 지식 검색"""
    if not _RETRIEVER:
        return []
    results = []
    seen_ids: set = set()
    for fpath in work_files:
        query = os.path.splitext(os.path.basename(fpath))[0].replace("_", " ")
        try:
            hits = _qdrant_search(query, top=top)
            for hit in hits:
                if hit.id in seen_ids:
                    continue
                seen_ids.add(hit.id)
                results.append({
                    "fname":   hit.payload.get("fname", ""),
                    "content": hit.payload.get("text", ""),
                    "score":   hit.score,
                })
        except Exception as e:
            log(f"⚠️ Qdrant 검색 실패 ({os.path.basename(fpath)}): {e}")
    return results


# ── Gemini File API 업로드 ─────────────────────────────────
def upload_work_files(work_files: list[str]) -> list:
    """업무파일을 Gemini File API로 업로드. 실패 시 건너뜀."""
    uploaded = []
    for fpath in work_files:
        ext  = os.path.splitext(fpath.lower())[1]
        mime = WORK_EXT.get(ext, "application/octet-stream")
        try:
            with open(fpath, "rb") as f:
                uf = client.files.upload(
                    file=f,
                    config={"mime_type": mime, "display_name": os.path.basename(fpath)},
                )
            log(f"  📎 업로드 완료: {os.path.basename(fpath)}")
            uploaded.append(uf)
        except Exception as e:
            log(f"  ⚠️ 업로드 실패 {os.path.basename(fpath)}: {e}")
    return uploaded


# ── 프롬프트 & 규칙 ───────────────────────────────────────
_RULES = """[절대 규칙 — 위반 시 실패]
1) 반드시 아래 순서/라벨을 지켜라: 오늘의 핵심 판단 → 1. 거대 가설 → 2. 충돌 지점 → 3. 창발 아이디어 → 오늘 바로 실행 → 10. 파괴적 질문 → [이 질문이 치명적인 이유]
2) '📚 출처' 섹션은 절대 출력하지 마라.
3) '오늘 바로 실행'은 반드시 독립 섹션으로 분리하여 아래 4요소를 모두 포함하라:
   - 다음 행동: (구체적 행동 1개)
   - 완료 조건: (언제 끝난 것으로 볼 것인가)
   - 소요 시간: (예상 소요 시간)
   - 리스크: (실행 시 주의할 점)
4) 10. 파괴적 질문은 질문 1개(완전한 문장)만 출력하라.
5) [이 질문이 치명적인 이유]는 2~4문장으로 설명하라.
6) 과장/환각 금지: 소스에 없는 사실은 "가정"으로 표시하라.
7) 문체: 간결하고 직관적. 불필요한 학술 표현 금지.
"""

_TEMPLATE = """오늘의 핵심 판단
→ (업무파일에서 도출한 전략적 판단)
→ (고려할 리스크 또는 기회)

1. 거대 가설
(업무파일 + 지식DB 통합 가설 — 최대 3개)

2. 충돌 지점
(업무파일과 지식DB 사이의 긴장 관계 — 최대 3개)

3. 창발 아이디어
(충돌에서 나온 새로운 전략 아이디어 — 최대 3개)

오늘 바로 실행
다음 행동: (구체적 행동 1개)
완료 조건: (언제 끝난 것으로 볼 것인가)
소요 시간: (예상 소요 시간)
리스크: (실행 시 주의할 점)

10. 파괴적 질문
(현재 사고 구조를 흔드는 질문 1개)

[이 질문이 치명적인 이유]
(2~4문장)
"""


def build_prompt(work_names: list[str], knowledge: list[dict], fallback_src: list[str]) -> str:
    """업무파일(70%) + 지식DB(30%) 구조의 프롬프트"""
    if knowledge:
        knowledge_block = "\n\n".join(
            f"[지식DB — {k['fname']} | 유사도 {k['score']:.3f}]\n{k['content'][:800].strip()}"
            for k in knowledge
        )
    elif fallback_src:
        texts = []
        for s in fallback_src:
            try:
                with open(s, "r", encoding="utf-8", errors="replace") as f:
                    texts.append(f"[참고자료 — {os.path.basename(s)}]\n{f.read()[:600].strip()}")
            except Exception:
                texts.append(f"[참고자료 — {os.path.basename(s)}]")
        knowledge_block = "\n\n".join(texts)
    else:
        knowledge_block = "(관련 지식 없음)"

    return (
        "당신은 '생각공장 Thought Factory'의 전략 분석 엔진이다.\n"
        "첨부된 업무파일(주요 분석 대상, 70%)을 지식DB 소스(참고, 30%)와 충돌·융합하여 "
        "'실행 가능한 의사결정 시스템'을 위한 보고서를 작성하라.\n\n"
        f"{_RULES}\n\n"
        f"{_TEMPLATE}\n\n"
        f"[업무파일] (위에 첨부됨): {', '.join(work_names)}\n\n"
        f"[지식DB — 참고 소스]\n{knowledge_block}"
    )


# ── Gemini 호출 ───────────────────────────────────────────
def call_gemini_with_files(uploaded_files: list, prompt: str) -> str:
    """업로드 파일 객체 + 텍스트 프롬프트로 Gemini 호출 (멀티모달)"""
    contents = list(uploaded_files) + [prompt]
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=contents)
            return (resp.text or "").strip()
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                log(f"⚠️ 429 한도 — {wait}s 대기 ({attempt+1}/3)")
                time.sleep(wait)
                continue
            log(f"❌ Gemini 오류: {e}")
            return ""
    return ""


def call_gemini(prompt: str) -> str:
    """텍스트 전용 폴백 (파일 업로드 실패 시)"""
    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=MODEL_ID, contents=prompt)
            return (resp.text or "").strip()
        except Exception as e:
            if "429" in str(e):
                wait = 60 * (attempt + 1)
                log(f"⚠️ 429 한도 — {wait}s 대기 ({attempt+1}/3)")
                time.sleep(wait)
                continue
            log(f"❌ Gemini 오류: {e}")
            return ""
    return ""


# ── 파일 이동 ──────────────────────────────────────────────
def move_originals(paths: list[str]):
    day_dir = os.path.join(ORIGINALS_DIR, datetime.now().strftime("%Y%m%d"))
    os.makedirs(day_dir, exist_ok=True)
    for p in paths:
        try:
            shutil.move(p, os.path.join(day_dir, os.path.basename(p)))
        except Exception as e:
            log(f"원본 이동 실패 {p}: {e}")


# ── 메인 ──────────────────────────────────────────────────
def run():
    log("==== factory_one v5 cycle ====")
    log(f"  Qdrant: {'✅' if _RETRIEVER else '❌ (폴백)'} | DEVONthink: {'✅' if _DEVONTHINK else '❌ (건너뜀)'}")

    index = load_index()
    work  = list_work_files()

    if not work:
        log("INPUT에 처리할 파일 없음")
        return

    work       = work[:2]
    work_names = [os.path.basename(w) for w in work]
    log(f"업무파일: {', '.join(work_names)}")

    # 1) Qdrant 지식 검색 (없으면 sources/ 폴백)
    knowledge    = search_knowledge(work)
    fallback_src = []
    if knowledge:
        log(f"  🎯 Qdrant 지식 {len(knowledge)}개 검색")
    else:
        log("  ⚠️ Qdrant 결과 없음 → sources/ 폴백")
        fallback_src = list_sources_fallback(4)

    # 2) 업무파일 Gemini File API 업로드
    uploaded = upload_work_files(work)

    # 3) 프롬프트 생성 → Gemini 호출
    prompt = build_prompt(work_names, knowledge, fallback_src)
    if uploaded:
        body = call_gemini_with_files(uploaded, prompt)
    else:
        log("  ⚠️ 파일 업로드 실패 → 텍스트 전용 폴백")
        body = call_gemini(prompt)

    if not body:
        log("❌ Gemini 응답 없음")
        return

    # 4) 출력 메시지 조립
    now      = datetime.now()
    date_tag = now.strftime("%y%m%d")
    time_tag = now.strftime("%H:%M")

    knowledge_lines = (
        "\n".join(f"• {k['fname']} (유사도 {k['score']:.3f})" for k in knowledge[:4])
        if knowledge else
        "\n".join(f"• {os.path.basename(s)}" for s in fallback_src)
    )
    header = (
        f"🚨 실시간 융합 [{time_tag}]\n"
        f"업무파일: {', '.join(work_names)}\n"
        f"📚 지식DB:\n{knowledge_lines}"
    )
    full_msg = f"{header}\n\n{body}"

    # 5) DEVONthink 저장
    if _DEVONTHINK:
        try:
            year     = f"20{date_tag[:2]}"
            dt_uuid  = _dt_save_report(
                title=f"실시간융합_{date_tag}_{work_names[0]}",
                body=full_msg,
                tags=["생각공장", "실시간융합"],
                group_path=f"/생각공장/보고서/{year}",
            )
            if dt_uuid:
                log(f"  📂 DEVONthink 저장 → UUID: {dt_uuid}")
        except Exception as e:
            log(f"  ⚠️ DEVONthink 오류: {e}")

    # 6) Webhook 전송
    send_webhook(full_msg)
    log("  📡 Webhook 전송")

    # 7) 파일 이동 + 인덱스 업데이트
    move_originals(work)
    for w in work:
        index[w] = now.isoformat()
    save_index(index)
    log("융합 완료")


if __name__ == "__main__":
    run()
