# 에이전틱 general 모드 — agent-server 구현

> **상태**: Step 1(plan)·Step 3(interpret) 구현 완료 · 폐렴 2모델(YOLO 검출 + ChestXray14 분류) end-to-end 검증됨
> **브랜치**: `feat/general-orchestration`
> **크로스레포**: `maple-routing-server`(Step 2 DAG 실행·Track B 포맷변환), `maple-model-execution-server`(meta.json provides/requires·컨테이너)
> **참고**: routing 측 계약 문서 `maple-routing-server/docs/agentic-general-mode.md`

general 모드를 "VLM 단독 분석"에서 **"의도분석 → 모델 자동 발견 → DAG 실행 → VLM 종합 해석"**
오케스트레이션으로 재설계했다. agent-server는 **Step 1(계획 수립)** 과 **Step 3(종합 해석)** 을 담당하고,
Step 2(모델 실행·집계)는 routing이 담당한다.

---

## 1. 3-Step 흐름

```
Step 1 (agent, LLM)              Step 2 (routing)             Step 3 (agent, VLM)
─────────────────────            ──────────────────           ─────────────────────
POST /agent/plan (general)       execution_plan.steps[] 수신   POST /agent/interpret
 의도분석(신체부위·질환군)    Res  DAG 실행 (병렬+순차)      Res  원본이미지+결과+메타
 하이브리드 후보 recall       ─→  포맷 정합(Track B)       ─→  → VLM 종합 판독문
 LLM 모델 선택                    결과 집계(step_results)        (결과이미지 [IMG:role] 인라인)
 DAG 실행계획 생성
```

- 모델 0개 선택 시 `fallback_vlm_only=true` → Step 2/3 없이 VLM 단독 답변(`message`).

---

## 2. plan 응답 계약 (`POST /agent/plan`, mode=general)

```jsonc
{
  "status": "ready",
  "mode": "general",
  "execution_plan": {
    "steps": [
      {
        "step_id": "s1",              // DAG 노드 식별자 (depends_on 참조 대상)
        "model_name": "...",
        "department": "...",          // 실행 호출 키
        "project": "...",             // 실행 호출 키
        "task_type": "...",
        "result_type": "image",
        "required_data": ["dicom"],   // routing이 포맷 정합에 사용
        "depends_on": []              // 선행 step_id 목록 (빈 배열=병렬)
      }
    ]
  },
  "fallback_vlm_only": false,         // true면 steps:[] + message에 VLM 답변
  "message": ""
}
```

구현: [routers/agent.py](../routers/agent.py) `PlanRequest`, [services/agent_service.py](../services/agent_service.py) `_general`.

---

## 3. 모델 탐색 방법론 — 하이브리드 recall + LLM 선택

단일 벡터 유사도 문턱(예: 0.65)은 교차언어(한글 문서/쿼리) 임베딩(`all-MiniLM-L6-v2`)에서
관련 모델을 놓친다. 그래서 **매직 문턱을 폐기**하고 여러 신호를 합친다.

1. **의도분석** (`_analyze_intent`) — LLM이 신체부위·질환군·모달리티 + 영어 `search_query` 추출.
   한글→영어 브릿지 역할(임베딩이 영어에 강함).
2. **하이브리드 후보 recall** (`_discover_models`) — 문턱 없이 넓게:
   - 벡터 검색(`search_models`, ChromaDB)
   - **메타데이터/doc_text 키워드 매칭** — `disease`에 박힌 조건(예: `pneumonia`)을 임베딩 점수와
     무관하게 확정 포착. (`embedder.get_all_models`가 doc_text 동봉)
3. **LLM 선택** (`_select_models`, `build_model_select_prompt`) — 후보 전체 메타를 LLM에 주고
   적절한 모델 선택. 매직 문턱 대체. task_type이 요청 표현과 달라도(분류 vs 검출) 해당 질환을
   다루면 포함하도록 지시. 후보 info는 **잘리지 않게 충분히**(1200자) 넘겨 `disease` 목록 노출.
