# maple-routing-server

MAPLE 의료 AI 플랫폼의 FastAPI 백엔드입니다.

환자·예약·방문·분석·임상 메모·채팅을 MongoDB에 저장하고, 의료 파일을
GridFS로 관리하며, AI Agent와 모델 실행 서버로 추론 요청을 라우팅합니다.

## 주요 기능

- JWT access/refresh 인증과 의사·관리자 역할 분리
- 환자 등록, 재방문 예약, 일정 변경·취소·진료 완료
- DICOM, NIfTI, CSV, 일반 이미지 업로드와 메타데이터 추출
- 의사별 직렬 분석 큐와 전역 동시 실행 제한
- 분석 lease heartbeat, 만료 복구, 재시도 및 취소 처리
- 모델 결과 기반 위험도 정책(`Low`, `High`, `Critical`)
- 분석 결과·파생 이미지 조회와 만료되는 서명 URL
- 환자의 전체 방문 이력을 활용하는 임상 채팅
- 기존 `/inference/` 호환 및 Agent/DAG 기반 범용 추론
- 8개 진료과 72종 모델 레지스트리·위험도 정책 관리자 API
- 재실행 가능한 개발·시연용 시드

상세 API 계약은 [클라이언트 API 인계 문서](docs/client-api-handoff.md), 전체
설계는 [임상 백엔드 설계](docs/clinical-backend-design.md)를 참고하세요.

## 시스템 구성

```text
maple-client (React/Electron, 사용자 PC)
        │
        │ HTTP :8100
        ▼
maple-routing-server (이 저장소, A100 호스트)
        ├── MongoDB replica set :27017
        ├── maple-inference :8110
        │     └── runtime-* 모델 컨테이너 :8000 (72종 모델)
        └── maple-agent-server :8101 (H100 사설망)
              ├── vLLM
              └── ChromaDB / Wiki
```

운영 구성:

- 라우팅 서버: A100 호스트의 conda `maple` 환경에서 Uvicorn으로 실행
- MongoDB: `docker-compose.mongo.yml`의 single-node replica set `rs0`
- 모델 게이트웨이·런타임: `maple-model-execution-server` 저장소에서 관리
- AI Agent: H100 서버에서 실행하며 A100과 사설망으로 통신
- 로컬 클라이언트: 필요하면 SSH `-L 8100:localhost:8100` 터널 사용

## 기술 스택

| 구분 | 기술 |
|---|---|
| API | FastAPI, Uvicorn, Pydantic v2 |
| DB | MongoDB 7 replica set, Motor, PyMongo, GridFS |
| 인증 | JWT(`python-jose`), Argon2 |
| HTTP | httpx |
| 의료 파일 | pydicom, pylibjpeg, nibabel, Pillow, pypdf |
| 데이터 처리 | NumPy, pandas |

Python 3.10 환경을 기준으로 합니다. 정확한 패키지 버전은
[`requirements.txt`](requirements.txt)에 고정되어 있습니다.

## 애플리케이션 구조

```text
maple-routing-server/
├── main.py                         # FastAPI 앱, lifespan, worker 기동
├── dependencies.py                 # 서비스·저장소 의존성 주입
├── config/
│   ├── settings.py                 # 환경 변수
│   ├── database.py                 # MongoDB 연결·인덱스
│   └── logging_filters.py          # 서명 URL 로그 마스킹
├── controllers/
│   ├── auth_controller.py
│   ├── clinical_controller.py
│   ├── analysis_controller.py
│   ├── chat_controller.py
│   ├── note_controller.py
│   ├── inference_controller.py
│   ├── projects_controller.py
│   ├── pipeline_controller.py
│   └── admin_controller.py
├── services/
│   ├── clinical_service.py
│   ├── clinical_inference_service.py
│   ├── analysis_worker.py
│   ├── analysis_query_service.py
│   ├── medical_file_service.py
│   ├── file_access_service.py
│   ├── clinical_chat_service.py
│   ├── auth_service.py
│   ├── risk_policy_service.py
│   └── ...
├── repositories/                   # MongoDB 접근 계층
├── models/                         # Pydantic 스키마
├── scripts/                        # 의사·환자·시연 데이터 시드
├── tests/                          # unittest 테스트
└── docs/                           # 설계·클라이언트 계약·운영 문서
```

## 빠른 시작

### 1. Python 환경

```bash
conda create -n maple python=3.10 -y
conda activate maple
pip install -r requirements.txt
```

### 2. 환경 변수

```bash
cp .env.example .env
```

최소한 다음 값을 실제 환경에 맞게 설정하세요.

