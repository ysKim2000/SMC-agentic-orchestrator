# maple-platform

의료 전문가가 AI 모델과 대화하듯 상호작용하는 **임상 AI 플랫폼**입니다.
도메인 특화 AI 모델 실행, RAG 기반 임상 지식 검색, Wiki, 첨부파일 종합 분석(에이전틱 오케스트레이션)을 하나의 채팅 인터페이스에서 제공합니다.

---

## 시스템 구성

Cloud GPU 서버 2대 + 로컬 클라이언트로 구성된다.

```
[maple-client]  로컬(노트북) — React + Electron UI
      │  HTTP
      ▼
── GPU 서버 A · A100 ×1 ──────────────────────────────────────────────
   [maple-routing-server]          FastAPI 백엔드 (Port 8100)
   [maple-model-execution-server]  AI 모델 컨테이너 (Docker, Port 9020~9023)
──────────────────────────────────────────────────────────────────────
      │  사설망 직접 통신 (인터넷 미경유)
      │  라우팅 → Agent 호출
      ▼
── GPU 서버 B · H100 ×1  ◄ 이 저장소 ─────────────────────────────────
   [maple-agent-server]  AI Agent (Port 8101)
       ├── vLLM      (Port 8011, gemma-4-31B-it)
       ├── ChromaDB  (Port 8010, 벡터 RAG)
       └── /wiki     (마크다운 지식 누적 레이어)
──────────────────────────────────────────────────────────────────────
```



| 저장소 | 역할 |
|---|---|
| `maple-client` | 채팅 UI, 추론 결과 시각화 (React + Electron) |
| `maple-routing-server` | 요청 라우팅, AI 컨테이너 오케스트레이션, 결과 저장 |
| **`maple-agent-server`** | **쿼리 분류, 모델 자동발견·DAG 계획, RAG 임상 해석, VLM 종합** |
| `maple-model-execution-server` | 도메인 특화 AI 모델 컨테이너 (UNet3D, YOLO26x, ChestXray14 등) |

---

## 핵심 기능

### 1. 4-Mode 추론 라우팅
사용자 쿼리와 업로드 파일을 분석해 최적 모드로 자동 분기합니다.

| 모드 | 트리거 | 처리 |
|---|---|---|
| `prediction` | 의료 영상/데이터 + 특화 모델 쿼리 | ChromaDB 모델 검색 → 컨테이너 실행 → VLM 임상 해석 |
| `clinical` | 임상 지식 질문 | Wiki + RAG(PubMedQA·MedMCQA) → LLM 즉시 답변 |
| `general` | 첨부파일 + 자연어 종합 분석 | 의도분석 → 모델 자동발견(하이브리드 검색+LLM 선택) → DAG 실행 → VLM 종합 해석 (매칭 모델 0개면 VLM 단독) |
| `auto` | 기본값 | LLM이 쿼리 유형 판단 후 자동 분기 |

### 2. Wiki + RAG 하이브리드 지식 레이어

RAG 단독 방식과 달리, **매 해석 결과를 Wiki에 누적**하여 지식이 쌓일수록 해석 품질이 향상됩니다.

```
쿼리 수신
  ↓
Wiki index.md → 관련 모델/개념 페이지 파악
  ↓
ChromaDB 병렬 검색 (maple_models + maple_knowledge)
  ↓
gemma-4-31B-it → 임상 응답 생성
  ↓
해석 결과 → wiki/interpretations/ 에 파일링 (지식 누적)
```

| 레이어 | 역할 | 저장소 |
|---|---|---|
| Wiki | AI 모델 메타데이터, 임상 해석 패턴 누적 | `/wiki/*.md` |
| RAG | PubMedQA·MedMCQA 논문/QA 벡터 검색 | ChromaDB `maple_knowledge` |
| 모델 레지스트리 | 자연어 쿼리 기반 모델 탐색 | ChromaDB `maple_models` |