4. **DAG 생성** (`_build_execution_dag`) — provides/requires로 의존 wiring + 사이클 제거.

> **검증**: "폐렴 찾아줘"/"detection of pneumonia" → YOLO(검출) + ChestXray14(분류) 2개를
> 안정적으로 선택. BraTS 4개는 LLM이 제외.

---

## 4. provides / requires (DAG 체인)

- 모델 등록(`POST /agent/models/register`, [routers/models.py](../routers/models.py))이 `provides`/`requires`
  태그를 수용해 ChromaDB 메타 + Wiki에 저장.
- `_build_execution_dag`가 consumer의 `requires`를 provider의 `provides`와 **정확 문자열 매칭**해
  `depends_on`을 자동 도출. 선행 못 채우는 모델은 연쇄 제외, 사이클은 제거.
- 규약: 소문자 snake_case, `<대상>_<산출물>`(roi/bbox/mask/seg/crop/heatmap/keypoints).
- **현재 등록 6개 모델은 전부 독립(태그 없음) → 병렬.** 태그 주입은 model-execution-server meta.json 몫.

---

## 5. Step 2 — DAG 실행·집계 (routing 담당)

agent가 준 `execution_plan.steps[]`를 routing이 실행한다. agent는 이 단계를 수행하지 않지만,
계약 이해를 위해 동작을 요약한다. (구현: `maple-routing-server/services/pipeline_service.py`,
`services/format_convert.py`)

1. **위상 검증** — `depends_on` 참조 유효성 + Kahn 사이클 검사. 사이클이면 실행 거부.
2. **DAG 실행** — step마다 asyncio 태스크. 각 태스크는 자기 `depends_on` 태스크 완료를 먼저 await.
   - `depends_on: []` 노드들은 **동시(병렬) 시작**
   - 의존 노드는 **선행 완료 즉시 시작** (barrier 낭비 없음 → 최대 병렬)
3. **입력 포맷 정합 (Track B)** — `_resolve_step_input`:
   - step의 `required_data` 카테고리와 업로드 원본이 맞으면 그대로 사용
   - 다르면 **변환 레지스트리**(`CONVERTERS`: `dicom→image`, `nifti→image` …)로 변환.
     예: ChestXray14(`image` 요구) + DICOM 업로드 → **DICOM을 PNG로 변환**해 투입
   - 의존 노드는 원본 + 선행 출력의 ROI를 병합해 전달
   - 변환/매칭 불가 시 그 step만 **skip + `errors[]` 기록**(전체 중단 아님)
4. **집계** — 결과를 **step별 1엔트리 평탄 리스트(`step_results`)** 로. 병렬/순차 구조는
   `depends_on`에 보존. `result_type`이 컨테이너에서 null이면 DB메타→plan step→이미지유무로 coalesce.
   일부 실패 시 `status: "partial"` + `errors[]`, 전부 실패면 error.
5. 집계된 `step_results` + 원본 `attachments`를 **Step 3 interpret로 전달**.

---

## 6. Step 3 — interpret (종합 해석, agent 담당)

`POST /agent/interpret`가 실행·집계 결과를 받아 VLM 종합 판독문을 생성한다.
구현: [services/agent_service.py](../services/agent_service.py) `interpret`, [llm/prompts/builders.py](../llm/prompts/builders.py) `build_interpret_prompt`.

**입력**
- `step_results[]`: `step`(=step_id), `model`, `result_type`, `predictions`, `model_output`, `images[{role,data}]`
- `execution_context`: `mode`, `plan`, `attachments[]`(원본 스캔 이미지+메타), `attachments_meta[]`(전환기 폴백)