```dotenv
MONGO_URI=mongodb://localhost:27017/?replicaSet=rs0
DB_NAME=maple_db
REQUIRE_REPLICA_SET=true

JWT_SECRET=<충분히-긴-무작위-값>
FILE_SIGNING_SECRET=<별도의-긴-무작위-값>

AGENT_URL=http://<H100-사설-IP>:8101
MAPLE_INFERENCE_URL=http://localhost:8110
AI_MODELS_DIR=../maple-model-execution-server/AI_Models
```

`REQUIRE_REPLICA_SET=true`일 때 기본 개발용 `JWT_SECRET`을 사용하면 서버가
시작되지 않습니다.

### 3. MongoDB replica set

```bash
docker compose -f docker-compose.mongo.yml up -d

docker exec maple-mongo mongosh --quiet --eval \
  'db.adminCommand({ping:1}); rs.status().set'
```

`maple-mongo-data` named volume을 사용하므로 컨테이너를 다시 만들어도 볼륨을
삭제하지 않는 한 데이터가 유지됩니다.

### 4. 모델 레지스트리

현재 모델 실행 서버의 `AI_Models/{department}/{model}/meta.json`과
`models/{model}/config.yaml`을 기준으로 **8개 진료과 72종**이 등록 대상입니다.

| 진료과 | 모델 수 |
|---|---:|
| Cardiology | 7 |
| Dermatology | 7 |
| Gastroenterology | 4 |
| Neurology | 9 |
| Obstetrics | 5 |
| Ophthalmology | 4 |
| Orthopedics | 19 |
| Pulmonology | 17 |
| **합계** | **72** |

```bash
python scan_and_register.py --dry-run
python scan_and_register.py
```

`--dry-run`의 `발견된 모델` 수는 위 경로에서 정확히 3단계 깊이에 있는
`meta.json`의 개수입니다. 모델 실행 서버의 자산이 변경되면 실제 스캔 결과를
기준으로 이 표도 함께 갱신해야 합니다.

### 5. 초기 의사 계정

```bash
export SEED_DOCTOR_PASSWORD='<doctor-password>'
export SEED_ADMIN_PASSWORD='<admin-password>'
python -m scripts.seed_doctors
unset SEED_DOCTOR_PASSWORD SEED_ADMIN_PASSWORD
```

### 6. 개발 데이터

일반 데모 40명:

```bash
python -m scripts.seed_clinical_demo
DEMO_DATE=2026-08-03 python -m scripts.seed_clinical_demo
```

2026-07-30 전용 시연 일정:

```bash
python -m scripts.seed_showcase_20260730
```

전용 시드는 20명의 예약과 `done=8`, `analyzing=4`,
`waiting_for_files=8` 상태를 재실행 가능하게 구성합니다. 예상하지 못한 다른
활성 예약이 같은 날짜에 있으면 임의로 삭제하지 않고 중단합니다.

### 7. 서버 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

- API 문서: <http://localhost:8100/docs>
- 상태 확인: <http://localhost:8100/health>

정상 응답:

```json
{"status":"ok","service":"maple-ai-backend"}
```

## 인증과 권한

`/auth/signup`, `/auth/login`, `/auth/refresh`를 제외한 주요 API는 Bearer access
token이 필요합니다.

```http
Authorization: Bearer <access-token>
```

| 역할 | 접근 범위 |
|---|---|
| `doctor` | 환자·예약·방문·분석·파일·채팅·추론·프로젝트 조회 |
| `admin` | doctor 권한 + `/admin/*` 모델 레지스트리 관리 |

인증 API:

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/auth/signup` | 의사 계정 생성 |
| `POST` | `/auth/login` | access/refresh token 발급 |
| `POST` | `/auth/refresh` | refresh token 회전 |
| `POST` | `/auth/logout` | 세션 폐기 |
| `GET` | `/auth/me` | 현재 사용자 |

## 임상 API

### 환자와 예약

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/patients?date=YYYY-MM-DD` | 날짜별 활성 예약 환자 목록 |
| `GET` | `/patients/search?q=...` | 환자 ID·이름 검색 |
| `POST` | `/patients` | 신규 환자·예약·방문 등록 |
| `GET` | `/patients/{patient_id}` | 환자 상세 |
| `PATCH` | `/patients/{patient_id}` | 진료 상태 수정 |
| `POST` | `/patients/{patient_id}/appointments` | 기존 환자 재방문 등록 |
| `GET` | `/patients/{patient_id}/appointments` | 예약 이력 |
| `PATCH` | `/appointments/{appointment_id}` | 시간·취소·진료 완료 변경 |

등록 요청은 `multipart/form-data`이며 `name`, `gender`, `birth_date`,
`appt_date`, `appt_time`, `note`, `files`를 사용합니다.

예약 규칙:

