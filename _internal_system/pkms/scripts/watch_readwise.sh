#!/bin/bash
# Readwise 파일 도착 감시 스크립트

GDRIVE_ROOT="/Users/arian/GDrive"
CHECK_INTERVAL=10  # 10초마다 체크
MAX_CHECKS=180     # 최대 30분 (180 * 10초)

echo "======================================================================"
echo "🔍 Readwise 파일 감시 시작"
echo "======================================================================"
echo "📂 감시 위치: $GDRIVE_ROOT"
echo "⏱️  체크 간격: ${CHECK_INTERVAL}초"
echo "⏰ 최대 대기: $((MAX_CHECKS * CHECK_INTERVAL / 60))분"
echo "======================================================================"
echo ""

count=0
while [ $count -lt $MAX_CHECKS ]; do
    count=$((count + 1))
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    # Readwise 파일 검색
    readwise_files=$(find "$GDRIVE_ROOT" -maxdepth 2 \
        \( -name "*Readwise*" -o -name "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]__*" \) \
        -type f -mmin -60 2>/dev/null)

    if [ -n "$readwise_files" ]; then
        echo ""
        echo "======================================================================"
        echo "✅ Readwise 파일 발견! [$timestamp]"
        echo "======================================================================"
        echo ""
        echo "$readwise_files" | while read -r file; do
            filename=$(basename "$file")
            filesize=$(ls -lh "$file" | awk '{print $5}')
            echo "📄 $filename ($filesize)"
        done
        echo ""
        echo "======================================================================"
        echo "🚀 브릿지를 실행하려면 다음 명령어를 실행하세요:"
        echo "======================================================================"
        echo "cd \"$GDRIVE_ROOT/NotebookLM_Staging/_internal_system/pkms\" && ./run.sh"
        echo "======================================================================"
        exit 0
    fi

    # 진행 상황 표시
    elapsed=$((count * CHECK_INTERVAL))
    elapsed_min=$((elapsed / 60))
    elapsed_sec=$((elapsed % 60))

    printf "\r⏳ 감시 중... [%02d:%02d] (%d/%d 체크)" \
        $elapsed_min $elapsed_sec $count $MAX_CHECKS

    sleep $CHECK_INTERVAL
done

echo ""
echo ""
echo "======================================================================"
echo "⏰ 타임아웃: 30분 동안 Readwise 파일이 도착하지 않았습니다."
echo "======================================================================"
echo ""
echo "다음을 확인해주세요:"
echo "1. Readwise에서 Export를 실행했나요?"
echo "2. https://drive.google.com에서 파일이 보이나요?"
echo "3. Google Drive Desktop이 실행 중인가요?"
echo ""
echo "수동으로 다시 확인하려면:"
echo "  bash $(basename "$0")"
echo "======================================================================"
exit 1