#### Wiki 디렉터리 — 무엇이 어디에 쌓이나

| 경로 | 생성 시점 | 내용 |
|---|---|---|
| `index.md` | 모델 등록 | 전체 목록 — Models/Departments 섹션에 `[[project/model]]` 링크 |
| `models/{project}/{model}.md` | 모델 등록 (`/register`) | 모델 **메타데이터 카드 + 해석 실행 이력**(아래) |
| `departments/{dept}.md` | 모델 등록 | 진료과별 소속 모델 목록 |
| `interpretations/{project}/{model}/{timestamp}.md` | interpret 실행 | VLM 임상 **판독문 전문**(실행 1회 = 파일 1개, 누적) |
| `concepts/{topic}.md` | clinical 답변 | 임상 지식 Q&A 캐시(같은 토픽이면 덮어씀) |
| `log.md` | 등록/삭제 | 이력 로그 (**gitignore**) |

**`models/….md` 에 남는 것** — "이 모델이 무엇인가"를 서술하는 정적 카드 + 해석이 언제 돌았는지의 인덱스:
- `기본 정보`: 진료과 · 프로젝트 · task_type · required_data · result_type · **provides / requires**(DAG 의존 태그)
- `설명`: 모델 용도 설명
- `임상 해석 패턴`: interpret가 돌 때마다 `- {날짜} 해석 완료 (…/{timestamp}.md)` 한 줄씩 추가 → 전문 파일로 링크
- `관련 개념`: 질환·키워드

**`interpretations/…/{timestamp}.md` 에 남는 것** — interpret가 생성한 **판독문 본문 그대로**:
- `## Summary` · `## Key Imaging Findings`(모델별 결과 표 + `[IMG:role]` 이미지 마커) · `## Clinical Interpretation` · `## Recommendations or Limitations`
- 파일명 `YYYYMMDD_HHMMSSffffff.md`, 덮어쓰지 않고 실행마다 누적(추적 대상) → 해석이 쌓일수록 지식이 축적

> 요약: **`models/` = 모델 정의(메타) + 해석 인덱스**, **`interpretations/` = 그때 실제로 낸 판독문(본문)**. 모델 페이지는 가볍게 유지하고 무거운 전문은 날짜별 파일로 분리한다.

### 3. 에이전틱 general 오케스트레이션

`general` 모드는 자연어 쿼리 + 첨부파일을 받아 **관련 모델을 스스로 찾아 실행하고 결과를 종합**합니다.

```
POST /agent/plan (general)                (라우팅이 실행)            POST /agent/interpret
  ↓ 의도분석 (신체부위·질환군)              DAG 실행 (병렬+순차)         ↓ 원본이미지+결과+메타
  ↓ 하이브리드 후보 recall (벡터+키워드)  →  포맷 정합(DICOM→PNG 등)  →   ↓ VLM 종합 판독문
  ↓ LLM 모델 선택 (매직 문턱 없음)            결과 집계                    ← [IMG:role] 인라인
  ← execution_plan.steps[] (DAG)                                        (모델 0개면 VLM 단독)
```

- **모델 탐색**: 단일 벡터 유사도 문턱 대신 **하이브리드 recall(벡터+메타 키워드) + LLM 선택**으로
  교차언어 임베딩에서도 관련 모델을 놓치지 않음. 예: "폐렴 찾아줘" → YOLO(검출)+ChestXray14(분류) 동시.
- **DAG 실행 계획**: 각 step에 `depends_on`(선행 step_id)을 담아 병렬/순차를 표현. 독립 모델은 병렬.
- **의존 체인**: 모델 등록 시 `provides`/`requires` 태그로 "선행 출력이 필요한" 관계를 선언 → 자동 wiring.
- 상세: [docs/agentic-general-mode.md](docs/agentic-general-mode.md)

### 4. 다단계 AI 파이프라인 (DAG)