- 진료시간 `08:00`~`17:00`
- 10분 단위
- 과거 날짜·현재 시각 이전 등록 허용
- 같은 의사·같은 시각의 활성 예약은 `409`

파일이 없으면 분석 문서를 만들지 않고 목록 응답에서
`analysis_status=waiting_for_files`를 합성합니다.

### 방문과 분석

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/patients/{patient_id}/visits` | 방문 이력 |
| `GET` | `/patients/{patient_id}/visits/{visit_id}` | 방문·최신 분석 상세 |
| `POST` | `/visits/{visit_id}/analyses` | 기존 방문 재분석 요청 |
| `GET` | `/visits/{visit_id}/analyses` | 방문의 분석 이력 |
| `GET` | `/analyses/{analysis_id}` | 분석 상태·결과 polling |
| `POST` | `/analyses/{analysis_id}/access-urls` | 결과 파일 URL 일괄 갱신 |

분석 상태:

```text
waiting_for_files   API 합성 상태(분석 문서 없음)
queued              실행 대기
analyzing           worker 실행 중
done                분석 완료
failed              재시도 후 실패
cancelled           예약 취소 등으로 취소
superseded          새 분석으로 대체
```

worker 동작:

- 같은 `requested_by_doctor_id`의 분석은 직렬 실행
- 서로 다른 의사는 `ANALYSIS_GLOBAL_CONCURRENCY` 범위에서 병렬 실행
- `scheduled_at`, `queued_at` 순서로 claim
- lease heartbeat와 프로세스 재시작 시 만료 작업 복구
- 취소된 예약은 실행하지 않음

### 파일

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/files/{file_id}?exp=...&sig=...` | 서명 검증 후 파일 stream |
| `GET` | `/files/{file_id}/metadata` | 파일·DICOM 메타데이터 |
| `GET` | `/config/client` | 업로드 한도·허용 확장자 |

지원 확장자:

```text
.dcm .dicom .nii .nii.gz .csv .png .jpg .jpeg
```

기본 업로드 한도는 512 MiB이며 `MAX_UPLOAD_BYTES`로 변경할 수 있습니다.

