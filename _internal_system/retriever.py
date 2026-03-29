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
from dotenv import load_dotenv
from google import genai
from qdrant_client import QdrantClient

BASE_DIR = "/Users/arian/GDrive/NotebookLM_Staging"
ENV_PATH = os.path.join(BASE_DIR, "_internal_system/pkms/.env")
load_dotenv(ENV_PATH)

COLLECTION_NAME = "thought_factory"
EMBED_MODEL     = "gemini-embedding-001"
DEFAULT_TOP     = 5

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
