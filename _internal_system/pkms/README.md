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
        ├─ 01_Work_Knowledge (업무 실무 지식)
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
├── 01_Work_Knowledge/          # 업무 실무 지식
├── 02_업무심화/                # 업무 역량 향상
├── 03_확장교양/                # 간접적 도움
├── 04_재미/                    # 흥미 위주
└── 99_OUTPUT/                  # 최종 산출물

pkms/ (로컬 스크립트)
├── scripts/
│   ├── classifier.py           # 분류 엔진 (소스 검증 포함)
│   ├── watcher.py             # 자동 모니터링
│   └── manual_classify.py     # 수동 분류
├── config.yaml                # 설정 (The Big 3 정의)
└── logs/                      # 분류 이력
```

## 🚀 사용 방법

### 1. Channel B (Manual Upload) 사용 - 가장 간단

업무 파일을 수동으로 업로드:

```bash
# 파일을 manual 폴더에 넣기
cp your-document.md "/Users/admin/Google Drive/My Drive/NotebookLM_Staging/00_INBOX/manual/"

# 자동 분류 실행
cd pkms/scripts
python manual_classify.py --all --auto
```

### 2. 실시간 모니터링 (Watcher)

The Big 3 채널을 실시간 모니터링:

```bash
cd pkms/scripts
python watcher.py

# 이제 00_INBOX의 각 폴더에 파일을 추가하면 자동 분류됨
```

### 3. 특정 파일 분류

```bash
# 단일 파일 분류 (확인 후 이동)
python manual_classify.py "path/to/file.md"

# 자동 모드 (확인 없이 이동)
python manual_classify.py "path/to/file.md" --auto
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
  work_knowledge: "01_Work_Knowledge - 업무에 직접 활용 가능한 실무 지식"
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

### 01_Work_Knowledge
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

## 🔍 소스 검증

모든 파일은 분류 전 소스 검증을 거칩니다:

```python
# classifier.py에서 자동 검증
is_valid, channel = classifier.validate_source(file_path)

if not is_valid:
    # The Big 3가 아니면 무시
    logger.warning("File ignored: not from The Big 3")
    return
```

## 📊 워크플로우 예제

### 예제 1: 업무 문서 업로드

```bash
# 1. FastAPI 가이드 작성
echo "# FastAPI Best Practices..." > api-guide.md

# 2. manual 폴더에 업로드
cp api-guide.md "/Users/admin/Google Drive/My Drive/NotebookLM_Staging/00_INBOX/manual/"

# 3. 자동 분류
python scripts/manual_classify.py --all --auto

# 결과: 01_Work_Knowledge/fastapi-production-guide.md
```

### 예제 2: Heptabase에서 가져온 노트

```bash
# Heptabase MCP가 노트를 추출 → 00_INBOX/heptabase/에 저장
# Watcher가 자동으로 감지하여 분류
# 결과: 적절한 카테고리로 자동 이동
```

## 🛠 문제 해결

### "Invalid source" 경고

```
⚠️ File ignored: not from The Big 3
```

→ 파일이 허용된 3개 채널 중 하나에 있는지 확인

### 분류가 실행되지 않음

```bash
# 설정 확인
cat config.yaml | grep validate_source

# 테스트 실행
python scripts/classifier.py
```

## 🎯 핵심 원칙

1. **The Big 3 Only**: 3개 채널 외 모든 소스 무시
2. **Source Validation**: 분류 전 반드시 소스 검증
3. **4 Categories**: NotebookLM의 4개 카테고리만 사용
4. **No Fragmentation**: 데일리/메모 등 파편화된 데이터 제외

## 📄 관련 파일

- `config.yaml`: 전체 설정 (The Big 3 정의)
- `scripts/classifier.py`: 분류 엔진 (소스 검증 로직)
- `scripts/watcher.py`: 실시간 모니터링
- `scripts/manual_classify.py`: 수동 분류 도구

## 🌉 Readwise Bridge (2026-02-19 추가)

Readwise의 내보내기 경로를 변경할 수 없는 한계를 해결하기 위한 자동 브릿지 시스템

### 작동 원리

```
Google Drive 루트 (Readwise 내보내기)
    ↓ readwise_bridge.py
00_INBOX/readwise/
    ↓ gdoc_downloader.py (선택)
Text/Markdown 변환
    ↓ manual_classify.py
자동 분류 → 4개 카테고리
```

### 실행 방법

#### 전체 파이프라인 실행 (권장)

```bash
cd _internal_system/pkms
./run.sh
```

또는

```bash
python3 scripts/run_pipeline.py
```

#### 단계별 실행

```bash
# 1. Readwise 파일 이동 (Google Drive 루트 → INBOX)
python3 scripts/readwise_bridge.py

# 2. Google Docs 다운로드 (필요시)
python3 scripts/gdoc_downloader.py

# 3. 자동 분류
python3 scripts/manual_classify.py --all --auto

# 4. 상태 확인
python3 scripts/check_status.py
```

### Readwise 설정 권장사항

**문제**: Readwise가 Google Docs 형식(.gdoc)으로 내보내면 인증 문제 발생

**해결책**: Readwise 설정 변경
1. Readwise 웹사이트 → Settings → Exports → Google Drive
2. **Export Format: Markdown** 선택
3. 파일 이름 패턴: `YYYYMMDD__Title` 유지

이렇게 하면 .md 파일로 직접 내보내져서 별도 변환 없이 바로 분류가 가능합니다.

### 브릿지 구성 요소

- `readwise_bridge.py`: Google Drive 루트에서 Readwise 파일 탐색 및 이동
- `gdoc_downloader.py`: Google Docs → 텍스트 변환 (선택사항)
- `run_pipeline.py`: 전체 워크플로우 통합 실행
- `run.sh`: 간단한 실행 스크립트

### 탐색 패턴

**폴더**: Readwise, Readwise Highlights, Readwise Reader
**파일**: *Readwise*.{gdoc,md,txt}, *readwise*.{gdoc,md,txt}

## 🔮 다음 단계

- [x] Readwise Bridge 구축 (2026-02-19 완료)
- [ ] Channel A (Heptabase MCP) 연동
- [ ] Channel C (YouTube MCP) 연동
- [ ] NotebookLM API 연동 (질문 세트 자동 실행)
- [ ] 분류 정확도 모니터링

## 📚 참고

- Anthropic Claude API
- NotebookLM
- Google Drive API

---

**Project Rule: The Big 3 Channels Only**