단일 요청으로 복수의 특화 모델이 **병렬·순차 DAG**로 실행되는 파이프라인을 지원합니다.

```
예시: 뇌종양 분할 (병렬)
  BraTS2020_FLAIR_UNet3D : FLAIR MRI 분할   ┐
  BraTS2020_T1ce_UNet3D  : T1ce MRI 분할    ├─ 병렬 실행 (depends_on: [])
  ...                                        ┘
  최종 — VLM 임상 해석 : 분할 결과 이미지 → 임상 소견 텍스트 생성
```

### 5. VLM 임상 해석 with 이미지 인라인 렌더링

```
Agent 해석 출력:
  "우측 전두엽에 Whole Tumor 소견이 확인됩니다 [IMG:seg_overlay] Enhancing Tumor 영역은..."

프론트엔드:
  [IMG:seg_overlay] 토큰을 분할 결과 이미지로 인라인 치환하여 렌더링
```

---

## 기술 스택

| 분류 | 기술 | 세부 내용 |
|---|---|---|
| **LLM / VLM** | vLLM + gemma-4-31B-it | NHN Cloud H100 ×1, TP=1, bf16 |
| **벡터 DB** | ChromaDB 0.5 | 모델 레지스트리 + 임상 논문 RAG |
| **임베딩** | sentence-transformers `all-MiniLM-L6-v2` | 쿼리·모델 설명 벡터화 |
| **AI 프레임워크** | FastAPI + asyncio | 전구간 비동기, 병렬 RAG 검색 |
| **특화 모델** | UNet3D(BraTS), YOLO26x(RSNA), ChestXray14 | Docker 컨테이너 격리 실행 |
| **프론트엔드** | React 19 + TypeScript + Electron | 웹/데스크탑 듀얼 빌드 |
| **백엔드** | FastAPI + Motor(MongoDB) + httpx | 비동기 DB, 비동기 컨테이너 호출 |
| **파일 처리** | pydicom, nibabel, Pillow | DICOM·NIfTI → PNG 변환, base64 인코딩 |

---

## 등록 AI 모델

| 모델 | 진료과 | 입력 | 출력 | 기술 |
|---|---|---|---|---|
| BraTS2020_FLAIR_UNet3D | 신경과 | NIfTI (FLAIR) | 뇌종양 분할 오버레이 | 3D Segmentation |
| BraTS2020_T1_UNet3D | 신경과 | NIfTI (T1) | 뇌종양 분할 오버레이 | 3D Segmentation |
| BraTS2020_T1ce_UNet3D | 신경과 | NIfTI (T1ce) | 뇌종양 분할 오버레이 | 3D Segmentation |
| BraTS2020_T2_UNet3D | 신경과 | NIfTI (T2) | 뇌종양 분할 오버레이 | 3D Segmentation |
| ChestXray14_Multilabel_Classification | 호흡기내과(Pulmonology) | PNG/JPG | GradCAM 오버레이 + 14개 흉부 소견 확률 | Classification + XAI |
| YOLO26x_RSNA_Pneumonia | 호흡기내과(Pulmonology) | DICOM | 폐렴 의심 영역 bbox 오버레이 | Object Detection |

---
> (추가 예정)


## 폴더 구조

```
maple-agent-server/
├── main.py                 # FastAPI 진입점 (Port 8101)
├── requirements.txt
├── .env.example
├── docs/                   # 설계 문서 (agentic-general-mode.md 등)
├── wiki/                   # 지식 누적 레이어
│   ├── index.md            # 전체 Wiki 목록
│   ├── models/             # AI 모델별 페이지
│   ├── departments/        # 진료과별 페이지
│   └── interpretations/    # interpret 판독문 전문 — 실행마다 날짜별 1파일 누적
├── services/
│   ├── agent_service.py    # 핵심 오케스트레이션 (plan/interpret, general DAG)
│   ├── wiki_service.py     # Wiki 읽기/쓰기
│   └── rag_service.py      # ChromaDB 병렬 검색
├── llm/
│   ├── client.py           # vLLM OpenAI 호환 클라이언트
│   └── prompts/            # 프롬프트 패키지 (system·builders·schemas·utils)
├── rag/
│   ├── embedder.py         # ChromaDB upsert / delete / get_all_models
│   └── retriever.py        # ChromaDB query + 결과 포맷
└── routers/
    ├── agent.py            # /agent/plan, /agent/interpret
    └── models.py           # /agent/models/*
```

