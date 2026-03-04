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
from datetime import datetime

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

    prompt=f"""

다음 업무파일을 중심으로 외부지식과 내부메모를 융합 분석하라.

업무파일
{work_names}

참고자료
{src_names}

출력 형식

공장장님, 실시간 융합 결과입니다.

### 1. 거대 가설
### 2. 충돌 지점
### 3. 창발 아이디어
### 10. 파괴적 질문
#### [이 질문이 치명적인 이유]

"""

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
