# PKMS Project Rules

**철학: "정제된 지식의 연결"**

## 🎯 The Big 3 Rule (절대 원칙)

이 프로젝트는 **오직 3개의 지식 소스만** 처리합니다.

### ✅ 허용된 소스 (The Big 3)

#### Channel A: Research Pipeline
- **경로**: `00_INBOX/heptabase`, `00_INBOX/marginnote`, `00_INBOX/readwise`
- **설명**: Marginnote4 → Readwise → Heptabase (MCP를 통한 추출)
- **목적**: 학습/독서 노트의 정제된 최종 결과물
- **특징**: 이미 한 번 이상 가공된 고품질 지식

#### Channel B: Work Files (Manual Upload)
- **경로**: `00_INBOX/manual`
- **설명**: Google Drive에 직접 업로드하는 업무 관련 파일
- **목적**: 즉각적인 업무 활용이 필요한 문서
- **특징**: 실무 가이드, 프로세스, 코드 예제

#### Channel C: Video Insight
- **경로**: `00_INBOX/youtube`
- **설명**: YouTube MCP를 통한 자막/요약본 추출
- **목적**: 영상 콘텐츠의 텍스트 변환
- **특징**: MCP로 자동 추출된 구조화된 인사이트

## 🚫 금지 사항 (Do NOT)

다음은 **절대로** 시스템에 포함하지 않습니다:

### ❌ 파편화된 데이터
- Daily notes (일일 노트)
- Journal entries (저널/일지)
- Quick memos (빠른 메모)
- Fleeting notes (임시 메모)
- Scratch pads (스크래치 패드)

### ❌ 비정제 데이터
- 원본 PDF/eBook (가공 전)
- 날것의 클리핑/하이라이트
- 처리되지 않은 웹 스크랩
- 임시 저장 파일

### ❌ 개인 기록
- 할 일 목록 (TODO lists)
- 일정/캘린더 항목
- 개인 감상/회고
- 단순 참고용 메모

### ❌ 출처 불명 데이터
- The Big 3 외의 임의 소스
- 검증되지 않은 경로의 파일
- 수동으로 생성한 랜덤 노트

## 🔒 강제 검증 (Source Validation)

모든 파일은 분류 전 **반드시** 소스 검증을 거쳐야 합니다.

### 검증 프로세스

```python
# classifier.py에서 자동 실행
def validate_source(file_path):
    """
    The Big 3 소스 중 하나인지 확인

    Returns:
        (is_valid, channel_name)
    """
    if file_path in allowed_sources:
        return True, channel_name
    else:
        logger.warning("File ignored: not from The Big 3")
        return False, None
```

### 검증 실패 시

```yaml
# config.yaml 설정
classification:
  validate_source: true          # 검증 활성화
  ignore_unknown_sources: true   # 실패 시 무시
```

- 로그에 경고 기록
- 파일 이동하지 않음
- 분류 프로세스 중단

## 📂 4 Categories (NotebookLM)

분류 대상은 **오직 4개 카테고리**입니다.

### 01_Work_Knowledge
- **정의**: 업무에 직접 활용 가능한 실무 지식
- **예시**: API 구현 가이드, 코드 스니펫, 설정 템플릿
- **키워드**: 즉시 적용, How-to, 실무 코드

### 02_업무심화
- **정의**: 업무 역량 향상을 위한 심화 학습
- **예시**: 아키텍처 패턴, 설계 원칙, 성능 최적화
- **키워드**: 원리 이해, 깊이, 전문성

### 03_확장교양
- **정의**: 간접적으로 업무에 도움이 되는 교양/인사이트
- **예시**: 경영 철학, 심리학, 커리어 전략, 리더십
- **키워드**: 통찰, 관점, 간접 효과

### 04_재미
- **정의**: 흥미 위주의 콘텐츠 (업무 무관)
- **예시**: 게임 리뷰, 엔터테인먼트, 취미
- **키워드**: 순수 흥미, 휴식

## 🔄 워크플로우 원칙

### 입력 단계
1. The Big 3 채널 중 하나에 파일 배치
2. 자동 또는 수동 분류 실행

### 검증 단계
1. 소스 경로 확인
2. The Big 3 여부 검증
3. 통과 시 다음 단계, 실패 시 무시

### 분류 단계
1. Claude API로 내용 분석
2. 4개 카테고리 중 선택
3. 신뢰도 계산

### 이동 단계
1. 대상 카테고리 폴더로 이동
2. 파일명 정규화
3. 분류 이력 기록

## 🛡️ 시스템 보호

### Ignore Patterns

```yaml
# config.yaml
file_patterns:
  ignore_patterns:
    - "daily*"      # 데일리 노트
    - "journal*"    # 저널
    - "memo*"       # 메모
    - "temp*"       # 임시 파일
    - "draft*"      # 초안
    - "todo*"       # 할일
```

### 자동 필터링

- 파일명 패턴으로 1차 필터
- 소스 경로로 2차 필터
- 내용 분석 전 차단

## 📊 목표 (End Goal)

1. **Google Drive Staging**: The Big 3 데이터 집결
2. **자동 분류**: Claude API로 4개 카테고리 배분
3. **NotebookLM 연동**: 각 카테고리별 질문 세트 실행
4. **고품질 지식**: 정제된 지식의 연결 네트워크

## 🎓 개발자 지침

### DO
- ✅ The Big 3 소스만 코드에 포함
- ✅ 소스 검증 로직 우선 실행
- ✅ 4개 카테고리만 사용
- ✅ 엄격한 필터링 유지

### DON'T
- ❌ 새로운 소스 추가 제안
- ❌ 데일리/메모 관련 기능
- ❌ 5개 이상의 카테고리
- ❌ 소스 검증 우회

## 🔍 테스트 체크리스트

새 기능 추가 시 확인:

- [ ] The Big 3 원칙 준수
- [ ] 소스 검증 로직 포함
- [ ] 4개 카테고리만 사용
- [ ] Ignore 패턴 적용
- [ ] 로그에 소스 정보 기록

## 📝 코드 주석 규칙

모든 주요 함수에 The Big 3 언급:

```python
"""
PKMS Classification Engine
Project Rule: Only 3 allowed sources (The Big 3)

Channels:
- Channel A: Research Pipeline
- Channel B: Work Files
- Channel C: Video Insight
"""
```

## 🚨 위반 사례

### ❌ 잘못된 예시

```python
# 새 소스 추가 시도 (금지)
def process_notion_export():
    pass

# 데일리 노트 처리 (금지)
def create_daily_note():
    pass

# 카테고리 추가 (금지)
categories = {
    "work": "...",
    "personal": "...",  # ← 금지
    "archive": "..."    # ← 금지
}
```

### ✅ 올바른 예시

```python
# The Big 3 검증
def validate_source(file_path):
    allowed_channels = ["heptabase", "marginnote", "readwise", "manual", "youtube"]
    return any(ch in file_path for ch in allowed_channels)

# 4개 카테고리만
categories = {
    "work_knowledge": "...",
    "work_advanced": "...",
    "extended_learning": "...",
    "entertainment": "..."
}
```

---

**이 규칙은 프로젝트의 핵심 철학입니다. 모든 코드와 설계는 이 원칙을 따라야 합니다.**