---

## 빠른 시작


```bash
# 이 서버는 conda env 2개로 분리 사용 — 각 터미널에서 해당 env를 활성화한다.
#   maple-agent : vLLM 전용             (pydantic 2.13)
#   maple-app   : ChromaDB + FastAPI 앱 (pydantic 2.9.2)
#   ※ 섞으면 vLLM이 올린 pydantic 때문에 chromadb 0.5.20이 깨진다(등록/조회 422·400).

# 터미널 1 — ChromaDB   (경로는 반드시 절대경로! 상대경로면 빈 DB가 새로 생겨 모델 0개가 된다)
conda activate maple-app
chroma run --host 0.0.0.0 --port 8010 --path ./chroma_data


# 터미널 2 — vLLM
#   시스템 CUDA 툴킷(/usr/local/cuda)이 없어, flashinfer JIT가 쓰는 nvcc/ninja를 conda·pip에서
#   끌어와야 한다. 아래 env 없이 그냥 실행하면 "Engine core initialization failed"로 죽는다.
conda activate maple-agent
export CUDA_HOME=$CONDA_PREFIX/lib/python3.10/site-packages/nvidia/cu13   # nvcc (ninja는 env/bin)
export PATH=$CUDA_HOME/bin:$PATH
export VLLM_USE_FLASHINFER_SAMPLER=0                                       # flashinfer 샘플러 JIT 회피
python -m vllm.entrypoints.openai.api_server \
  --model google/gemma-4-31B-it \
  --tensor-parallel-size 2 \
  --port 8011 \
  --max-model-len 8192 \
  --max-num-seqs 32
# (B200 ×2면 --tensor-parallel-size 2, 위 CUDA_HOME/VLLM_* env는 불필요)

# 터미널 3 — Agent 서버
conda activate maple-app
uvicorn main:app --host 0.0.0.0 --port 8101 --reload
```

### 최초 실행 시 (1회, 현 H100 서버에는 이미 해둬서 안해도 됨)

```bash
# 1. 임상 지식 베이스 구축 — PubMedQA·MedMCQA → ChromaDB maple_knowledge
# 주의: 오래 걸림
python scripts/ingest_knowledge.py
```

**2. AI 모델 등록 (ChromaDB `maple_models`)** — 두 경로 중 택1:

```bash
# (A) agent-server 단독/협업용 — MongoDB·타 레포 불필요
#     scripts/register_models.py의 MODELS를 agent /register로 등록
python scripts/register_models.py

# (B) 전체 플랫폼 — MongoDB + AI_Models 원천(meta.json)까지 동기화
#     scan_and_register.py는 maple-routing-server에 있고, AI_MODELS_DIR로
#     ../maple-model-execution-server/AI_Models 를 읽는다
cd ../maple-routing-server
AGENT_URL=http://localhost:8101 python scan_and_register.py
```

> Agent 워크스페이스에서만 개발한다면 **(A)** 면 충분하다(등록은 ChromaDB+wiki만 씀 — MongoDB 불필요).
> `register_models.py`는 model-execution-server `meta.json`을 미러링한 편의 스크립트이므로,
> 모델이 바뀌면 (B)로 재등록하거나 이 파일을 갱신한다.

---

## API

