#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indexer.py — Thought Factory 벡터 색인 엔진

기능:
- 02_Archive/sources/ 의 .txt 파일을 읽어 Gemini 임베딩 생성
- Qdrant thought_factory 컬렉션에 저장
- 이미 색인된 파일은 MD5 해시로 스킵 (processed_index_indexer.json)
- cron 등록: 0 7 * * * python3 .../indexer.py
"""

import os
import json
import glob
import hashlib
import time
from datetime import datetime

from google import genai
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

# ── 설정 ───────────────────────────────────────────────────
BASE_DIR    = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH    = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SOURCES_DIR    = os.path.join(BASE_DIR, "02_Archive/sources")
INDEX_FILE     = os.path.join(BASE_DIR, "_internal_system/pkms/processed_index_indexer.json")
LOG_DIR        = os.path.join(BASE_DIR, "_internal_system/pkms/logs")

COLLECTION_NAME = "thought_factory"
EMBED_MODEL     = "gemini-embedding-001"
VECTOR_DIM      = 3072
CHUNK_SIZE      = 1500   # 청크 크기(chars)

os.makedirs(LOG_DIR, exist_ok=True)

client_ai     = genai.Client(api_key=GOOGLE_API_KEY)
client_qdrant = QdrantClient(host="localhost", port=6333)


# ── 인덱스 로드/저장 ──────────────────────────────────────

def load_index() -> dict:
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r") as f:
            return json.load(f)
    return {}


def save_index(d: dict):
    with open(INDEX_FILE, "w") as f:
        json.dump(d, f, indent=2)


def file_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ── Qdrant 컬렉션 초기화 ──────────────────────────────────

def ensure_collection():
    names = [c.name for c in client_qdrant.get_collections().collections]
    if COLLECTION_NAME not in names:
        client_qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )
        print(f"  ✅ 컬렉션 생성: {COLLECTION_NAME} (dim={VECTOR_DIM})")
    else:
        info = client_qdrant.get_collection(COLLECTION_NAME)
        print(f"  ℹ️ 컬렉션 존재: {COLLECTION_NAME} ({info.points_count}개 벡터)")


# ── 임베딩 ────────────────────────────────────────────────

def embed_text(text: str) -> list[float]:
    for attempt in range(3):
        try:
            result = client_ai.models.embed_content(
                model=EMBED_MODEL,
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            if "429" in str(e):
                wait = 30 * (attempt + 1)
                print(f"    ⚠️ 429 한도 — {wait}s 대기")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("임베딩 실패 (재시도 초과)")


def chunk_text(text: str) -> list[str]:
    return [text[i:i + CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]


# ── 파일 색인 ─────────────────────────────────────────────

def classify_source(fname: str) -> str:
    upper = fname.upper()
    if "READWISE" in upper:
        return "readwise"
    if "APPLENOTES" in upper or "APPLE_NOTES" in upper or "HEPTABASE" in upper:
        return "memo"
    return "other"


def index_file(fpath: str, index: dict, next_id: list) -> int:
    fname = os.path.basename(fpath)
    fhash = file_hash(fpath)

    if index.get(fname) == fhash:
        return 0  # 변경 없음 — 스킵

    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read().strip()
    if not text:
        return 0

    chunks = chunk_text(text)
    stype  = classify_source(fname)
    points = []

    for i, chunk in enumerate(chunks):
        try:
            vector = embed_text(chunk)
        except Exception as e:
            print(f"    ⚠️ 임베딩 실패 ({fname}[{i}]): {e}")
            continue

        points.append(PointStruct(
            id=next_id[0],
            vector=vector,
            payload={
                "fname": fname,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "source_type": stype,
                "text": chunk,
            },
        ))
        next_id[0] += 1

    if points:
        client_qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
        index[fname] = fhash
        return len(points)
    return 0


# ── 메인 ──────────────────────────────────────────────────

def run():
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ── indexer 시작")

    ensure_collection()
    index = load_index()

    files = sorted(glob.glob(os.path.join(SOURCES_DIR, "*.txt")))
    if not files:
        print("  ℹ️ sources/에 색인할 파일 없음")
        return

    current_count = client_qdrant.get_collection(COLLECTION_NAME).points_count
    next_id = [current_count]

    total_new = 0
    for fpath in files:
        n = index_file(fpath, index, next_id)
        if n > 0:
            print(f"  ✅ {os.path.basename(fpath)} → {n}청크")
            total_new += n

    save_index(index)

    final_count = client_qdrant.get_collection(COLLECTION_NAME).points_count
    print(f"\n  📊 완료: 신규 {total_new}개 / 전체 {final_count}개 벡터")


if __name__ == "__main__":
    run()
