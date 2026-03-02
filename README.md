# Thought Factory v1.0

01_Inbox를 감시하다가 새 파일이 들어오면 Gemini 2.5 Pro로 제텔카스텐 분석 후 02_Archive에 저장하는 자동화 PKMS 시스템.

## 구조

```
본진/
├── 01_Inbox/          ← 원료 입고 (Readwise, AppleNotes 파일)
├── 02_Archive/        ← 지식 출고 (Zettelkasten 결과물)
└── _internal_system/
    ├── factory_one.py ← 메인 감시 엔진
    └── pkms/.env      ← 설정 (git 제외)
```

## 실행

```bash
python3 _internal_system/factory_one.py
```

## 규칙

- 파일명: `YYMMDD_Zettelkasten_HHMMSS.txt`
- 모델: Gemini 2.5 Pro
- 원본: 처리 후 `02_Archive/sources/`로 이동
- 보고: 완료 시 Webhook 전송
