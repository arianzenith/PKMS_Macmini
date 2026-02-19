# 🚀 PKMS 빠른 시작 가이드

## 1️⃣ API 키 설정 (필수)

### Anthropic API 키 발급
1. https://console.anthropic.com/ 접속
2. API Keys 메뉴에서 새 키 생성
3. 키를 복사해둡니다

### 환경 변수 설정

```bash
# .env 파일 생성
cp .env.example .env

# .env 파일 편집 (API 키 입력)
# ANTHROPIC_API_KEY=sk-ant-... 형식으로 입력
```

## 2️⃣ 기본 테스트

### 설정 확인
```bash
python test_setup.py
```

### 분류 엔진 테스트
```bash
cd scripts
python classifier.py
```

## 3️⃣ 사용 방법

### 방법 1: 수동 분류 (추천 - 처음 사용시)

```bash
# inbox에 있는 모든 파일 분류 (하나씩 확인)
python scripts/manual_classify.py --inbox

# 특정 파일만 분류
python scripts/manual_classify.py inbox/my-note.md

# 자동 모드 (확인 없이 바로 이동)
python scripts/manual_classify.py --inbox --auto
```

### 방법 2: 자동 모니터링

```bash
# inbox 폴더 실시간 모니터링 시작
python scripts/watcher.py

# 이제 inbox에 파일을 추가하면 자동으로 분류됩니다
# Ctrl+C로 중지
```

## 4️⃣ 워크플로우 예시

### 새 개념 학습 노트 작성

```bash
# 1. inbox에 파일 생성
echo "# Python Decorators

데코레이터는 함수를 수정하는 함수입니다.

## 기본 문법
@decorator
def function():
    pass
" > inbox/decorators.md

# 2. 분류 실행
python scripts/manual_classify.py inbox/decorators.md
```

결과:
- 카테고리: `concepts` (학습 내용이므로)
- 파일명 제안: `python-decorators.md`
- 최종 위치: `knowledge_base/concepts/python-decorators.md`

### 프로젝트 문서 작성

```bash
# 템플릿 복사
cp templates/project-note.md inbox/my-project.md

# 내용 편집 후 분류
python scripts/manual_classify.py inbox/my-project.md
```

## 5️⃣ 주요 명령어 모음

```bash
# 테스트
python test_setup.py                          # 전체 설정 확인
python scripts/classifier.py                  # 분류 엔진 테스트

# 수동 분류
python scripts/manual_classify.py --inbox     # inbox 전체 분류
python scripts/manual_classify.py FILE        # 특정 파일 분류
python scripts/manual_classify.py --inbox --auto  # 자동 분류

# 자동 모니터링
python scripts/watcher.py                     # 실시간 모니터링

# 로그 확인
cat logs/classifications.log                  # 분류 이력
tail -f logs/pkms.log                        # 실시간 로그
```

## 6️⃣ 팁과 트릭

### 신뢰도 임계값 조정

`config.yaml`에서 조정:
```yaml
classification:
  confidence_threshold: 0.7  # 0.5로 낮추면 더 관대하게 분류
```

### 카테고리 추가

`config.yaml`에 카테고리 추가:
```yaml
categories:
  concepts: "개념, 이론, 학습 내용"
  projects: "프로젝트 관련 문서"
  resources: "참고자료, 링크"
  dailies: "일일 노트, 저널"
  tutorials: "튜토리얼, 가이드"  # 새 카테고리
```

대응하는 폴더 생성:
```bash
mkdir knowledge_base/tutorials
```

### 분류 결과 확인

```bash
# 최근 분류 10개 확인
tail -n 50 logs/classifications.log

# 특정 카테고리로 분류된 파일 목록
ls -l knowledge_base/concepts/
```

## 7️⃣ 문제 해결

### "ANTHROPIC_API_KEY not found" 오류
```bash
# .env 파일 확인
cat .env

# API 키가 올바르게 설정되어 있는지 확인
# ANTHROPIC_API_KEY=sk-ant-... 형식이어야 함
```

### 파일이 이동되지 않음
```bash
# 로그 확인
cat logs/pkms.log

# 수동으로 한 번 실행해보기
python scripts/manual_classify.py inbox/파일명.md
```

### 의존성 오류
```bash
# 가상환경 활성화 확인
which python  # .venv 경로가 나와야 함

# 패키지 재설치
pip install -r requirements.txt  # requirements.txt가 있다면
# 또는
pip install anthropic python-dotenv pyyaml watchdog
```

## 8️⃣ 다음 단계

- [ ] 일주일 동안 사용해보고 카테고리 조정
- [ ] 자주 사용하는 템플릿 추가
- [ ] 분류 정확도 개선을 위한 프롬프트 튜닝
- [ ] Git 저장소 초기화하여 버전 관리
- [ ] Obsidian 등의 노트 앱과 연동

## 📚 더 알아보기

- `README.md`: 전체 프로젝트 문서
- `config.yaml`: 설정 파일 (카테고리, 임계값 등)
- `scripts/`: Python 스크립트 소스 코드
