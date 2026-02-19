# PKMS (Personal Knowledge Management System)

Claude API를 활용한 자동화된 개인 지식 관리 시스템

## 🎯 프로젝트 철학

**"정제된 지식의 연결"**

이 시스템은 엄격하게 선별된 3개의 지식 소스(The Big 3)만을 처리하여,
고품질의 지식을 NotebookLM의 4개 카테고리로 자동 분류합니다.

## 📊 시스템 아키텍처

```
The Big 3 Sources
├─ Channel A: Research Pipeline
│  ├─ Marginnote4 → Readwise → Heptabase (MCP)
│  └─ 학습/독서 노트의 정제된 결과물
│
├─ Channel B: Work Files
│  ├─ Google Drive inbox/manual
│  └─ 업무 관련 직접 업로드 파일
│
└─ Channel C: Video Insight
   ├─ YouTube (MCP)
   └─ 자막/요약본 추출

                  ↓
        Google Drive Staging
        (00_INBOX with source validation)
                  ↓
        Claude API Classification
                  ↓
        NotebookLM 4 Categories
        ├─ 01_업무지식 (업무 실무 지식)
        ├─ 02_업무심화 (업무 역량 향상)
        ├─ 03_확장교양 (간접적 도움)
        └─ 04_재미 (흥미 위주)
```

## 🚫 금지 사항

다음은 **절대 시스템에 포함하지 않습니다:**
- ❌ 데일리 노트 (daily notes)
- ❌ 파편화된 메모 (fragmented memos)
- ❌ 일상 기록 (journal entries)
- ❌ The Big 3 외의 임의 소스

## 📁 디렉토리 구조

```
NotebookLM_Staging/
├── 00_INBOX/                    # 소스별 입력 폴더
│   ├── heptabase/              # Channel A
│   ├── marginnote/             # Channel A
│   ├── readwise/               # Channel A
│   ├── manual/                 # Channel B ⭐
│   └── youtube/                # Channel C
│
├── 01_업무지식/                # 업무 실무 지식
├── 02_업무심화/                # 업무 역량 향상
├── 03_확장교양/                # 간접적 도움
├── 04_재미/                    # 흥미 위주
└── 99_OUTPUT/                  # 최종 산출물

_internal_system/pkms/ (로컬 스크립트)
├── scripts/
│   ├── run_pipeline.py         # 전체 파이프라인 통합 실행
│   ├── classifier.py           # 분류 엔진 (소스 검증 포함)
│   ├── manual_classify.py      # 수동 분류
│   ├── watcher.py              # 자동 모니터링
│   ├── root_sweeper.py         # Google Drive 루트 청소
│   ├── readwise_bridge.py      # Readwise 파일 자동 이동
│   ├── readwise_merger.py      # Readwise 파일 병합
│   ├── move_readwise.py        # Readwise 파일 이동
│   ├── gdoc_downloader.py      # Google Docs → Markdown 변환
│   ├── check_status.py         # 시스템 상태 대시보드
│   └── watch_readwise.sh       # Readwise 파일 감시 스크립트
├── config.yaml                 # 설정 (The Big 3 정의)
├── .env                        # 환경 변수 (API 키, 경로)
├── run.sh                      # 간단한 실행 스크립트
├── test_setup.py               # 설정 검증 테스트
└── logs/                       # 분류 이력
```

## 🚀 빠른 시작

### 1. 환경 설정

```bash
cd "/Users/arian/GDrive/NotebookLM_Staging/_internal_system/pkms"

# 가상환경 생성 및 활성화
python3 -m venv .venv
source .venv/bin/activate

# 패키지 설치
pip install anthropic python-dotenv pyyaml watchdog
```

### 2. .env 설정

```bash
# .env 파일에 API 키와 경로 설정
ANTHROPIC_API_KEY=sk-ant-...
ROOT_PATH=/Users/arian/GDrive/NotebookLM_Staging
MODEL_NAME=claude-sonnet-4-6
```

### 3. 설정 검증

```bash
.venv/bin/python3 test_setup.py
```

### 4. 전체 파이프라인 실행

```bash
.venv/bin/python3 scripts/run_pipeline.py
```

또는

```bash
./run.sh
```

## 📋 스크립트별 사용법

### 전체 파이프라인 (권장)

```bash
.venv/bin/python3 scripts/run_pipeline.py
```

5단계 순차 실행:
1. Root Sweeper - Google Drive 루트 청소
2. Readwise Bridge - Readwise 파일 → INBOX 이동
3. Google Docs 다운로드 - .gdoc → Markdown 변환
4. 자동 분류 - Claude API로 4개 카테고리 분류
5. 상태 확인 - 대시보드 출력

### 수동 파일 업로드 (Channel B)

