#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
factory_one v4.0 안정판

목표
- 업무파일은 INPUT 폴더에서만 읽음
- txt/md 파일은 업무파일로 절대 인식하지 않음
- 업무파일 최대 2개를 1개 메시지로 융합
- Readwise / AppleNotes / Heptabase는 sources에서 재료로만 사용
- 처리된 업무파일은 originals/YYYYMMDD 로 이동
"""

import os
import json
import random
import shutil
import time
from datetime import datetime
from urllib import request as urllib_request
from urllib.error import URLError
from dotenv import load_dotenv
from google import genai

# ===============================
# 경로 설정
# ===============================

BASE_DIR = "/Users/arian/GDrive/NotebookLM_Staging"

INPUT_DIR = os.path.join(BASE_DIR, "INPUT")

ARCHIVE_DIR = os.path.join(BASE_DIR, "02_Archive")

SOURCES_DIR = os.path.join(ARCHIVE_DIR, "sources")

ORIGINALS_DIR = os.path.join(ARCHIVE_DIR, "originals")

LOG_DIR = os.path.join(BASE_DIR, "_internal_system/pkms/logs")

INDEX_FILE = os.path.join(BASE_DIR, "_internal_system/pkms/processed_index_factory_one.json")

ENV_PATH = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
WEBHOOK_URL    = os.getenv("WEBHOOK_URL")
MODEL_ID       = os.getenv("FACTORY_MODEL_ID", "gemini-2.5-pro")

if not GOOGLE_API_KEY:
    raise SystemExit(f"❌ GOOGLE_API_KEY 없음. 확인: {ENV_PATH}")

client = genai.Client(api_key=GOOGLE_API_KEY)

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(SOURCES_DIR, exist_ok=True)
os.makedirs(ORIGINALS_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ===============================
# 업무파일 확장자
# ===============================

WORK_EXT = {
".pdf",
".docx",
".pptx",
".xlsx",
".csv",
".png",
".jpg",
".jpeg"
}

# ===============================
# 로그
# ===============================

def log(msg):

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{ts}] {msg}")

# ===============================
# Gemini 호출
# ===============================

def call_gemini(prompt: str) -> str:

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

# ===============================
# Webhook 전송
# ===============================

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

# ===============================
# processed index
# ===============================

def load_index():

    if os.path.exists(INDEX_FILE):

        return json.load(open(INDEX_FILE))

    return {}

def save_index(d):

    with open(INDEX_FILE,"w") as f:

        json.dump(d,f,indent=2)

# ===============================
# 업무파일 판별
# ===============================

def is_work_file(fname):

    ext = os.path.splitext(fname.lower())[1]

    return ext in WORK_EXT

# ===============================
# INPUT 업무파일 수집
# ===============================

def list_work_files():

    files = []

    for f in os.listdir(INPUT_DIR):

        p = os.path.join(INPUT_DIR,f)

        if not os.path.isfile(p):

            continue

        if not is_work_file(f):

            continue

        files.append(p)

    files.sort(key=lambda x: os.stat(x).st_mtime,reverse=True)

    return files

# ===============================
# sources 재료 수집
# ===============================

def list_sources():

    items=[]

    for root,dirs,files in os.walk(SOURCES_DIR):

        for f in files:

            p=os.path.join(root,f)

            items.append(p)

    return items

# ===============================
# 업무파일 이동
# ===============================

def move_originals(paths):

    day_dir=os.path.join(ORIGINALS_DIR,datetime.now().strftime("%Y%m%d"))

    os.makedirs(day_dir,exist_ok=True)

    for p in paths:

        dst=os.path.join(day_dir,os.path.basename(p))

        try:

            shutil.move(p,dst)

        except Exception as e:

            log(f"원본 이동 실패 {p} {e}")

# ===============================
# 출처 요약
# ===============================

def make_source_summary(work,src):

    lines=[]

    lines.append("📚 출처")

    for w in work:

        lines.append(f"• {os.path.basename(w)}")

    for s in src[:4]:

        lines.append(f"• {os.path.basename(s)}")

    if len(src)>4:

        lines.append(f"• 외 {len(src)-4}개 소스")

    return "\n".join(lines)

# ===============================
# 프롬프트 생성
# ===============================

def build_prompt(work_files,sources):

    work_names=[os.path.basename(x) for x in work_files]

    src_names=[os.path.basename(x) for x in sources]

    rules = """[절대 규칙 — 위반 시 실패]
1) 반드시 아래 순서/라벨을 지켜라: 오늘의 핵심 판단 → 1. 거대 가설 → 2. 충돌 지점 → 3. 창발 아이디어 → 오늘 바로 실행 → 10. 파괴적 질문 → [이 질문이 치명적인 이유]
2) '📚 출처' 섹션은 절대 출력하지 마라. (출처는 시스템이 헤더에서 제공한다)
3) '오늘 바로 실행'은 반드시 독립 섹션으로 분리하여, 오늘 실제로 실행 가능한 행동 1개를 구체적으로 제시하라.
4) 10. 파괴적 질문은 질문 1개(완전한 문장)만 출력하라.
5) [이 질문이 치명적인 이유]는 2~4문장으로, 왜 치명적인지 의미를 설명하라.
6) 과장/환각 금지: 소스에 없는 사실을 단정하지 말고, 불확실하면 "가정"으로 표시하라.
7) 문체: 아침에 읽기 편한 간결한 문장. 불필요한 학술 표현 금지. 설명형·직관적으로 작성하라.
"""

    template = """오늘의 핵심 판단
→ (오늘 전략적으로 판단해야 할 것)
→ (고려할 리스크 또는 기회)

1. 거대 가설
(여러 자료를 통합한 가장 중요한 구조적 가설 — 최대 3개)

2. 충돌 지점
(자료 사이의 긴장 관계 또는 상충 논리 — 최대 3개)

3. 창발 아이디어
(분석 기반 새로운 전략적 아이디어 — 최대 3개)

오늘 바로 실행
(오늘 실제로 실행할 수 있는 행동 제안 1개)

10. 파괴적 질문
(현재 사고 구조를 흔드는 질문 1개)

[이 질문이 치명적인 이유]
(2~4문장)
"""

    prompt = (
        "당신은 '생각공장 Thought Factory'의 전략 분석 엔진이다.\n"
        "다음 자료(업무파일, 참고자료)를 분석하여 "
        "'실행 가능한 의사결정 시스템'을 위한 보고서를 작성하라.\n\n"
        f"{rules}\n"
        f"{template}\n"
        f"[업무파일]\n{work_names}\n\n"
        f"[참고자료]\n{src_names}\n"
    )

    return prompt

# ===============================
# 메인 실행
# ===============================

def run():

    log("==== factory_one v4 cycle ====")

    index=load_index()

    work=list_work_files()

    if not work:

        log("INPUT에 처리할 파일 없음")

        return

    # 최대 2개

    work=work[:2]

    # sources 수집

    src=list_sources()

    random.shuffle(src)

    src=src[:4]

    prompt=build_prompt(work,src)

    # ===========================
    # 기존 Gemini 호출
    # ===========================

    body=call_gemini(prompt)

    # ===========================

    header=f"🚨 실시간 융합 [{datetime.now().strftime('%H:%M')}]"

    source_summary=make_source_summary(work,src)

    msg=f"""{header}

업무파일: {", ".join(os.path.basename(x) for x in work)}

{source_summary}

{body}

🎯 제텔카스텐 전략실로 바로가기
"""

    send_webhook(msg)

    move_originals(work)

    for w in work:

        index[w]=True

    save_index(index)

    log("융합 완료")

# ===============================

if __name__=="__main__":

    run()