### Agent

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/agent/plan` | 모드별 쿼리 라우팅 + 모델 실행 계획 수립 |
| `POST` | `/agent/interpret` | AI 추론 결과 VLM 임상 해석 |

### Models

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/agent/models/register` | 모델 등록 → Wiki + ChromaDB 자동 동기화 |
| `DELETE` | `/agent/models/{model_id}` | 모델 삭제 → Wiki + ChromaDB 자동 동기화 |
| `GET` | `/agent/models/lookup` | 자연어 쿼리로 모델 탐색 |
| `GET` | `/health` | vLLM + ChromaDB 연결 상태 |

### POST `/agent/plan`

**Request**

```json
{
  "query": "가슴 사진 폐렴인지 판독해줘",
  "mode": "general",
  "uploaded_types": ["dicom"],
  "history": [],
  "attachments": [
    {
      "type": "dicom",
      "filename": "chest.dcm",
      "images": ["data:image/png;base64,..."],
      "text": "",
      "metadata": {"modality": "CR", "body_part": "CHEST", "age": "075Y", "sex": "M"},
      "tabular": null
    }
  ]
}
```

| 파라미터 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `query` | string | 필수 | 사용자 자연어 요청 |
| `mode` | string | `"auto"` | `auto` \| `clinical` \| `prediction` \| `general` |
| `uploaded_types` | string[] | `[]` | 업로드된 파일 타입 목록 (e.g. `["dicom"]`). 비면 `attachments[].type`에서 파생 |
| `attachments` | object[] | `[]` | 라우팅이 정규화한 첨부 — `{type, filename, images[], text, metadata, tabular}` (라우팅이 파싱·비식별화) |
| `images` | string[] | `[]` | (레거시) `attachments` 이전 base64 이미지 목록 |
| `csv_data` | dict[] | `[]` | (레거시) `pd.read_csv().to_dict("records")` 결과 |

**모드별 동작**

| mode | Agent 처리 |
|---|---|
| `prediction` | ChromaDB `maple_models` 검색 → required_data 매칭 → 실행 계획 반환 |
| `clinical` | Wiki + RAG(PubMedQA·MedMCQA) 병렬 검색 → LLM 즉시 답변 |
| `general` | 의도분석 → 하이브리드 모델 recall + LLM 선택 → DAG 실행계획 반환 (모델 0개면 VLM 단독 `message`) |
| `auto` | 첨부 있으면 `general`; 없으면 LLM이 `prediction` / `clinical` / `general` 판단 |

**Response**

```json
// prediction — 실행 계획 수립
{
  "status": "ready",
  "mode": "prediction",
  "execution_plan": {
    "steps": [{"step": 1, "model": "YOLO26x_RSNA_Pneumonia", "department": "Pulmonology", "project": "RSNA_Pneumonia_YOLO26x"}]
  }
}

// prediction — 파일 미첨부
{"status": "requires_input", "mode": "prediction", "message": "..."}

// prediction — 모델 미매칭
{"status": "no_model", "mode": "prediction", "message": "..."}

// prediction — 파일 타입 불일치
{"status": "type_mismatch", "mismatched_models": [{"model": "YOLO26x_RSNA_Pneumonia", "required": ["dcm"], "uploaded": ["csv"]}]}

// clinical
{"query_type": "knowledge", "mode": "clinical", "message": "...", "sources": [...], "model_suggestion": "..."}

// general — 모델 선택됨 (DAG 실행계획)
{
  "status": "ready",
  "mode": "general",
  "execution_plan": {
    "steps": [
      {"step_id": "s1", "model_name": "YOLO26x_RSNA_Pneumonia", "department": "Pulmonology", "project": "RSNA_Pneumonia_YOLO26x", "task_type": "bbox detection", "result_type": ["bbox_overlay", "detection_predictions"], "required_data": ["dcm"], "depends_on": []},
      {"step_id": "s2", "model_name": "ChestXray14_Multilabel_Classification", "department": "Pulmonology", "project": "ChestXray14_Multilabel_Classification", "task_type": "classification", "result_type": ["gradcam_overlay", "classification_probabilities"], "required_data": ["png", "jpg", "jpeg"], "depends_on": []}
    ]
  },
  "fallback_vlm_only": false,
  "message": ""
}

// general — 매칭 모델 0개 (VLM 단독 fallback)
{"status": "ready", "mode": "general", "execution_plan": {"steps": []}, "fallback_vlm_only": true, "message": "..."}
```