```bash
# 파일을 manual 폴더에 복사
cp your-document.md "/Users/arian/GDrive/NotebookLM_Staging/00_INBOX/manual/"

# 분류 실행
.venv/bin/python3 scripts/manual_classify.py --all --auto
```

### 단계별 실행

```bash
# 1. Readwise 파일 이동
.venv/bin/python3 scripts/readwise_bridge.py

# 2. Google Docs 변환 (필요시)
.venv/bin/python3 scripts/gdoc_downloader.py

# 3. 자동 분류
.venv/bin/python3 scripts/manual_classify.py --all --auto

# 4. 상태 확인
.venv/bin/python3 scripts/check_status.py
```

### 실시간 모니터링

```bash
.venv/bin/python3 scripts/watcher.py
```

## 🔧 설정 (config.yaml)

### The Big 3 채널 정의

```yaml
source_channels:
  channel_a:
    name: "Research Pipeline"
    subfolders: ["heptabase", "marginnote", "readwise"]
    enabled: true

  channel_b:
    name: "Work Files"
    subfolders: ["manual"]
    enabled: true

  channel_c:
    name: "Video Insight"
    subfolders: ["youtube"]
    enabled: true
```

### 분류 카테고리 (NotebookLM 4개)

```yaml
categories:
  work_knowledge: "01_업무지식 - 업무에 직접 활용 가능한 실무 지식"
  work_advanced: "02_업무심화 - 업무 역량 향상을 위한 심화 학습"
  extended_learning: "03_확장교양 - 간접적으로 업무에 도움이 되는 교양"
  entertainment: "04_재미 - 흥미 위주의 콘텐츠"
```

### 소스 검증 설정

```yaml
classification:
  validate_source: true          # The Big 3 검증 필수
  ignore_unknown_sources: true   # 허용되지 않은 소스 무시
  confidence_threshold: 0.75     # 신뢰도 임계값
```

## 📖 분류 기준

### 01_업무지식
- 즉시 업무에 적용 가능한 실무 코드/도구
- 예: API 구현 가이드, 설정 파일 템플릿

### 02_업무심화
- 업무 역량을 향상시키는 심화 학습
- 예: 아키텍처 패턴, 설계 원칙

### 03_확장교양
- 간접적으로 업무에 도움되는 인사이트
- 예: 경영 철학, 심리학, 커리어 전략

### 04_재미
- 순수 흥미/재미 위주 콘텐츠
- 예: 게임 리뷰, 엔터테인먼트

## 🌉 Readwise Bridge

Readwise의 내보내기 경로를 변경할 수 없는 한계를 해결하기 위한 자동 브릿지 시스템

### 작동 원리

```
Google Drive 루트 (Readwise 내보내기)
    ↓ root_sweeper.py + readwise_bridge.py
00_INBOX/readwise/
    ↓ gdoc_downloader.py (선택)
Text/Markdown 변환
    ↓ manual_classify.py
자동 분류 → 4개 카테고리
```

### Readwise 설정 권장사항

**문제**: Readwise가 Google Docs 형식(.gdoc)으로 내보내면 인증 문제 발생

**해결책**: Readwise 설정 변경
1. Readwise 웹사이트 → Settings → Exports → Google Drive
2. **Export Format: Markdown** 선택
3. 파일 이름 패턴: `YYYYMMDD__Title` 유지

## 🛠 문제 해결

### "Source not found" 경고

```
WARNING - Source not found: channel_a_readwise
```

→ INBOX 폴더가 비어있을 때 정상 출력. 파일이 없으면 무시.

### "Invalid source" 경고

```
⚠️ File ignored: not from The Big 3
```

→ 파일이 허용된 3개 채널 중 하나에 있는지 확인

### venv 패키지 오류

```bash
# venv python으로 실행 (시스템 python3 사용 금지)
.venv/bin/python3 scripts/run_pipeline.py
```

## 🎯 핵심 원칙

1. **The Big 3 Only**: 3개 채널 외 모든 소스 무시
2. **Source Validation**: 분류 전 반드시 소스 검증
3. **4 Categories**: NotebookLM의 4개 카테고리만 사용
4. **No Fragmentation**: 데일리/메모 등 파편화된 데이터 제외

## 🔮 다음 단계

- [x] Readwise Bridge 구축 (2026-02-19 완료)
- [x] 맥미니(arian) 환경 마이그레이션 (2026-02-20 완료)
- [ ] Channel A (Heptabase MCP) 연동
- [ ] Channel C (YouTube MCP) 연동
- [ ] NotebookLM API 연동 (질문 세트 자동 실행)
- [ ] 분류 정확도 모니터링

## 📚 참고

- Anthropic Claude API (`claude-sonnet-4-6`)
- NotebookLM
- Google Drive

---

**Project Rule: The Big 3 Channels Only**
