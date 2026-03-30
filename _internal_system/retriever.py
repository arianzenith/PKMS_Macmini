#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retriever.py — Thought Factory 벡터 검색 엔진

사용:
  python3 retriever.py "검색 쿼리"
  python3 retriever.py "AI 전략" --top 5
"""

import os
import sys
import json
from collections import defaultdict
from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient

BASE_DIR = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

COLLECTION_NAME = "thought_factory"
EMBED_MODEL     = "gemini-embedding-001"
DEFAULT_TOP     = 5
QUESTIONS_FILE  = os.path.join(BASE_DIR, "_internal_system/pkms/questions.json")

client_ai     = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
client_qdrant = QdrantClient(host="localhost", port=6333)


def embed_query(text: str) -> list[float]:
    result = client_ai.models.embed_content(model=EMBED_MODEL, contents=text)
    return result.embeddings[0].values


def search(query: str, top: int = DEFAULT_TOP):
    vector = embed_query(query)
    result = client_qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=vector,
        limit=top,
        with_payload=True,
    )
    return result.points


def load_questions() -> list[dict]:
    """questions.json에서 active 질문을 weight 내림차순으로 반환"""
    if not os.path.exists(QUESTIONS_FILE):
        return []
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    questions = data.get("active", [])
    return sorted(questions, key=lambda q: q.get("weight", 0), reverse=True)



def get_today_question() -> dict | None:
    """
    가중치 기반 순환 로테이션으로 오늘의 탐구질문 1개 선택.

    동작 방식:
    - weight가 높을수록 더 자주 선택
    - last_used_date가 오래된 질문 우선
    - 선택 후 last_used_date 업데이트
    """
    if not os.path.exists(QUESTIONS_FILE):
        return None

    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    active = data.get("active", [])
    if not active:
        return None

    from datetime import date, timedelta
    today = date.today().isoformat()

    # 각 질문의 "다음 사용 예정일" 계산
    # weight=2.0 → 0.5일마다, weight=1.0 → 1일마다, weight=0.5 → 2일마다
    candidates = []
    for q in active:
        w = max(q.get("weight", 1.0), 0.1)
        last = q.get("last_used_date", "2000-01-01")
        last_date = date.fromisoformat(last)
        interval = 1.0 / w          # weight=2.0 → 0.5일 간격
        next_due = last_date + timedelta(days=interval)
        overdue = (date.today() - next_due).days  # 양수일수록 오래됨
        candidates.append((overdue, q))

    # 가장 오래된(overdue 큰) 질문 선택
    candidates.sort(key=lambda x: x[0], reverse=True)
    chosen = candidates[0][1]

    # last_used_date 업데이트
    for q in active:
        if q["id"] == chosen["id"]:
            q["last_used_date"] = today
            break

    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return chosen

def search_by_questions(top_per_q: int = 3) -> list[dict]:
    """
    active 질문 각각으로 Qdrant 검색 → 중복 제거 → 질문 태그 포함 반환.
    서로 다른 질문에서 나온 소스들이 함께 담겨 '충돌' 재료가 됨.
    """
    questions = load_questions()
    if not questions:
        return []

    seen_ids: set = set()
    results: list[dict] = []

    for q in questions:
        hits = search(q["question"], top=top_per_q)
        for hit in hits:
            if hit.id in seen_ids:
                continue
            seen_ids.add(hit.id)
            results.append({
                "question_id": q["id"],
                "question":    q["question"],
                "score":       hit.score,
                "fname":       hit.payload.get("fname", ""),
                "source_type": hit.payload.get("source_type", "other"),
                "content":     hit.payload.get("text", ""),
            })

    return results


def print_results(query: str, hits: list):
    print(f"\n🔍 쿼리: \"{query}\"  —  상위 {len(hits)}개 결과\n" + "=" * 60)
    for i, hit in enumerate(hits, 1):
        p = hit.payload
        print(f"\n[{i}] 유사도: {hit.score:.4f}")
        print(f"    파일: {p.get('fname', '-')}")
        print(f"    타입: {p.get('source_type', '-')}  청크: {p.get('chunk_index', 0)+1}/{p.get('total_chunks', 1)}")
        print(f"    내용: {p.get('text', '')[:200].strip()}...")
    print("=" * 60)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("사용법: python3 retriever.py \"검색어\" [--top N]")
        sys.exit(1)

    top = DEFAULT_TOP
    if "--top" in args:
        idx = args.index("--top")
        top = int(args[idx + 1])
        args = [a for i, a in enumerate(args) if i != idx and i != idx + 1]

    query = " ".join(args)
    hits = search(query, top)
    print_results(query, hits)