> `general` 모드: `fallback_vlm_only: true`면 `steps: []` + `message`(VLM 답변)를 그대로 사용. `false`면 라우팅이 `execution_plan.steps[]`를 DAG로 실행한 뒤 `/agent/interpret`를 호출한다.

### POST `/agent/interpret`

**Request**

```json
{
  "query": "가슴 사진 폐렴인지 판독해줘",
  "task": {"department": "Pulmonology", "project": "RSNA_Pneumonia_YOLO26x"},
  "execution_context": {
    "mode": "general",
    "plan": {"steps": []},
    "attachments": [
      {"type": "dicom", "filename": "chest.dcm", "images": ["data:image/png;base64,..."], "metadata": {"modality": "CR", "body_part": "CHEST", "age": "075Y"}}
    ]
  },
  "step_results": [
    {
      "step": "s1",
      "model": "YOLO26x_RSNA_Pneumonia",
      "result_type": ["bbox_overlay", "detection_predictions"],
      "predictions": [{"pred_name": "pneumonia_opacity", "conf": 0.18}],
      "model_output": {"detection_count": 2},
      "images": [{"role": "bbox_overlay", "data": "data:image/png;base64,..."}]
    },
    {
      "step": "s2",
      "model": "ChestXray14_Multilabel_Classification",
      "result_type": ["gradcam_overlay", "classification_probabilities"],
      "predictions": [{"finding": "Pneumonia", "prob": 0.61}],
      "images": [{"role": "gradcam_overlay", "data": "data:image/png;base64,..."}]
    }
  ]
}
```

> `execution_context.attachments`는 **원본 스캔 이미지 + 메타데이터**로, general 종합 판독의 임상 컨텍스트로 VLM에 함께 투입된다. `step`은 prediction=순번(int), general DAG=step_id(str). `result_type`·`role`이 null이면 `""`로 흡수(422 방지).

**Response**

```json
{
  "status": "confirmed",
  "result": {
    "finding": "우측 하엽에 폐렴 의심 혼탁 소견이 확인됩니다.\n[IMG:bbox_overlay]",
    "interpretation": "영상 소견과 분류 확률을 함께 고려하면 폐렴 가능성이 있습니다.",
    "recommendation": "임상 증상 및 검사실 소견과 연계한 전문의 검토를 권고합니다.",
    "risk_tier": "moderate",
    "confidence": {
      "display": 0.61,
      "source_model": "ChestXray14_Multilabel_Classification",
      "task_type": "classification",
      "model_scores": [],
      "conflict": false,
      "score_gap": null
    }
  },
  "interpretation": "## 소견\n...\n\n## 임상적 해석\n...\n\n## 권장조치 및 한계\n...",
  "interpretation_raw": "기존 클라이언트 호환용 마크다운",
  "board": {},
  "escalation_reason": null,
  "images": {"bbox_overlay": "data:image/png;base64,..."}
}
```

> `[IMG:role]` 토큰 위치에 `images` 맵의 이미지를 인라인 렌더링합니다.
> 동일 classification 태스크 모델이 둘 이상이면 대표 confidence를 임의로
> 집계하지 않고 `model_scores`와 `score_gap`을 노출합니다. `score_gap > 0.2`는
> `pending_review` 및 `model_disagreement` 사유가 됩니다. Detection과
> classification처럼 서로 다른 태스크의 점수는 비교하지 않습니다.

**Board on/off**

```env
BOARD_MODE=on   # 기본값: Clinical Board 검증 결과 사용
BOARD_MODE=off  # 기존 단일 VLM/LLM interpret 사용
```

