#!/bin/bash
# PKMS 통합 파이프라인 실행 스크립트

cd "$(dirname "$0")"

echo "🚀 PKMS 파이프라인 실행..."
echo ""

python3 scripts/run_pipeline.py

echo ""
echo "✅ 완료!"