### 임상 채팅과 메모

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/visits/{visit_id}/chat` | 방문 채팅 이력 |
| `POST` | `/visits/{visit_id}/chat` | 전체 환자 이력을 문맥으로 질문 |
| `GET` | `/patients/{patient_id}/notes` | 임상 메모 목록 |
| `POST` | `/patients/{patient_id}/notes` | 임상 메모 작성 |

채팅은 환자의 여러 방문에 걸친 완료 분석을 문맥으로 사용합니다. 응답의
`[IMG:{analysis_id}:{role}:{index}]` 토큰은 서버에 등록된 결과 파일만 참조할 수
있습니다.

## 추론·모델 API

`/inference/*`, `/pipeline/*`, `/projects/*`는 doctor token이 필요하고,
`/admin/*`는 admin 역할이 필요합니다.

| Method | Endpoint | 설명 |
|---|---|---|
| `POST` | `/inference/` | 레거시 multipart 추론(`auto`, `clinical`, `prediction`, `general`) |
| `POST` | `/inference/agent/plan` | 브라우저용 Agent plan 프록시 |
| `GET` | `/inference/agent/models/lookup` | 모델 검색 프록시 |
| `POST` | `/pipeline/run` | 명시적 다단계 파이프라인 |
| `GET` | `/projects/` | 진료과 목록 |
| `GET` | `/projects/{department}` | 프로젝트 목록 |
| `GET` | `/projects/{department}/{project}` | 모델 목록 |
| `PUT` | `/admin/models/{department}/{project}/risk-policy` | 위험도 정책 등록 |

`POST /inference/`는 기존 클라이언트 호환용입니다. 신규 임상 화면에서는
환자·방문과 연결되는 `/visits/{visit_id}/analyses` 및
`/visits/{visit_id}/chat` 사용을 권장합니다.

## 위험도 판정

최종 위험도는 Agent의 자연어가 아니라 라우팅 서버의 `RiskPolicyService`가
모델 레지스트리의 `risk_policy`와 정규화된 모델 출력을 평가해 결정합니다.

```text
risk_status=pending       아직 분석 전
risk_status=assessed      정책 평가 완료
risk_status=unavailable   정책 없음 또는 평가 불가
```

정책이 없을 때 confidence만으로 `Low`를 임의 생성하지 않습니다.

## MongoDB 컬렉션

| 영역 | 컬렉션 |
|---|---|
| 인증 | `doctors`, `auth_sessions` |
| 임상 | `patients`, `appointments`, `visits`, `analyses`, `notes` |
| 채팅 | `chat_messages` |
| 파일 | `medical_files`, `fs.files`, `fs.chunks` |
| 큐 | `doctor_analysis_locks` |
| 모델 | `departments`, `inference_results` |
| 운영 | `counters`, `access_logs` |

환자 등록은 여러 컬렉션을 transaction으로 함께 변경하므로 MongoDB replica
set이 필요합니다. 주요 unique/TTL 인덱스는 서버 시작 시 확인합니다.

## 환경 변수

전체 예시는 [`.env.example`](.env.example)을 참고하세요.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB URI |
| `DB_NAME` | `maple_db` | DB 이름 |
| `REQUIRE_REPLICA_SET` | `false` | replica set 필수 여부 |
| `JWT_SECRET` | 개발 기본값 | JWT 서명 키, 운영 필수 |
| `ACCESS_TOKEN_MINUTES` | `30` | access token 수명 |
| `REFRESH_TOKEN_DAYS` | `7` | refresh token 수명 |
| `APP_TIMEZONE` | `Asia/Seoul` | 예약·화면 기준 시간대 |
| `MAX_UPLOAD_BYTES` | `536870912` | 파일당 업로드 한도 |
| `GRIDFS_BUCKET` | `fs` | GridFS bucket |
| `FILE_SIGNING_SECRET` | `JWT_SECRET` | 파일 URL 서명 키 |
| `FILE_SIGNED_URL_SECONDS` | `300` | 서명 URL 수명 |
| `ANALYSIS_WORKER_ENABLED` | `true` | 프로세스 내 worker 실행 |
| `ANALYSIS_GLOBAL_CONCURRENCY` | `3` | 동시 의사 분석 수 |
| `ANALYSIS_LEASE_SECONDS` | `300` | 분석 lease |
| `ANALYSIS_MAX_ATTEMPTS` | `2` | 최대 시도 수 |
| `AGENT_URL` | `http://localhost:8101` | AI Agent 주소 |
| `AGENT_TIMEOUT` | `300` | Agent timeout |
| `MAPLE_INFERENCE_URL` | `http://localhost:8110` | 모델 게이트웨이 |
| `CLIENT_ORIGINS` | 개발 origin | CORS 허용 목록 |

## 테스트

```bash
python -m unittest discover -s tests -v
```

현재 테스트는 다음을 포함합니다.

- 인증·refresh token 회전·logout
- 예약 시간대·10분 격자·과거 예약
- 업로드 한도와 streaming staging
- DICOM 메타데이터 비식별화
- 위험도 정책
- 모델 실행 DAG와 런타임 라우팅
- 파일 서명과 로그 마스킹
- 채팅 이미지 토큰 검증

## 운영 점검

```bash
# MongoDB
docker compose -f docker-compose.mongo.yml ps
docker exec maple-mongo mongosh --quiet --eval 'rs.status().set'

# Agent
curl "$AGENT_URL/health"

# 모델 게이트웨이
curl "$MAPLE_INFERENCE_URL$MAPLE_INFERENCE_HEALTH_PATH"

# 서버
curl http://localhost:8100/health
```

문제가 생기면 먼저 다음을 확인하세요.

1. `.env`의 `JWT_SECRET`, MongoDB URI, Agent·게이트웨이 주소
2. `maple-mongo` replica set 상태
3. `departments` 모델 레지스트리 존재 여부
4. A100과 H100 사이 사설망 연결
5. `data/` 및 `CLINICAL_WORK_DIR` 쓰기 권한
6. worker 로그의 lease 만료·재시도 메시지

## 관련 문서

- [서버 개요와 내부 동작](docs/server-overview.md)
- [클라이언트 API 계약](docs/client-api-handoff.md)
- [임상 백엔드 설계](docs/clinical-backend-design.md)
- [임상 v4 구현 보고서](docs/clinical-v4-implementation-report.md)
- [DICOM 메타데이터 API](docs/client-dicom-metadata-api.md)
- [의료 메타데이터 렌더링 계약](docs/client-medical-metadata-rendering-contract.md)
- [업로드 제한 인계](docs/client-upload-limit-handoff.md)
- [모델 실행 런타임 라우팅](docs/model-execution-runtime-routing-requirements.md)

## 보안·데이터 주의사항

- `.env`, 의료 원본, 결과 파일, JWT·서명 키를 Git에 커밋하지 마세요.
- 운영에서는 `JWT_SECRET`과 `FILE_SIGNING_SECRET`을 별도로 설정하세요.
- DICOM 환자 식별정보는 API에 노출하기 전에 비식별화합니다.
- 파일 접근 로그는 `AUDIT_RETENTION_DAYS` 이후 TTL로 정리됩니다.
- 서명 URL의 `sig` 값은 로그 필터에서 마스킹됩니다.