환경변수 변경 후 agent-server를 재시작해야 적용됩니다. `off`에서는 Reader,
Challenger, Evidence, Guardian을 호출하지 않으며 기존 `interpretation/images`
계약을 그대로 반환합니다.

`on`의 Clinical Board 처리 흐름은 다음과 같습니다.

```text
Reader(구조화 1차 판독)
  → Challenger(블라인드 감별진단)
  → Evidence(RAG 주장별 근거 대조)
  → Guardian(don't-miss·위험도 검토)
  → Calibration(동일 classification 모델 score gap)
  → Orchestrator(최종 5필드 결과와 검토 상태 조립)
```

Guardian veto, `high/critical` 위험도, Board 구성요소 실패 또는 동일 태스크
모델의 `score_gap > 0.2`가 발생하면 `status=pending_review`로 반환합니다.
LLM JSON 파싱 실패와 필수 필드 누락은 자동 확정하지 않고 fail-closed 처리합니다.

**자동 재시도 및 폴백**

| 조건 | 처리 |
|---|---|
| VLM HTTP 오류 | 이미지 없이 텍스트 전용 LLM 재시도 |
| 품질 부족 (텍스트 < 120자, 섹션 < 3개, 문장 < 4개) | 동일 이미지로 1회 재생성 |
| 재시도 후에도 품질 부족 | 서버 측 폴백 텍스트 반환 |

### POST `/agent/models/register`

```json
{
  "id": "pulmonology-rsna-pneumonia-yolo26x",
  "model_name": "YOLO26x_RSNA_Pneumonia",
  "department": "Pulmonology",
  "project": "RSNA_Pneumonia_YOLO26x",
  "description": "Pneumonia detection model on chest X-ray. DICOM 입력 → 폐렴 의심 영역 bbox 검출",
  "task_type": "bbox detection",
  "disease": "pneumonia, lung opacity, chest x-ray",
  "required_data": ["dcm"],
  "result_type": ["bbox_overlay", "detection_predictions"],
  "provides": [],
  "requires": []
}
```

**필드**

| 필드 | 타입 | 설명 |
|---|---|---|
| `id` | string | ChromaDB 문서 id |
| `model_name` / `department` / `project` | string | 모델 식별·실행 호출 키 |
| `description` | string | 모델 설명 (임베딩·LLM 선택에 사용 — 다루는 소견을 영어로 명시 권장) |
| `task_type` | string | `bbox detection` \| `classification` \| `segmentation` … |
| `disease` | string | 대상 질환·소견 키워드 (콤마 구분) — general 키워드 recall에 사용 |
| `required_data` | string[] | 입력 파일 확장자 (e.g. `["dcm"]`, `["png","jpg","jpeg"]`, `["nii.gz","nii"]`) |
| `result_type` | string[] | 출력 타입 목록 (e.g. `["bbox_overlay","detection_predictions"]`) |
| `provides` / `requires` | string[] | DAG 체인 태그 (기본 `[]`) |

→ `wiki/models/RSNA_Pneumonia_YOLO26x/YOLO26x_RSNA_Pneumonia.md` 생성 + `wiki/index.md` 업데이트 + ChromaDB `maple_models` 등록 자동 수행

> `provides`/`requires`: general DAG 체인 구성용 태그. consumer의 `requires`를 provider의 `provides`와
> 정확 문자열 매칭해 `depends_on`을 자동 도출한다(예: 검출모델 `provides:["sij_roi"]` → 분류모델 `requires:["sij_roi"]`).
> 독립 모델은 빈 배열.

---

## 추론 흐름

### prediction 모드

