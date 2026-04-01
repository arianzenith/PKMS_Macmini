#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
devonthink_sync.py — DEVONthink 4 연동 모듈

기능:
  1. is_running()              — DEVONthink 4 실행 중 여부 확인
  2. save_report(title, body)  — 보고서를 마크다운 레코드로 저장 → UUID 반환
  3. search_related(query)     — DEVONthink에서 관련 원본 검색 → 파일 목록 반환

단독 실행 (테스트):
  python3 _internal_system/devonthink_sync.py
"""

import os
import sys
import subprocess
import tempfile
import time

# DEVONthink 4 application bundle ID
DT_APP_ID = "DNtp"

# 기본 저장 그룹 경로 (없으면 자동 생성)
DEFAULT_GROUP = "/생각공장/보고서"

# 대상 데이터베이스 파일 경로
DEFAULT_DB_PATH = os.path.expanduser("~/Databases/우리집도서관.dtBase2")


# ── AppleScript 실행 헬퍼 ──────────────────────────────────
def _run_script(script: str, timeout: int = 30) -> tuple[bool, str]:
    """osascript 실행 → (성공여부, stdout)"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _esc(text: str) -> str:
    """AppleScript 문자열 이스케이프 (큰따옴표 내부 삽입용)"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


# ── 핵심 기능 ──────────────────────────────────────────────
def is_running() -> bool:
    """DEVONthink 실행 중 여부 (번들 ID 기준)"""
    ok, out = _run_script(f'application id "{DT_APP_ID}" is running')
    return ok and out.lower() == "true"


def _ensure_running(wait: int = 15) -> None:
    """DEVONthink이 실행 중이 아니면 osascript로 기동하고 wait초 대기."""
    if not is_running():
        subprocess.Popen(
            ["osascript", "-e", f'tell application id "{DT_APP_ID}" to activate'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(wait)


def save_report(title: str, body: str,
                tags: list[str] | None = None,
                group_path: str = DEFAULT_GROUP,
                db_path: str = DEFAULT_DB_PATH) -> str | None:
    """
    DEVONthink 4에 마크다운 레코드로 보고서 저장.
    본문을 임시 파일로 전달하여 큰따옴표·특수문자 이스케이프 문제를 우회.
    db_path로 대상 DB를 명시적으로 지정하여 Inbox 저장 방지.
    반환: UUID 문자열 또는 None(실패)
    """
    _ensure_running()  # cron 환경에서 앱이 닫혀 있을 경우 자동 기동

    # 본문을 임시 파일에 저장
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", encoding="utf-8", delete=False
    )
    try:
        tmp.write(body)
        tmp.close()
        tmp_path = tmp.name

        safe_title  = _esc(title.replace("\n", " "))
        safe_group  = _esc(group_path)
        safe_path   = _esc(tmp_path)
        safe_db     = _esc(db_path)

        tags_line = ""
        if tags:
            tags_str = ", ".join(f'"{_esc(t)}"' for t in tags)
            tags_line = f"set tags of theRecord to {{{tags_str}}}"

        script = f'''tell application id "{DT_APP_ID}"
    set theDB to open database "{safe_db}"
    set bodyText to do shell script "cat " & quoted form of "{safe_path}"
    set theGroup to create location "{safe_group}" in theDB
    set theRecord to create record with {{name:"{safe_title}", type:markdown, content:bodyText}} in theGroup
    {tags_line}
    return uuid of theRecord
end tell'''

        ok, out = _run_script(script, timeout=30)
        if ok and out:
            return out
        print(f"  ❌ DEVONthink 저장 실패: {out}")
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def search_related(query: str, limit: int = 5) -> list[dict]:
    """
    DEVONthink 4에서 query로 유사 문서 검색.
    반환: [{"uuid": ..., "name": ..., "path": ..., "score": ...}, ...]
    """
    safe_q = _esc(query[:200])

    script = f'''tell application id "{DT_APP_ID}"
    set theResults to search "{safe_q}"
    set outList to ""
    set cnt to 0
    repeat with r in theResults
        if cnt >= {limit} then exit repeat
        set rUUID to uuid of r
        set rName to name of r
        set rLoc  to location of r
        set rScore to score of r as string
        set outList to outList & rUUID & "<<<S>>>" & rName & "<<<S>>>" & rLoc & "<<<S>>>" & rScore & "<<<R>>>"
        set cnt to cnt + 1
    end repeat
    return outList
end tell'''

    ok, out = _run_script(script, timeout=30)
    if not ok or not out:
        return []

    results = []
    for row in out.split("<<<R>>>"):
        row = row.strip()
        if not row:
            continue
        parts = row.split("<<<S>>>")
        if len(parts) >= 4:
            results.append({
                "uuid":  parts[0].strip(),
                "name":  parts[1].strip(),
                "path":  parts[2].strip(),
                "score": parts[3].strip(),
            })
    return results


# ── morning_report.py 연동용 공개 함수 ──────────────────────
def save_morning_report(report_text: str, date_tag: str) -> str | None:
    """
    morning_report.run()에서 호출.
    date_tag 예: "260329"
    그룹: /생각공장/보고서/2026
    """
    year = f"20{date_tag[:2]}"
    group = f"/생각공장/보고서/{year}"
    title = f"아침융합리포트_{date_tag}"
    return save_report(title, report_text, tags=["생각공장", "보고서"], group_path=group)


# ── 단독 실행 테스트 ───────────────────────────────────────
if __name__ == "__main__":
    print("\n── DEVONthink 4 연동 테스트 ──────────────────────")

    running = is_running()
    print(f"DEVONthink 4 실행 여부: {'true' if running else 'false'}")

    if not running:
        print("\n⚠️  DEVONthink 4가 실행 중이지 않습니다.")
        print("   앱을 시작한 후 다시 실행해주세요.")
        sys.exit(1)

    # 1) 보고서 저장 테스트
    print("\n1) 보고서 저장 테스트...")
    test_body = (
        "# DEVONthink 연동 테스트\n\n"
        "`devonthink_sync.py` 자동 생성 문서입니다.\n\n"
        "**삭제해도 됩니다.**\n\n"
        "## 테스트 항목\n- AppleScript 실행 ✅\n- 본문 저장 ✅\n- UUID 반환 ✅"
    )
    uuid = save_report(
        title="[TEST] devonthink_sync 연동 테스트",
        body=test_body,
        tags=["test", "생각공장"],
    )
    if uuid:
        print(f"   → UUID: {uuid}")
    else:
        print("   → 저장 실패 (권한 설정 확인: 시스템 설정 → 자동화 → Terminal → DEVONthink 4 ✅)")

    # 2) 관련 원본 검색 테스트
    print("\n2) 관련 원본 검색 테스트 (쿼리: 'AI 전략')...")
    hits = search_related("AI 전략", limit=5)
    if hits:
        for h in hits:
            print(f"   • {h['name']}  [score: {h['score']}]  ({h['path']})")
    else:
        print("   → 검색 결과 없음 (DB가 비어있거나 검색어 불일치)")

    print("\n── 테스트 완료 ───────────────────────────────────")
