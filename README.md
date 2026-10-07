# SMC Agentic Orchestrator

**에이전트 기반 의료 AI 모델 통합 플랫폼** — 의료 AI 모델의 통합 활용 및 임상 적용을 위한 시스템

자연어 요청과 의료 데이터를 받아 에이전트가 적합한 의료 AI 특화 모델을 **선택·실행**하고,
모델 결과와 외부 지식을 **하나의 임상 해석**으로 통합하는 Agentic AI Orchestration Platform입니다.
8개 진료과, 94종의 의료 AI 모델을 단일 에이전트 인터페이스에서 실행합니다.

<!-- TODO: 대표 이미지 (시스템 실행 화면) -->

| 94종 | 7종 | 6종 | 0.708 → 0.861 |
|:---:|:---:|:---:|:---:|
| 등록 의료 AI 모델 | 지원 데이터 모달리티 | 지원 분석 태스크 | 공개 벤치마크 8종 평균 AUROC<br>(VLM 단독 → Ours) |

---

## 목차

- [배경](#배경)
- [시스템 아키텍처](#시스템-아키텍처)
- [에이전트 워크플로](#에이전트-워크플로)
- [결과](#결과)
- [저장소 구조](#저장소-구조)
- [실행 방법](#실행-방법)
- [문서](#문서)
- [성과](#성과)

---

## 배경

삼성서울병원 데이터사이언스연구소 AI연구센터에서는 질환 분류, 장기·병변 분할, 병변 검출, 예후 예측,
멀티모달 분석, 생성형 판독문 작성 등 다양한 의료 AI 특화 모델을 개발해 왔습니다.

하지만 모델마다 **입력 형식, 실행 환경, 출력 구조가 달라** 여러 모델을 하나의 워크플로에서 함께
활용하기 어렵습니다. 범용 VLM 하나만으로는 전문 영역의 정확도에 한계가 있습니다.

이 플랫폼은 다음 문제를 해결합니다.

1. **요청 이해** — 임상적 질문과 분석 목적을 파악
2. **모델 선택·실행** — 적합한 의료 AI 모델을 자동으로 선택하고 실행
3. **결과 통합** — 여러 모델의 결과를 외부 지식과 연결해 하나의 임상 해석으로 구성

범용 모델의 한계를 특화 모델로 보완하고, 다양한 의료 AI 모델을 일관된 방식으로 선택·실행하는
Sovereign AI를 구축하는 것을 목표로 합니다.

---

## 시스템 아키텍처

<!-- TODO: 아키텍처 다이어그램 이미지 -->

요청 처리, 에이전트 추론, 모델 실행을 담당하는 **3개 서버를 분리**하고 API로 연결했습니다.
모델별 실행 환경의 차이를 수용하면서 분석 흐름과 결과를 통합 관리합니다.

```text
Client (React / Electron)
        │  HTTP :8100
        ▼
┌─ Routing Server ──────────────────────────────┐
│  요청 접수 · 실행 흐름 조율 · 결과 저장          │
│  MongoDB + GridFS :27017                       │
└──────┬─────────────────────────────┬──────────┘
       │ plan / interpret            │ infer
       ▼                             ▼
┌─ Agent Server :8101 ─────┐   ┌─ Model Execution Server ──────┐
│  모델 선택·실행 계획      │   │  Inference Gateway :8110       │
│  결과 통합 해석           │   │   └ runtime 컨테이너            │
│  ├ vLLM (Gemma 4) :8011  │   │     basic · medical ·          │
│  ├ ChromaDB :8010        │   │     yolo · nnunet              │
│  └ LLM Wiki              │   │  94종 의료 AI 모델              │
└──────────────────────────┘   └────────────────────────────────┘
```

| 구성 요소 | 역할 |
|---|---|
| **[Routing Server](routing-server/)** | 사용자 요청을 접수하고 Agent·Model Execution Server 간 실행 흐름을 조율합니다. 모델 간 의존 관계에 따른 순차·병렬 실행과 비동기 요청 처리·동시성 제어를 구현했습니다. 인증, 환자·방문·분석 관리, 의료 파일 업로드를 담당합니다. |
| **[Agent Server](agent-server/)** | 요청과 모델 메타데이터를 분석해 모델 선택·실행 계획(DAG)을 수립하고, RAG·LLM Wiki로 결과를 해석합니다. 특화 모델이 없거나 실행에 실패하면 RAG·LLM 응답으로 전환하는 Fallback 경로를 둡니다. |
| **[Model Execution Server](model-execution-server/)** | 공통 실행 인터페이스와 Inference Gateway로 모델별 추론 요청을 처리합니다. Docker로 실행 환경을 분리해 기존 구조를 유지한 채 새 모델을 독립적으로 추가할 수 있습니다. |
| **Data & Knowledge Layer** | MongoDB·GridFS로 입력 데이터와 실행 상태·결과 이력을 관리합니다. ChromaDB로 모델 탐색·근거 검색을 지원하고, LLM Wiki에 해석 지식을 축적합니다. |

---

## 에이전트 워크플로

<!-- TODO: 에이전트 워크플로 이미지 -->

```text
① Request Analysis & Planning      ② Model Execution             ③ Result Integration & Interpretation
요청 · 영상 · 모델 메타데이터 분석  →  모델 순차·병렬(DAG) 실행     →  RAG · LLM Wiki로 모델 결과와
적합한 모델 선택, 실행 계획 수립       예측 결과 · 시각적 근거 수집      외부 지식을 통합해 최종 해석 제공
```

1. **요청 분석 및 계획** — 사용자 질의, 환자 정보, 영상을 바탕으로 분석 목적을 파악합니다.
   하이브리드 검색(벡터 + 메타데이터 키워드)으로 후보 모델을 찾고, LLM이 최종 모델을 선택해
   `depends_on`이 포함된 DAG 실행 계획을 만듭니다.
2. **모델 실행** — 독립적인 모델은 병렬로, 선행 출력이 필요한 모델은 순차로 실행합니다.
   DICOM → PNG 같은 포맷 변환을 거쳐 예측 확률, Grad-CAM, ROI, 분할 마스크 등의 근거를 수집합니다.
3. **결과 통합 및 해석** — 모델별 결과와 특성, 검색된 관련 자료(PubMedQA·MedMCQA), Wiki에 축적된
   해석 패턴을 함께 활용해 판독문 형태의 통합 해석을 생성합니다. 해석 결과는 다시 Wiki에 누적됩니다.

추론 모드는 요청에 따라 자동으로 분기합니다.

| 모드 | 처리 |
|---|---|
| `prediction` | 모델 검색 → 특화 모델 실행 → VLM 임상 해석 |
| `clinical` | Wiki + RAG 기반 임상 지식 질의응답 |
| `general` | 의도 분석 → 모델 자동 탐색 → DAG 실행 → VLM 종합 해석 (매칭 모델이 없으면 VLM 단독) |
| `auto` | LLM이 요청 유형을 판단해 위 모드 중 하나로 분기 (기본값) |

---

## 결과

### 등록 모델 현황

<!-- TODO: 시스템 실행 화면 이미지 -->

| 모달리티 | 모델 수 | 대표 태스크 |
|---|---:|---|
| X-ray | 30 | 분류 / 검출 |
| CT | 23 | 분할 / 정량화 |
| MRI | 17 | 분할 / 키포인트 |
| 피부 · 내시경 | 11 | 분류 / 분할 |
| 생체신호 · ECG | 7 | 예측 |
| 안저 · OCT | 2 | 분류 |
| 기타 | 4 | 분류 |
| **합계** | **94** | |

> 94개 모델은 모두 공개 데이터로 학습했습니다.

**진료과별 모델 수**: 호흡기내과 38 · 정형외과 19 · 신경과 10 · 순환기내과 7 · 피부과 7 · 산부인과 5 · 안과 4 · 소화기내과 4

**입력 형식**: 2D 영상(PNG·JPEG) 41 · NIfTI(3D 볼륨) 29 · DICOM 13 · CSV(생체신호·임상 수치) 11

**근거 산출물**: 예측 확률/신뢰도 52 · XAI(Grad-CAM·Occlusion) 42 · 분할 18 · 위치 검출(BBox·Keypoint) 12 · 정량화(부피·각도·측정) 12

**처리 시간**: 요청당 평균 39초 (모델 선택 ~ 특화 모델 실행, MIMIC-CXR 2,145건 실측 기준).
통합 해석(Clinical Board 5단계)을 포함하면 약 3~4분입니다.

### 공개 벤치마크 성능 비교

공개 벤치마크 8종(X-ray · CT · ECG · MRI), 총 32,518건을 VLM 단독(GPT5.6 Luna)과 비교했습니다.

| Public Dataset | Modality | VLM AUROC | VLM Acc. | VLM F1 | **Ours AUROC** | **Ours Acc.** | **Ours F1** |
|---|---|---:|---:|---:|---:|---:|---:|
| MIMIC-CXR | X-ray | 0.820 | 0.766 | 0.733 | **0.845** | **0.772** | 0.672 |
| Montgomery (NLM TB) | X-ray | 0.814 | 0.750 | 0.713 | **0.898** | **0.820** | **0.809** |
| CT-RATE | CT | 0.643 | 0.688 | 0.290 | **0.713** | 0.610 | **0.415** |
| PTB-XL | ECG | 0.682 | 0.630 | 0.626 | **0.827** | **0.710** | **0.688** |
| FracAtlas | X-ray | 0.784 | 0.760 | 0.733 | **0.935** | **0.910** | **0.913** |
| GRAZPEDWRI-DX | X-ray | 0.853 | 0.770 | 0.747 | **0.999** | **0.980** | **0.980** |
| Knee OA (OAI) | X-ray | 0.715 | 0.580 | 0.475 | **0.932** | **0.840** | **0.843** |
| SPIDER | MRI | 0.458 | 0.510 | 0.226 | **0.852** | **0.745** | **0.737** |
| **Mean** | | 0.708 | 0.673 | 0.554 | **0.861** | **0.790** | **0.737** |

8개 데이터셋 모두 AUROC 기준으로 VLM 단독보다 높았고, 전문 영역에서 향상 폭이 가장 컸습니다
(SPIDER +0.394, Knee OA +0.217).

### 통합 해석 사례

<!-- TODO: 통합 해석 결과 사례 이미지 (MIMIC-CXR 흉부 X선) -->

예) “흉부 X-ray의 이상 소견과 의심 부위를 확인해줘”

1. **선택된 모델** — ChestXray14 Multilabel(14개 흉부 소견), CXR Cardiomegaly(심비대), CXR Support Devices(삽입 장치)
2. **모델별 결과** — Cardiomegaly 0.887, Cardiac Enlargement 0.858, Support Devices 0.757 + ROI · Grad-CAM
3. **통합 해석** — 외부 지식과 Wiki를 참고해 확률 값을 판독 문장으로 변환.
   우측 IJ 중심정맥관 검출 및 접근 경로가 정답 판독문과 일치했습니다.

---

## 저장소 구조

```text
SMC-agentic-orchestrator/
├── routing-server/            # FastAPI 백엔드 — 인증, 임상 데이터, 분석 큐, 추론 라우팅
├── agent-server/              # AI Agent — 모델 선택·DAG 계획, RAG, LLM Wiki, VLM 해석
└── model-execution-server/    # Inference Gateway + 공용 GPU runtime 컨테이너
```

각 서버의 상세 구조와 API는 서버별 README를 참고하세요.

## 기술 스택

| 구분 | 기술 |
|---|---|
| LLM / VLM | vLLM, Gemma 4 (31B-it) |
| 벡터 DB / 임베딩 | ChromaDB, sentence-transformers |
| 백엔드 | FastAPI, Uvicorn, Pydantic v2, httpx, asyncio |
| 데이터베이스 | MongoDB 7 (replica set), Motor, GridFS |
| 인증 | JWT, Argon2 |
| 모델 실행 | Docker, NVIDIA PyTorch, MONAI, TorchXRayVision, Ultralytics, nnU-Net v2, TotalSegmentator |
| 의료 파일 | pydicom, nibabel, Pillow |
| 클라이언트 | React, TypeScript, Electron |

---

## 실행 방법

서버마다 실행 환경이 다르므로 아래 순서대로 띄웁니다. 자세한 설정은 각 서버의 README를 참고하세요.

| 순서 | 서비스 | 포트 | 참고 |
|:---:|---|---|---|
| 1 | Model Execution Server (Gateway + runtime) | 8110 | [model-execution-server/README.md](model-execution-server/README.md) |
| 2 | ChromaDB · vLLM · Agent Server | 8010 · 8011 · 8101 | [agent-server/README.md](agent-server/README.md) |
| 3 | MongoDB · Routing Server | 27017 · 8100 | [routing-server/README.md](routing-server/README.md) |

```bash
# 1. Model Execution Server
cd model-execution-server
docker compose -f docker-compose.runtime.yml build runtime-basic
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d --build
curl http://localhost:8110/ready

# 2. Agent Server (ChromaDB, vLLM은 별도 터미널에서 먼저 실행)
cd agent-server
cp .env.example .env
uvicorn main:app --host 0.0.0.0 --port 8101

# 3. Routing Server
cd routing-server
cp .env.example .env            # JWT_SECRET, FILE_SIGNING_SECRET, AGENT_URL 등 설정
docker compose -f docker-compose.mongo.yml up -d
python scan_and_register.py     # 모델 레지스트리 등록
uvicorn main:app --host 0.0.0.0 --port 8100
```

> 모델 가중치와 모델별 패키지(`AI_Models/`, `models/`)는 저장소에 포함되어 있지 않습니다.
> 모델 패키지 규격은 [AI_Models/Manual.md](model-execution-server/AI_Models/Manual.md)를 참고하세요.

---

## 문서

| 문서 | 내용 |
|---|---|
| [routing-server/docs/server-overview.md](routing-server/docs/server-overview.md) | 라우팅 서버 개요 |
| [routing-server/docs/clinical-backend-design.md](routing-server/docs/clinical-backend-design.md) | 임상 백엔드 설계 |
| [agent-server/docs/agentic-general-mode.md](agent-server/docs/agentic-general-mode.md) | 에이전틱 general 모드 설계 |
| [model-execution-server/docs/runtime_architecture.md](model-execution-server/docs/runtime_architecture.md) | 공용 runtime 아키텍처 |
| [model-execution-server/AI_Models/Manual.md](model-execution-server/AI_Models/Manual.md) | 모델 패키지 제출 규격 |

---

## 성과

- **특허** — 「인공지능 에이전트 기반 임상 해석 제공 방법 및 하드웨어 장치」 국내 출원 (10-2026-0113846, 2026-06-22)
- **논문** — *A Multi-Specialty Medical AI Orchestration Platform for Evidence-Integrated Clinical Interpretation* (작성 중)
- **발표** — 의료인공지능융합인재양성사업 우수사례 발표 (2026-10-02)
- **수상** — 2026 AI-Champion (전국민 AI 경진대회) 본선 진출

## 향후 과제

현재는 영상과 텍스트 요청을 함께 입력받아 모델을 선택·실행합니다. 앞으로는 텍스트 요청 없이
**입력 데이터만으로** 분석 목적을 추론하고, 모델 구성부터 통합 리포트 생성까지 자동으로 수행하도록
확장할 계획입니다.

---

## Contributors

- **김윤서 (Yunseo Kim)** — 플랫폼 아키텍처 설계, 백엔드 개발, Agent Orchestration 개발
  · 성균관대학교 삼성융합의과학원 디지털헬스학과 · 삼성서울병원 데이터사이언스연구소 AI연구센터
- **PI** — 정명진, 유학제