```
POST /agent/plan {mode: "prediction", query, uploaded_types}
  ↓ ChromaDB maple_models 검색 (유사도 0.40 미만 제거) → required_data 매칭
  ← {status: "ready", execution_plan: {steps: [...]}}

(백엔드가 AI 컨테이너 실행 후)

POST /agent/interpret {query, task, step_results}
  ↓ Wiki 페이지 수집 → 이미지 role 매핑
  ↓ VLM(180s) 또는 LLM(120s) → 품질 검증 → 필요 시 재시도
  ↓ 해석 결과 wiki/interpretations/ 에 누적
  ← {interpretation, images}
```

### clinical 모드

```
POST /agent/plan {mode: "clinical", query}
  ↓ Wiki 키워드 검색 + ChromaDB maple_models·maple_knowledge 병렬 검색
  ↓ LLM 임상 답변 생성
  ← {message, sources, model_suggestion}
```

### general 모드 (에이전틱 오케스트레이션)

```
POST /agent/plan {mode: "general", query, attachments}
  ↓ 의도분석 (신체부위·질환군·영어 검색쿼리)
  ↓ 하이브리드 후보 recall (벡터 + 메타 키워드) → LLM 모델 선택
  ↓ provides/requires 기반 DAG 실행계획 생성
  ← {execution_plan: {steps: [...]}, fallback_vlm_only}
     (모델 0개면 fallback_vlm_only=true + message에 VLM 단독 답변)

(라우팅이 DAG 실행 — 병렬/순차 + 포맷 정합(DICOM→PNG 등) → 결과 집계 후)

POST /agent/interpret {query, step_results, execution_context.attachments}
  ↓ 원본 스캔 이미지+메타 + 모델 결과 → VLM 종합 판독문
  ← {interpretation, images}
```

상세: [docs/agentic-general-mode.md](docs/agentic-general-mode.md)

---

## Wiki 스키마

### index.md

```markdown
# Maple AI Agent Wiki Index

## Models
- [[RSNA_Pneumonia_YOLO26x/YOLO26x_RSNA_Pneumonia]] - Pulmonology

## Departments
- [[Pulmonology]]

## Interpretations
- [[RSNA_Pneumonia_YOLO26x/YOLO26x_RSNA_Pneumonia/20260525_...]]
```

### 모델 페이지 (`wiki/models/ModelName.md`)

```markdown
# ModelName

## 기본 정보
- **진료과:** Pulmonology
- **task_type:** bbox detection
- **required_data:** [dcm]
- **result_type:** [bbox_overlay, detection_predictions]

## 임상 해석 패턴
### [2026-04-15] 해석 패턴
누적된 해석 결과...
```

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8011/v1` | vLLM OpenAI 호환 API URL |
| `LLM_MODEL` | `google/gemma-4-31B-it` | 텍스트/VLM 모델명 |
| `CHROMA_HOST` | `localhost` | ChromaDB 호스트 |
| `CHROMA_PORT` | `8010` | ChromaDB 포트 |
| `WIKI_PATH` | `./wiki` | Wiki 파일 경로 |

---

## 트러블슈팅

### ChromaDB tenant 오류

```
ValueError: Tenant default_tenant not found
```

ChromaDB를 먼저 실행한 뒤 `ingest_knowledge.py`로 초기화합니다.

```bash
chroma run --host 0.0.0.0 --port 8010 --path ./chroma_data
python scripts/ingest_knowledge.py
```

### vLLM 시작 시 GPU 메모리 부족

```
ValueError: Free memory on device cuda:1 is less than desired GPU memory utilization
```

다른 프로세스가 GPU를 점유 중입니다. `nvidia-smi`로 확인 후 해당 프로세스를 종료하세요.

### vLLM gemma4 아키텍처 인식 불가

```
model type `gemma4` but Transformers does not recognize this architecture
```

```bash
pip install --upgrade transformers
```

### `no_model` 반환 — 모델이 등록되어 있는데 검색 실패

1. ChromaDB 실행 상태 확인
2. `POST /agent/models/register` 정상 호출 여부 확인
3. 쿼리가 모델 설명과 너무 달라 유사도 0.40 미만인 경우 쿼리를 더 구체적으로 작성