**처리 흐름**
1. **Wiki 컨텍스트 수집** — 각 step 모델의 Wiki 페이지를 모아 배경 지식으로 첨부.
2. **이미지 수집**
   - `step_results[].images` → `role→data` 맵(`image_map`, 프론트 렌더용) + VLM 입력용 base64 목록.
     `role`이 있으면 `[IMG:role]` 토큰 후보, 없으면 컨텍스트 이미지로만 사용.
   - `execution_context.attachments[].images`(원본 스캔) → **결과 이미지 뒤에** VLM 입력으로 추가.
3. **프롬프트 조립** — 실행 결과 요약 + Wiki + **Original Scan Context**(모달리티·부위·나이 등 메타)
   + 이미지 삽입 규칙(`[IMG:role]`는 해당 소견을 설명하는 문장 뒤에만).
4. **생성** — 이미지 있으면 VLM, 없으면 LLM. HTTP/타임아웃 오류 시 텍스트 전용으로 폴백 재시도.
5. **품질 검사 & 재시도** (`_needs_interpretation_retry`) — 분석 텍스트가 부족하면(<120자 / 섹션<3 /
   문장<4) 1회 재생성, 그래도 미달이면 **서버 폴백 판독문**(보수적 4섹션 템플릿) 반환.
6. **토큰 보완** (`_ensure_all_image_tokens`) — 본문에 빠진 `[IMG:role]`는 "Attached Images" 부록으로 첨부.
7. **Wiki 누적** — 각 모델 페이지에 해석 결과 파일링(`wiki/interpretations/`, 런타임 산출물이라 gitignore).
8. **응답** — `{interpretation, interpretation_raw, images}` (`images`는 `role→dataURI` 맵; 프론트가
   `[IMG:role]` 토큰을 해당 이미지로 인라인 치환).

**null 내성**: routing이 `result_type`/`role`을 null로 보내도 `""`로 흡수(422 방지).

---

## 7. 크로스레포 분담

| 레포 | 책임 |
|---|---|
| **maple-agent-server** (이 레포) | Step 1(의도·recall·선택·DAG 계획), Step 3(VLM 종합), 모델 등록 수용 |
| **maple-routing-server** | Step 2(DAG 실행·집계), **Track B 포맷 정합**(required_data에 맞춰 DICOM→PNG 등 변환), interpret 호출 |
| **maple-model-execution-server** | 모델 컨테이너, meta.json(`description`·`provides`/`requires`), Grad-CAM 등 출력 이미지 |

### 유효성 검증 = 탈락 아닌 "포맷 정합"
agent는 확장자 불일치로 모델을 탈락시키지 않는다(관련 모델을 전부 계획에 실음). 입력을 각 모델이
요구하는 포맷으로 변환하는 것은 routing Track B의 역할이며, agent는 step의 `required_data`로 전달한다.

---

## 8. 주요 결정 / 수정 로그

| 커밋 | 내용 |
|---|---|
| `f5f071c` | Step 1 — plan이 의도분석→모델탐색→DAG 실행계획 반환 |
| `9c06433` | Step 3 — interpret가 원본 스캔 이미지+메타 종합 |
| `e9e7ead` | 모델 탐색을 하이브리드 recall + LLM 선택으로 (매직 문턱 폐기) |
| `6982169` | interpret step_results의 null result_type/role/model 흡수 (422 방지) |
| `ab663b4` | 선택 프롬프트 300자 잘림이 disease 라벨을 가리던 문제 수정(→1200자) |

### 알려진 데이터/인프라 이슈 (agent 밖)
- **ChestXray14 Grad-CAM 미표시**: `runtime-medical` 컨테이너에 opencv/libGL 부재 → Grad-CAM 실패 시
  맨 원본 이미지로 조용히 폴백. → `docker/Dockerfile.runtime-medical`에 opencv-python-headless + libgl1/libglib2.0 추가 필요.
- **모델 description**: 임베딩·선택 품질을 위해 영어로, 다루는 소견을 전부 명시 권장 (meta.json).
