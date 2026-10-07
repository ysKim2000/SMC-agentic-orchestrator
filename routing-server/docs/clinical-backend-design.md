# MAPLE Clinical Backend 상세 설계

> 대상 저장소: `maple-routing-server`  
> 연동 클라이언트: `maple-platform/maple-client` `dev` 브랜치  
> 기준 문서: `docs/backend-requirements.md`  
> 문서 상태: 구현 기준안  
> 개정: v3 — Electron origin, 예약 변경·취소, 진료 의사, 서명 URL 배치 반영  
> 시간대: `Asia/Seoul`

---

## 1. 목적

현재 MAPLE Clinical Chat의 데모 상수 데이터를 서버 데이터로 전환하고 다음 흐름을 지원한다.

1. 의사가 직접 가입하고 로그인한다.
2. 접수 모달은 기존 환자 검색을 우선하고, 신규 환자만 새 환자 ID를 발급한다.
3. 신규 환자 등록 또는 기존 환자 재방문 접수 시 예약과 방문이 함께 생성된다.
4. 의료 파일이 있으면 분석 작업이 자동으로 대기열에 등록된다.
5. 같은 의사가 요청한 분석은 순차 실행되고, 서로 다른 의사의 분석은 병렬 실행된다.
6. 예약 시간이 지났거나 가까운 환자의 분석을 우선한다.
7. 환자의 전체 방문, 분석 결과, 메모, 채팅을 영구 보관한다.
8. 후속 질문은 현재 방문뿐 아니라 과거 방문의 채팅과 분석 결과까지 종합한다.
9. 환자 및 분석 데이터는 물리적으로 삭제하지 않는다.

이 문서는 데이터 구조와 API 계약을 우선 확정한다. 모델 실행 서버와 Agent 서버의 내부 구현 변경은 각 서버 담당 범위로 분리하되, 라우팅 서버가 요구하는 연동 계약은 포함한다.

---

## 2. 확정된 제품 정책

### 2.1 계정

- 의사는 직접 가입할 수 있다.
- 가입한 계정은 승인 절차 없이 즉시 활성화한다.
- 일반 회원가입으로 만들 수 있는 역할은 `doctor`뿐이다.
- 초기 계정은 3개를 만든다.
  - Doctor 2명
  - Doctor와 Admin 역할을 함께 가진 의사 1명
- Admin 전용 화면과 관리 기능은 후속 단계로 미룬다.
- 병원은 우선 하나만 운영한다.
- 모든 주요 문서에는 향후 다기관 확장을 위해 `hospital_id`를 저장한다.

### 2.2 환자와 방문

- 환자 ID는 서버가 원자적으로 발급한다.
- 클라이언트는 `patient_id`를 생성하거나 수정하지 않는다.
- 접수 모달의 기본 흐름은 기존 환자 검색 후 재방문 예약이다.
- 신규 환자 등록 시 환자, 예약, 첫 방문을 함께 생성한다.
- 기존 환자 재방문 시 환자 기본정보는 유지하고 예약과 새 방문만 생성한다.
- 환자를 등록한 로그인 의사를 담당 의사로 자동 연결한다.
- 키, 몸무게, 혈압 등 활력징후는 1차 구현 범위에서 제외한다.
- 환자와 의료 기록을 물리적으로 삭제하는 API는 만들지 않는다.
- 예약은 서울 기준 오늘 이후, `08:00`~`17:00`, 10분 단위만 허용한다.
- 같은 의사의 같은 예약 시각에는 활성 예약을 한 건만 허용한다.

### 2.3 분석

- 환자 등록 시 파일이 있으면 별도 선택 없이 분석 대기열에 등록한다.
- 등록 모달의 `autoAnalyze` 체크박스와 필드는 제거한다.
- 파일이 없으면 분석 문서를 만들지 않고 목록 집계에서 `waiting_for_files`로 표시한다.
- 분석 요청 API는 분석 완료를 기다리지 않고 작업 ID를 즉시 반환한다.
- 같은 의사가 요청한 분석은 한 번에 하나만 실행한다.
- 서로 다른 의사의 분석은 병렬 실행할 수 있다.
- 아직 시작하지 않은 작업은 예약 시각이 빠른 순서로 실행한다.
- 실행 중인 분석은 더 이른 예약 작업이 새로 들어와도 중단하지 않는다.
- 분석 실패가 같은 의사의 다음 작업을 막지 않는다.

### 2.4 날짜 표시

- DB에는 `Today` 같은 표시 문자열을 저장하지 않는다.
- 날짜와 시각은 실제 값으로 저장한다.
- 클라이언트가 실제 서울 날짜와 같은 날짜에만 `(Today)`를 붙인다.

### 2.5 채팅

- 채팅과 분석 결과는 방문별로 저장한다.
- 후속 질문은 환자의 모든 방문 이력을 고려한다.
- DB에는 전체 원문을 보존한다.
- Agent 입력은 토큰 한도를 고려해 최근 원문과 과거 요약을 조합한다.

### 2.6 보안 경계

- Phase 2부터 `/health`, 가입·로그인·토큰 재발급, 유효한 서명 URL 파일 GET을 제외한 모든 API는 Bearer 인증을 요구한다.
- 기존 `/projects`, `/inference`, `/pipeline`, `/admin`도 예외 없이 보호한다.
- `/admin` 변경 API는 `admin` 역할만 호출할 수 있다.
- CORS 전면 개방은 Phase 2에서 제거한다.
- Electron 프로덕션 렌더러는 `file://` 대신 표준·보안 커스텀 프로토콜 `app://maple`로 제공한다.
- API CORS는 개발 `http://localhost:3000`과 프로덕션 `app://maple`만 허용한다.

---

## 3. 전체 구조

MongoDB 데이터베이스는 기존 `maple_db` 하나를 유지하고 도메인별 컬렉션을 분리한다.

```text
maple_db
├── doctors
├── auth_sessions
├── patients
├── appointments
├── visits
├── analyses
├── doctor_analysis_locks
├── notes
├── chat_messages
├── medical_files
├── access_logs
├── counters
├── departments              기존
├── inference_results        기존, 단계적으로 analyses로 대체
├── fs.files                 GridFS
└── fs.chunks                GridFS
```

도메인 관계:

```text
Doctor
  ├── AuthSession
  ├── Assigned Patient
  └── Requested Analysis Queue

Patient
  ├── Appointment
  │     └── Visit
  │           ├── Analysis 1..N
  │           ├── ChatMessage 0..N
  │           └── MedicalFile 0..N
  └── Note 0..N
```

환자, 예약, 방문, 분석을 분리하는 이유:

- 한 환자가 여러 번 예약될 수 있다.
- 한 예약이 취소되거나 실제 방문으로 이어지지 않을 수 있다.
- 한 방문에서 여러 모델을 실행하거나 재분석할 수 있다.
- 실패 후 재시도해도 이전 실행 기록을 보존할 수 있다.

---

## 4. 식별자와 공통 규칙

### 4.1 외부 공개 ID

| 리소스 | 형식 | 예 |
|---|---|---|
| 환자 | `PT-{sequence}` | `PT-1040` |
| 예약 | `A-{YYYYMMDD}-{sequence}` | `A-20260729-0001` |
| 방문 | `V-{YYYYMMDD}-{sequence}` | `V-20260729-0001` |
| 분석 | `AN-{YYYYMMDD}-{sequence}` | `AN-20260729-0001` |
| 파일 | `F-{UUID}` | `F-550e8400-...` |

- 외부 API에는 가능한 한 위 공개 ID를 사용한다.
- MongoDB 내부 관계에는 `_id: ObjectId`를 사용할 수 있다.
- 환자 조회와 외부 연동의 안정성을 위해 `patient_id`는 변경하지 않는다.

### 4.2 시간

- 모든 시각은 MongoDB UTC `Date`로 저장한다.
- API는 ISO 8601 형식을 사용한다.
- 예약 날짜 조회 기준은 `Asia/Seoul`이다.
- 생년월일은 시간대가 없는 `YYYY-MM-DD` 문자열로 저장한다.
- `created_at`, `updated_at`은 서버가 생성하며 클라이언트 값을 신뢰하지 않는다.

### 4.3 삭제

- 환자, 방문, 분석, 메모, 채팅, 확정된 의료 파일은 물리 삭제하지 않는다.
- 비활성화가 필요하면 `is_active`, `is_archived`, `revoked_at`, `superseded_at`을 사용한다.
- 업로드가 확정되기 전 실패한 임시 파일은 의료 기록이 아니므로 별도 정리 작업의 대상이 될 수 있다.

---

## 5. 컬렉션 설계

### 5.1 `counters`

사람이 읽을 수 있는 ID를 동시성 충돌 없이 발급한다.

```jsonc
{
  "_id": "patient_id",
  "seq": 40,
  "updated_at": "ISODate"
}
```

일자별 번호가 필요한 리소스는 다음과 같은 키를 사용할 수 있다.

```text
appointment:20260729
visit:20260729
analysis:20260729
```

발급은 `find_one_and_update(..., $inc, upsert=True)`와 `ReturnDocument.AFTER`를 사용한다.

### 5.2 `doctors`

```jsonc
{
  "_id": "ObjectId",
  "employee_id": "chest01",
  "password_hash": "$argon2id$...",
  "name": "김체스트 교수",
  "hospital_id": "champion",
  "hospital": "챔피언 병원",
  "department": "정형외과",
  "title": "전문의",
  "initial": "김",
  "roles": ["doctor"],
  "is_active": true,
  "created_at": "ISODate",
  "updated_at": "ISODate",
  "last_login_at": "ISODate | null"
}
```

규칙:

- 회원가입에서는 `roles`를 받지 않고 서버가 `["doctor"]`를 넣는다.
- 현재 병원 값은 서버 설정에서 넣는다.
- `initial`이 없으면 이름의 첫 글자를 사용한다.
- 비밀번호 평문은 로그와 DB 어디에도 남기지 않는다.

인덱스:

```text
employee_id unique
(hospital_id, is_active)
```

### 5.3 `auth_sessions`

Refresh token 회전과 로그아웃을 지원한다.

```jsonc
{
  "_id": "ObjectId",
  "session_id": "UUID",
  "doctor_id": "ObjectId",
  "refresh_token_hash": "sha256-or-password-hash",
  "created_at": "ISODate",
  "expires_at": "ISODate",
  "last_used_at": "ISODate",
  "revoked_at": "ISODate | null",
  "created_ip": "string | null",
  "user_agent": "string | null"
}
```

인덱스:

```text
session_id unique
(doctor_id, revoked_at)
expires_at TTL
```

TTL 인덱스는 만료 세션만 정리한다. 사용자가 명시적으로 로그아웃한 기록이 감사상 필요하면 `auth_sessions` 삭제 대신 `revoked_at`을 남기고 별도 보존 정책을 적용한다.

### 5.4 `patients`

환자의 안정적인 기본정보만 저장한다. 예약과 분석 상태는 포함하지 않는다.

```jsonc
{
  "_id": "ObjectId",
  "patient_id": "PT-1040",
  "hospital_id": "champion",
  "name": "홍길동",
  "search_name": "홍길동",
  "gender": "M",
  "birth_date": "1972-04-10",
  "assigned_doctor_id": "ObjectId",
  "created_by_doctor_id": "ObjectId",
  "care_status": "관찰중",
  "is_archived": false,
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

`care_status` 허용값:

```text
관찰중 | 치료중 | 추적관찰 | 퇴원
```

`search_name`은 공백과 대소문자 표기를 정규화한 검색용 필드다. 이름 검색은 인덱스를 사용할 수 있도록 정규화된 이름의 앞자리 일치로 제한한다. 환자번호 검색은 입력에서 영숫자 외 문자를 제거한 뒤 `patient_id`와 비교하여 `PT-1006`, `pt1006`, `1006`을 같은 번호로 처리한다.

인덱스:

```text
patient_id unique
(hospital_id, search_name)
(hospital_id, assigned_doctor_id)
(hospital_id, is_archived)
```

나이는 저장하지 않고 `birth_date`로 계산한다.

### 5.5 `appointments`

```jsonc
{
  "_id": "ObjectId",
  "appointment_id": "A-20260729-0001",
  "hospital_id": "champion",
  "patient_id": "PT-1040",
  "doctor_id": "ObjectId",
  "scheduled_at": "ISODate",
  "scheduled_date": "2026-07-29",
  "scheduled_time": "15:40",
  "exam_type": "DICOM",
  "status": "scheduled",
  "slot_claimed": true,
  "created_at": "ISODate",
  "updated_at": "ISODate"
}
```

`status`:

```text
scheduled | checked_in | in_progress | completed | cancelled
```

`scheduled_date`, `scheduled_time`은 서울 기준 날짜별 목록과 화면 표시를 단순화하기 위한 검색용 중복 필드다. 기준값은 `scheduled_at`이고 서버가 세 값을 함께 생성한다.

예약 규칙:

- 서울 기준 오늘보다 이전 날짜는 `422 Unprocessable Entity`
- 진료 시간은 `08:00`~`17:00` 양끝 포함
- 분은 10분 단위여야 한다.
- 오늘 예약은 서버가 요청을 검증하는 시점보다 미래인 슬롯만 허용한다.
- 같은 `hospital_id`, `doctor_id`, `scheduled_at`의 활성 예약은 한 건만 허용한다.
- 슬롯 충돌은 `409 Conflict`로 응답한다.
- 취소 시 `status=cancelled`, `slot_claimed=false`로 바꾸어 슬롯을 다시 사용할 수 있게 한다.

인덱스:

```text
appointment_id unique
(hospital_id, scheduled_date, scheduled_time)
(doctor_id, scheduled_at)
(patient_id, scheduled_at desc)
unique (hospital_id, doctor_id, scheduled_at)
  partialFilterExpression={"slot_claimed": true}
```

의사 필드의 역할:

- `patients.assigned_doctor_id`: 환자의 장기 담당의. 재방문 접수로 자동 변경하지 않는다.
- `appointments.doctor_id`: 이번 예약을 진료할 의사. 신규·재방문 모두 접수한 로그인 의사다.
- `visits.doctor_id`: 이번 방문의 진료 의사로 `appointments.doctor_id`와 같다.
- `analyses.requested_by_doctor_id`: 분석을 요청한 로그인 의사로 의사별 큐의 기준이다.

따라서 B 의사가 A 의사의 기존 환자를 재방문 접수하면 환자의 담당의는 A로 유지되고, 새 예약·방문·분석 큐는 B에게 연결된다.

### 5.6 `visits`

환자 등록 시 첫 방문을 즉시 생성한다.

```jsonc
{
  "_id": "ObjectId",
  "visit_id": "V-20260729-0001",
  "hospital_id": "champion",
  "patient_id": "PT-1040",
  "appointment_id": "A-20260729-0001",
  "doctor_id": "ObjectId",
  "visit_date": "2026-07-29",
  "exam_type": "DICOM",
  "modalities": [],
  "status": "open",
  "created_at": "ISODate",
  "updated_at": "ISODate",
  "completed_at": "ISODate | null"
}
```

`status`:

```text
open | completed | cancelled
```

방문은 진료 단위이므로 분석 실행 상태를 갖지 않는다.

- `visits.status`: 진료가 열려 있는지, 완료되었는지, 예약 취소로 무효화되었는지
- `appointments.status`: 일정 및 좌측 패널의 진료 완료 여부
- `analyses.status`: 모델 실행 상태
- `care_status`: 환자의 임상 관리 상태

좌측 패널의 “진료 완료” 하단 정렬 기준은 `appointments.status=completed`다. 이때 연결된 방문도 `visits.status=completed`로 함께 바꾼다. 예약 취소 시 연결 방문은 `visits.status=cancelled`로 남고 기본 일정에서 숨긴다. 파일이 없어 분석이 없는 열린 방문은 API 집계 DTO에서 `analysis_status=waiting_for_files`로 표현한다.

인덱스:

```text
visit_id unique
(patient_id, visit_date desc, created_at desc)
appointment_id
```

### 5.7 `analyses`

분석 요청, 대기열 상태, 실행 결과를 하나의 영속 문서로 관리한다. 별도 `analysis_jobs` 컬렉션을 만들지 않아 상태 중복을 피한다.

```jsonc
{
  "_id": "ObjectId",
  "analysis_id": "AN-20260729-0001",
  "hospital_id": "champion",
  "patient_id": "PT-1040",
  "visit_id": "V-20260729-0001",
  "appointment_id": "A-20260729-0001",

  "requested_by_doctor_id": "ObjectId",
  "visit_doctor_id": "ObjectId",
  "scheduled_at": "ISODate",
  "queued_at": "ISODate",
  "started_at": "ISODate | null",
  "completed_at": "ISODate | null",
  "cancel_requested_at": "ISODate | null",

  "status": "queued",
  "attempt": 0,
  "worker_id": "string | null",
  "lease_expires_at": "ISODate | null",

  "mode": "auto",
  "query": "업로드한 영상을 분석해줘.",
  "department": null,
  "project": null,
  "model_name": null,

  "risk_tier": null,
  "risk_status": "pending",
  "confidence": null,
  "finding": null,
  "interpretation": null,
  "recommendation": null,
  "predictions": null,

  "input_file_ids": ["F-..."],
  "result_file_ids": {
    "base": [],
    "heat": [],
    "box": []
  },

  "error": null,
  "created_at": "ISODate",
  "updated_at": "ISODate",
  "superseded_at": null
}
```

`status`:

```text
queued | analyzing | done | failed | cancelled | superseded
```

`risk_status`:

```text
pending | assessed | unavailable
```

상태별 위험도 규칙:

| 분석 상태 | `risk_status` |
|---|---|
| `queued`, `analyzing` | `pending` |
| `done` + 정책 판정 성공 | `assessed` |
| `done` + 정책 없음/판정 불가 | `unavailable` |
| `failed`, `cancelled` | `unavailable` |
| `superseded` | 기존 판정값 보존, 최신 결과 선정에서는 제외 |

인덱스:

```text
analysis_id unique
(requested_by_doctor_id, status, scheduled_at, queued_at)
(patient_id, created_at desc)
(visit_id, created_at desc)
(status, lease_expires_at)
```

분석 큐는 `requested_by_doctor_id` 기준이다. 담당 환자를 다른 의사가 재분석한 경우 재분석을 요청한 의사의 큐에 들어간다.

### 5.8 `doctor_analysis_locks`

서버 프로세스가 여러 개이거나 재시작되어도 의사별 동시 실행 1건을 보장한다.

```jsonc
{
  "_id": "doctor ObjectId",
  "owner_worker_id": "worker-01",
  "analysis_id": "AN-...",
  "lease_expires_at": "ISODate",
  "updated_at": "ISODate"
}
```

`_id`가 의사 ID이므로 의사별 잠금 문서는 하나만 존재한다.

### 5.9 `medical_files`

GridFS 바이너리와 의료 도메인 메타데이터를 연결한다.

```jsonc
{
  "_id": "ObjectId",
  "file_id": "F-UUID",
  "gridfs_id": "ObjectId",
  "hospital_id": "champion",
  "patient_id": "PT-1040 | null",
  "visit_id": "V-... | null",
  "analysis_id": "AN-... | null",
  "kind": "input",
  "role": "source",
  "slice_index": null,
  "original_filename": "scan.dcm",
  "content_type": "application/dicom",
  "extension": "dcm",
  "size_bytes": 123456,
  "sha256": "hex",
  "status": "active",
  "created_by_doctor_id": "ObjectId",
  "created_at": "ISODate"
}
```

`kind`:

```text
input | derived_image
```

`role` 예:

```text
source | base | heat | box
```

`status`:

```text
staging | active | orphaned
```

같은 슬라이스의 `base`, `heat`, `box`는 동일한 `slice_index`를 사용한다.

인덱스:

```text
file_id unique
gridfs_id unique
(analysis_id, role, slice_index)
(visit_id, created_at)
sha256
```

### 5.10 `notes`

```jsonc
{
  "_id": "ObjectId",
  "hospital_id": "champion",
  "patient_id": "PT-1040",
  "visit_id": "V-... | null",
  "author_id": "ObjectId",
  "author_name": "김체스트 교수",
  "source": "registration",
  "text": "폐결절 의심 소견을 확인해줘.",
  "created_at": "ISODate"
}
```

`source`:

```text
registration | clinical
```

`author_name`은 과거 표시를 안정적으로 유지하기 위한 스냅샷이다. 권한 판정은 `author_id`를 사용한다.

인덱스:

```text
(patient_id, created_at)
(visit_id, created_at)
```

### 5.11 `chat_messages`

```jsonc
{
  "_id": "ObjectId",
  "message_id": "UUID",
  "hospital_id": "champion",
  "patient_id": "PT-1040",
  "visit_id": "V-...",
  "doctor_id": "ObjectId",
  "role": "user",
  "content": "이전 검사와 비교해줘.",
  "context_analysis_ids": ["AN-..."],
  "created_at": "ISODate"
}
```

`role`:

```text
user | assistant
```

인덱스:

```text
message_id unique
(patient_id, created_at)
(visit_id, created_at)
```

### 5.12 `access_logs`

```jsonc
{
  "_id": "ObjectId",
  "doctor_id": "ObjectId | null",
  "employee_id": "chest01 | null",
  "hospital_id": "champion | null",
  "action": "read",
  "resource": "patient",
  "resource_id": "PT-1040",
  "method": "GET",
  "path": "/patients/PT-1040",
  "status_code": 200,
  "ip": "192.0.2.10",
  "at": "ISODate"
}
```

환자 본문, 비밀번호, 토큰, 채팅 원문은 감사 로그에 복사하지 않는다.

서명 URL의 `sig`, `exp`도 인증정보로 취급한다.

- `access_logs.path`에는 query string을 제거한 경로만 저장한다.
- 애플리케이션 logger에는 전체 서명 URL을 출력하지 않는다.
- uvicorn access log와 앞단 reverse proxy도 query string을 제거하거나 `sig`, `exp`를 마스킹한다.
- 오류 응답과 예외 trace에도 원본 URL을 포함하지 않는다.

인덱스:

```text
(doctor_id, at desc)
(resource_id, at desc)
(hospital_id, at desc)
at TTL (AUDIT_RETENTION_DAYS가 0보다 클 때)
```

감사 로그 기본 보존 기간은 365일로 두고 환경변수로 조정한다. 운영·법무 정책이 확정되면 그 값으로 변경한다. `0`은 무기한 보존을 의미한다.

---

## 6. 인증 설계

### 6.1 토큰

- Access token: 15~30분
- Refresh token: 장기 토큰, 서버 세션과 연결
- 비밀번호: Argon2id 권장, bcrypt 허용
- Refresh token 원문은 DB에 저장하지 않는다.
- 로그아웃 시 `auth_sessions.revoked_at`을 기록한다.
- 비활성 의사는 기존 토큰이 남아 있어도 보호 API를 사용할 수 없다.

초기 클라이언트 호환을 위해 access/refresh token을 JSON으로 반환할 수 있다. 운영 전에는 refresh token을 `HttpOnly`, `Secure`, `SameSite` 쿠키로 옮기는 것을 권장한다.

### 6.2 API

#### `POST /auth/signup`

```jsonc
{
  "employee_id": "chest03",
  "password": "user supplied",
  "name": "박메이플 교수",
  "hospital": "챔피언 병원",
  "department": "영상의학과",
  "title": "전문의"
}
```

규칙:

- 현재 클라이언트 호환을 위해 `hospital`을 요청 스키마에 포함한다.
- 서버 설정의 단일 병원명과 정확히 일치하는지 검증하고, 저장할 `hospital_id`와 `hospital`은 서버 설정값을 사용한다.
- 지원하지 않는 병원명은 `422 Unprocessable Entity`다.
- 클라이언트는 병원 필드를 읽기 전용으로 바꿀 수 있지만 서버 검증은 유지한다.
- 역할은 항상 `["doctor"]`다.
- 가입 즉시 `is_active=true`다.
- `employee_id` 중복은 `409 Conflict`다.

응답은 로그인과 같은 세션을 바로 발급할 수 있다.

#### `POST /auth/login`

```jsonc
{
  "employee_id": "chest01",
  "password": "..."
}
```

#### `POST /auth/refresh`

유효한 refresh token을 검증하고 rotation한 새 토큰 쌍을 발급한다.

#### `POST /auth/logout`

현재 refresh 세션을 폐기한다.

#### `GET /auth/me`

현재 의사의 프로필과 역할을 반환한다.

### 6.3 보호 규칙

- `/health`, `/auth/signup`, `/auth/login`, `/auth/refresh`를 제외한 모든 API는 Bearer 인증 필수다.
- `GET /files/{file_id}?exp=&sig=`만 유효한 단기 서명을 Bearer 인증 대신 허용한다.
- 같은 `hospital_id`의 환자만 조회할 수 있다.
- `doctor_id`, `author_id`, `requested_by_doctor_id`는 요청 본문이 아니라 토큰에서 결정한다.
- 일반 회원가입 요청이 `admin` 역할을 포함하면 거부하거나 무시한다.
- `/projects` 조회, `/inference`, `/pipeline`은 활성 `doctor` 또는 `admin` 역할을 요구한다.
- `/admin`의 조회·생성·수정·삭제는 `admin` 역할을 요구한다.
- 기존 라우터에 보호 dependency를 붙이기 전에는 실제 환자 데이터를 연결하지 않는다.

### 6.4 클라이언트 세션 수명

Refresh 흐름은 Phase 2 인증 구현의 필수 범위이며 후속 TODO가 아니다.

- Access token 기본 수명: 30분
- Refresh session 최대 수명: 7일
- 클라이언트는 access token 만료 시 `/auth/refresh`를 한 번 호출하고 원 요청을 한 번만 재시도한다.
- 공용 워크스테이션 정책에 따라 access/refresh token은 `sessionStorage`에 둔다.
- 창을 닫으면 두 토큰이 사라지므로 실제 세션 수명은 창 수명보다 길어지지 않는다.
- 창이 열려 있는 동안에는 refresh로 진료 중 강제 로그아웃을 방지한다.
- refresh 실패 또는 세션 폐기 시 저장 토큰을 지우고 로그인 화면으로 이동한다.

---

## 7. 신규 환자 및 재방문 접수 유스케이스

### 7.1 검색 우선 흐름

접수 모달의 기본 탭은 기존 환자 검색이다. 동명이인을 구분하고 중복 환자 생성을 줄이기 위해 신규 등록보다 검색을 먼저 수행한다.

```text
기존 환자
  GET /patients/search?q=
  → 환자 선택
  → POST /patients/{patient_id}/appointments

신규 환자
  POST /patients
  → 서버 patient_id 발급
```

검색 규칙:

- 현재 로그인 의사와 같은 병원으로 제한한다.
- 정규화된 환자명의 앞자리 일치를 지원한다. 예: `홍`, `홍길` → `홍길동`.
- 중간 문자열 포함 검색은 일반 MongoDB 인덱스를 사용할 수 없어 1차 범위에서 지원하지 않는다.
- 이름 prefix 정규식에 넣기 전 사용자 입력을 escape하여 정규식 삽입과 비정상 쿼리를 막는다.
- 환자번호는 `PT-1006`, `pt1006`, `1006` 표기를 같은 번호로 정규화한다.
- 최대 20건을 반환한다.
- 동명이인 구분을 위해 `birth_date`, `last_visit_date`, `visit_count`를 반환한다.

```http
GET /patients/search?q=조유윤
```

```jsonc
{
  "results": [
    {
      "patient_id": "PT-1006",
      "name": "조유윤",
      "gender": "F",
      "birth_date": "1984-07-07",
      "last_visit_date": "2026-07-01",
      "visit_count": 2
    }
  ]
}
```

### 7.2 신규 환자 요청

```http
POST /patients
Authorization: Bearer <access-token>
Content-Type: multipart/form-data
```

필드:

```text
name
gender
birth_date
appt_date
appt_time
note                선택
files               0..N
```

보내지 않는 필드:

```text
patient_id
doctor_id
hospital_id
autoAnalyze
```

### 7.3 기존 환자 재방문 요청

```http
POST /patients/{patient_id}/appointments
Authorization: Bearer <access-token>
Content-Type: multipart/form-data
```

필드:

```text
appt_date
appt_time
note                선택
files               0..N
```

기존 환자의 이름, 성별, 생년월일은 요청에서 받지 않는다. 서버에 저장된 환자 정보를 그대로 사용하며 새 예약과 방문만 생성한다.

응답은 신규 환자 등록과 같은 `appointment_id`, `visit_id`, 선택적 `analysis_id` 형태를 사용한다.

### 7.4 공통 예약 검증

`POST /patients`와 `POST /patients/{patient_id}/appointments`는 동일한 서버 검증 함수를 사용한다.

| 규칙 | 값 | 오류 |
|---|---|---|
| 날짜 | 서울 기준 오늘 이후 | `422` |
| 과거 예약 | 등록 허용(과거 진료 기록 입력 지원) | - |
| 진료 시간 | `08:00`~`17:00`, 양끝 포함 | `422` |
| 시간 단위 | `minute % 10 == 0`, 초는 0 | `422` |
| 의사별 슬롯 | 동일 의사·동일 `scheduled_at` 한 건 | `409` |

클라이언트 모달을 오래 열어둔 사이 슬롯이 지나거나 다른 사용자가 먼저 예약할 수 있으므로 서버 검증 결과가 최종이다.

### 7.5 `exam_type` 파생

`exam_type`은 클라이언트 요청값을 받지 않고 서버가 실제 첨부 파일 확장자에서 계산한다.

```text
.dcm, .dicom        → DICOM
.nii, .nii.gz       → NIfTI
.csv                → CSV
.png, .jpg, .jpeg   → 이미지
복수 형식           → "DICOM · CSV"처럼 고정 순서로 결합
파일 없음           → ""
```

복수 형식의 서버 결합 순서는 항상 `DICOM → NIfTI → CSV → 이미지`다. 업로드 순서와 무관하다. 허용 확장자 밖의 파일은 `exam_type` fallback으로 넘기지 않고 업로드 검증 단계에서 `415 Unsupported Media Type`으로 거부한다.

파일 없는 방문에 나중에 분석 파일이 추가되면 연결된 예약과 방문의 `exam_type`을 같은 규칙으로 갱신한다.

### 7.6 처리 순서

1. 인증 의사와 병원을 확인한다.
2. 예약 날짜·시간, 슬롯 충돌, 파일 확장자·크기를 검증한다.
3. 첨부 파일을 GridFS와 `medical_files(status=staging)`에 저장한다.
4. MongoDB 트랜잭션에서 다음을 처리한다.
   - 신규 흐름이면 환자 ID 발급 및 `patients` 생성
   - 재방문 흐름이면 기존 환자를 확인하고 기본정보는 변경하지 않음
   - `appointments` 생성
   - `visits` 생성
   - 메모가 있으면 `notes` 생성
   - 파일이 있으면 `analyses(status=queued)` 생성
   - 파일이 없으면 분석 문서를 만들지 않고 방문은 `open`으로 유지
   - staging 파일을 환자/방문/분석에 연결하고 `active`로 전환
5. 트랜잭션 완료 후 큐 worker가 작업을 발견한다.
6. 생성 결과를 즉시 반환한다.

트랜잭션을 설계 전제로 확정한다. 따라서 Phase 1에서 현재 standalone MongoDB를 single-node replica set으로 전환하고 연결 URI에 `replicaSet`을 설정한다. 이 전환이 완료되기 전에는 Phase 3 환자 접수를 구현하지 않는다.

### 7.7 응답

파일이 있는 경우:

```jsonc
{
  "patient_id": "PT-1041",
  "appointment_id": "A-20260729-0041",
  "visit_id": "V-20260729-0041",
  "analysis": {
    "analysis_id": "AN-20260729-0041",
    "status": "queued",
    "queue_position": 3
  }
}
```

파일이 없는 경우:

```jsonc
{
  "patient_id": "PT-1041",
  "appointment_id": "A-20260729-0041",
  "visit_id": "V-20260729-0041",
  "analysis": null,
  "visit_status": "open",
  "analysis_status": "waiting_for_files"
}
```

### 7.8 후속 파일 등록과 재분석

파일 없이 등록된 방문 또는 기존 방문의 재분석은 다음 API를 사용한다.

```http
POST /visits/{visit_id}/analyses
Content-Type: multipart/form-data
```

이 API도 파일과 질의를 받고 새 `analysis_id`를 발급해 대기열에 넣는다. 기존 성공 결과는 덮어쓰지 않는다.

---

## 8. 분석 대기열 설계

### 8.1 정렬 규칙

각 의사 큐에서 아직 시작하지 않은 작업을 다음 순서로 정렬한다.

```text
scheduled_at ASC
queued_at ASC
```

예약 시각이 빠른 작업이 먼저이므로 다음 요구를 함께 만족한다.

- 이미 예약 시각이 지난 환자가 가장 먼저 처리된다.
- 오늘 예정 환자는 예약 시간순으로 처리된다.
- 오늘 작업 뒤에 미래 예약이 처리된다.
- 새로 등록된 지연 환자는 아직 시작하지 않은 미래 작업보다 앞선다.

현재 실행 중인 작업은 선점하지 않는다.

### 8.2 의사별 직렬화

worker는 다음 과정을 반복한다.

1. `queued` 작업이 있는 의사 후보를 찾는다.
2. `doctor_analysis_locks`에서 해당 의사의 만료된 잠금을 원자적으로 획득한다.
3. 해당 의사의 가장 이른 `queued` 분석 한 건을 `analyzing`으로 바꾼다.
4. 실행 중 lease를 주기적으로 연장한다.
5. 성공 시 `done`, 실패 시 `failed`로 저장한다.
6. 의사 잠금을 해제한다.
7. 같은 의사의 다음 작업 또는 다른 의사의 작업을 처리한다.

다른 의사의 잠금은 독립적이므로 worker 여유가 있으면 병렬 실행된다.

잠금 획득 구현 주의:

- `_id=doctor_id`와 `upsert=True`를 함께 사용할 때 다른 worker가 유효한 lease를 보유하면 필터에 일치하지 않은 upsert가 같은 `_id`를 삽입하려다 `DuplicateKeyError(E11000)`가 발생할 수 있다.
- 이 오류는 worker 장애가 아니라 정상적인 “잠금 획득 실패”로 처리하고 다음 의사 후보로 넘어간다.
- 잠금 획득 후 `owner_worker_id`를 다시 확인한 뒤 분석을 claim한다.

### 8.3 예약 변경·취소 반영

- 예약 시각 변경은 아직 시작하지 않은 `queued` 분석의 `scheduled_at`에만 반영한다.
- 예약 취소는 `queued → cancelled`로 전이한다.
- `analyzing` 작업은 선점 중단하지 않고 `cancel_requested_at`을 기록한다.
- worker는 claim 직전에 예약과 분석 상태를 다시 확인하여 취소된 작업을 실행하지 않는다.
- `cancelled` 작업은 큐 위치 계산과 최신 활성 결과 선정에서 제외한다.

### 8.4 프로세스 장애 복구

- `lease_expires_at`이 지난 `analyzing` 작업은 복구 대상이다.
- worker가 죽으면 새 worker가 만료된 잠금을 인수한다.
- 재시도 시 `attempt`를 증가시킨다.
- 최대 자동 재시도 횟수는 환경변수로 둔다.
- 최대 횟수를 초과하면 `failed`로 확정하고 다음 작업으로 진행한다.
- 모델 실행이 실제로 계속되고 있는지 확인할 수 없는 경우 중복 실행 가능성을 로그로 남긴다.

### 8.5 큐 위치

`queue_position`은 저장값이 아니라 조회 시 계산한다.

같은 `requested_by_doctor_id`의 `queued` 작업 중 현재 작업보다 다음 정렬 조건이 앞선 작업 수에 1을 더한다.

```text
scheduled_at
queued_at
```

실행 중 작업은 별도로 `analyzing`으로 표시한다.

### 8.6 상태 조회

초기 클라이언트는 polling을 사용한다.

```http
GET /analyses/{analysis_id}
GET /patients?date=YYYY-MM-DD
```

2~3초 간격으로 조회하고 `done` 또는 `failed`에서 중단한다. SSE/WebSocket은 후속 최적화로 남긴다.

---

## 9. 추론 연동

### 9.1 기존 엔드포인트

기존 `POST /inference/`는 다른 클라이언트 호환을 위해 유지한다. 신규 임상 흐름은 worker 내부 서비스가 동일한 추론 로직을 호출하도록 리팩터링한다.

컨트롤러에 환자 저장 로직을 반복해서 넣지 않고 다음 서비스 경계를 둔다.

```text
AnalysisQueueService
  ├── enqueue()
  ├── claim_next()
  ├── mark_done()
  └── mark_failed()

ClinicalInferenceService
  ├── plan()
  ├── execute()
  ├── interpret()
  ├── normalize_result()
  └── store_artifacts()
```

### 9.2 구조화 결과

라우팅 서버가 `analyses`에 저장하고 클라이언트에 반환하는 표준 결과:

```jsonc
{
  "model_name": "RSNA_Pneumonia_YOLO26x",
  "risk_tier": "High",
  "risk_status": "assessed",
  "confidence": 0.87,
  "finding": "...",
  "interpretation": "...",
  "recommendation": "...",
  "predictions": {},
  "images": {
    "base": ["/files/F-..."],
    "heat": ["/files/F-..."],
    "box": ["/files/F-..."]
  }
}
```

### 9.3 위험도 판정

최종 `risk_tier` 판정 주체는 라우팅 서버의 `RiskPolicyService`로 확정한다.

- Agent는 소견과 해석을 생성하지만 최종 위험도 권한을 갖지 않는다.
- 임상 UI에서 사용하는 각 모델은 모델 레지스트리에 `risk_policy`를 가져야 한다.
- 정책은 모델의 정규화된 label, severity, confidence 등 실제 출력에 모델별로 적용한다.
- 관련 모델의 초기 정책은 seed 또는 모델 설정에 함께 등록한다.
- 정책이 없거나 출력이 정책에 맞지 않으면 confidence만으로 위험도를 임의 생성하지 않고 `risk_tier=null`, `risk_status=unavailable`을 반환한다.
- 클라이언트는 이 경우 “미분류”로 표시하고 위험도 정렬에서 판정 환자 뒤에 둔다.
- `Low`, `High`, `Critical` 이외의 값은 저장하지 않는다.

클라이언트의 기존 confidence 임계값은 레거시 동기 응답 표시용 폴백일 뿐, DB에 저장되는 위험도나 예약 목록 정렬의 근거로 사용하지 않는다.

### 9.4 Agent history 전달

`AgentService.plan()`과 `/inference/agent/plan` 프록시에 `history`를 추가한다.

```jsonc
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```

Agent 서버도 실제 프롬프트에 `history`를 반영해야 한다.

### 9.5 `inference_results` 전환

환자 연결 정보가 없는 기존 `inference_results`를 억지로 `analyses`로 이관하지 않는다.

전환 정책:

1. 신규 환자·방문 분석은 처음부터 `analyses`에만 기록한다.
2. `patient_id`, `visit_id`가 없는 기존 레거시 `/inference/` 호출은 당분간 `inference_results`에 기록한다.
3. 기존 결과 중 환자·방문 매핑을 검증할 수 있는 데이터만 별도 마이그레이션 스크립트로 옮긴다.
4. 내부 클라이언트가 모두 임상 API로 전환되면 레거시 쓰기를 중단한다.
5. 이중쓰기는 두 컬렉션 불일치 위험이 있어 사용하지 않는다.
6. `inference_results`는 읽기 전용 보관 기간을 거친 뒤 archive 대상으로 표시하되 물리 삭제는 별도 정책 승인 전 수행하지 않는다.

---

## 10. 채팅 문맥 설계

### 10.1 API

```http
GET  /visits/{visit_id}/chat
POST /visits/{visit_id}/chat
```

POST 요청:

```jsonc
{
  "content": "지난 검사와 비교해서 악화 여부를 알려줘."
}
```

서버 처리:

1. 방문과 환자의 병원 접근 권한을 확인한다.
2. user 메시지를 저장한다.
3. 환자의 전체 방문과 성공한 분석 결과를 조회한다.
4. 환자의 전체 채팅을 조회한다.
5. 토큰 예산에 맞는 Agent context를 생성한다.
6. Agent에 질문한다.
7. assistant 메시지를 저장한다.
8. 두 메시지와 사용한 분석 ID를 반환한다.

### 10.2 Context Builder

Agent 입력은 다음 순서로 구성한다.

1. 환자 기본정보
2. 방문별 날짜, 검사 종류, 위험도
3. 방문별 `finding`, `interpretation`, `recommendation`
4. 과거 방문 채팅 요약
5. 최근 채팅 원문
6. 현재 질문

정책:

- DB 원문은 전부 보존한다.
- 최근 방문과 현재 질문 관련 방문을 우선한다.
- 오래된 채팅은 방문별 요약으로 축약한다.
- 이미지 자체를 매번 모두 보내지 않고 관련 분석의 결과 메타데이터와 필요한 대표 이미지만 선택한다.
- 실제 토큰 한도와 요약 방식은 Agent 서버 성능 확인 후 환경변수로 조정한다.

### 10.3 분석과 채팅의 관계

- 텍스트만 있는 후속 질문은 새 분석 작업을 만들지 않는다.
- 새 의료 파일이 첨부된 질문은 먼저 새 분석 작업을 생성하고 큐에 넣는다.
- 분석 완료 전 질문이면 완료된 기존 분석만 사용하고, 대기 중인 분석 상태를 응답 문맥에 명시한다.

---

## 11. API 목록

### 11.1 인증

| Method | Endpoint | 설명 |
|---|---|---|
| POST | `/auth/signup` | 의사 직접 가입 및 즉시 활성화 |
| POST | `/auth/login` | 로그인 |
| POST | `/auth/refresh` | 토큰 재발급 및 rotation |
| POST | `/auth/logout` | 현재 세션 폐기 |
| GET | `/auth/me` | 현재 의사 프로필 |

### 11.2 환자와 예약

| Method | Endpoint | 설명 |
|---|---|---|
| POST | `/patients` | 환자·예약·첫 방문 생성, 파일이 있으면 분석 enqueue |
| GET | `/patients/search?q=` | 이름·환자번호 검색, 재방문 접수 |
| GET | `/patients?date=YYYY-MM-DD` | 날짜별 예약 환자 목록 |
| GET | `/patients/{patient_id}` | 환자 상세 |
| PATCH | `/patients/{patient_id}` | 환자 관리 상태 등 수정 |
| POST | `/patients/{patient_id}/appointments` | 기존 환자 재방문 예약·방문 생성 |
| GET | `/patients/{patient_id}/appointments` | 예약 이력 |
| PATCH | `/appointments/{appointment_id}` | 예약 취소·시간 변경·진료 완료 |

`PATCH /appointments/{appointment_id}` 허용 변경:

- `status=cancelled`: 오등록 예약을 취소하고 슬롯을 해제한다.
- `status=completed`: 좌측 패널의 진료 완료 섹션으로 이동하고 연결 방문을 완료한다.
- `scheduled_date`, `scheduled_time`: 아직 시작하지 않은 예약을 변경한다. 공통 예약 검증과 슬롯 충돌 검사를 다시 수행한다.

취소된 예약은 기본 날짜별 목록에서 제외하고 예약 이력에는 남긴다.

예약 시각 변경:

- 예약과 방문 관계는 유지한다.
- 연결된 `analyses.status=queued` 작업의 `scheduled_at`을 새 예약 시각으로 같은 트랜잭션에서 갱신한다.
- `analyzing`, `done`, `failed`, `cancelled`, `superseded` 분석의 시각은 실행 당시 기록으로 유지한다.
- 슬롯 unique 충돌 검사와 queued 분석 갱신 중 하나라도 실패하면 전체 변경을 롤백한다.

예약 취소:

- `appointments.status=cancelled`, `slot_claimed=false`
- 연결된 `visits.status=cancelled`
- 연결된 `queued` 분석은 `status=cancelled`, `risk_status=unavailable`, `completed_at=now`
- 연결된 `analyzing` 분석은 중단하지 않고 `cancel_requested_at=now`만 기록한다. 실행 후 `done` 또는 `failed`로 보존하되 기본 일정과 최신 활성 결과에서는 제외한다.
- 이미 끝난 분석은 변경하지 않고 취소된 방문 이력 아래에 보존한다.
- 취소 API는 변경된 예약·방문 상태와 취소된 queued 분석 ID, 계속 실행 중인 분석 ID를 반환한다.

슬롯 충돌 응답:

```jsonc
{
  "detail": "슬롯 충돌",
  "conflict": {
    "doctor": {
      "employee_id": "rad02",
      "name": "이영상 전공의"
    },
    "scheduled_at": "2026-07-30T15:40:00+09:00"
  }
}
```

환자 목록 응답은 화면용 집계 DTO를 사용한다.

```jsonc
{
  "date": "2026-07-29",
  "patients": [
    {
      "patient_id": "PT-1040",
      "name": "홍길동",
      "gender": "M",
      "birth_date": "1972-04-10",
      "appt_time": "15:40",
      "exam_type": "DICOM",
      "care_status": "치료중",
      "assigned_doctor": {
        "employee_id": "chest01",
        "name": "김체스트 교수"
      },
      "appointment_doctor": {
        "employee_id": "rad02",
        "name": "이영상 전공의"
      },
      "appointment_id": "A-20260729-0001",
      "latest_visit_id": "V-20260729-0001",
      "latest_analysis_id": "AN-20260729-0001",
      "appointment_status": "scheduled",
      "analysis_status": "queued",
      "queue_position": 2,
      "risk_tier": null,
      "confidence": null,
      "visit_count": 1
    }
  ]
}
```

### 11.3 방문과 분석

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/patients/{patient_id}/visits` | 방문 이력 |
| GET | `/patients/{patient_id}/visits/{visit_id}` | 방문 상세 |
| POST | `/visits/{visit_id}/analyses` | 파일 업로드 및 새 분석 enqueue |
| GET | `/visits/{visit_id}/analyses` | 방문의 분석 실행 이력 |
| GET | `/analyses/{analysis_id}` | 분석 상태와 결과 |

### 11.4 메모와 채팅

| Method | Endpoint | 설명 |
|---|---|---|
| GET | `/patients/{patient_id}/notes` | 환자 메모 |
| POST | `/patients/{patient_id}/notes` | 메모 작성 |
| GET | `/visits/{visit_id}/chat` | 방문 채팅 이력 |
| POST | `/visits/{visit_id}/chat` | 전체 환자 문맥 기반 후속 질문 |

### 11.5 파일

| Method | Endpoint | 설명 |
|---|---|---|
| POST | `/analyses/{analysis_id}/access-urls` | 분석 이미지 전체의 5분 서명 URL 일괄 갱신 |
| GET | `/files/{file_id}?exp=&sig=` | 서명 검증 후 GridFS 스트리밍 |

파일 응답은 적절한 `Content-Type`, `Content-Length`, `ETag`, `Cache-Control`을 제공한다.

파일 접근 방식은 5분 HMAC 서명 URL로 확정한다.

- `GET /analyses/{analysis_id}`와 방문 상세 응답은 모든 결과 이미지의 `file_id`, 서명 URL, 공통 `expires_at`을 반드시 반환한다.
- 클라이언트용 `images.base`, `images.heat`, `images.box`에는 바로 표시할 수 있는 서명 URL 배열을 넣는다.
- 원본 `file_id` 배열도 별도 필드로 반환하여 URL 갱신과 감사 연결에 사용한다.
- 서명 만료 갱신은 파일별 왕복 대신 `POST /analyses/{analysis_id}/access-urls` 한 번으로 해당 분석의 모든 URL을 다시 발급한다.
- 한 응답에서 발급한 URL은 동일한 만료 시각을 사용한다.
- 서명은 `file_id`, 만료 시각, 필요하면 세션 ID를 포함한다.
- 서명 URL 발급 시 환자·병원 접근 권한을 검사하고 감사 로그를 한 번 남긴다.
- 실제 슬라이스 GET마다 의료 감사 행을 만들지 않고 운영 access log만 남긴다.
- URL이 만료되면 클라이언트는 분석 단위 배치 API로 새 URL을 받는다.
- 응답 캐시는 `Cache-Control: private, max-age=300` 이하로 제한한다.

현재 클라이언트의 CSS `background-image`는 서명 URL을 그대로 사용할 수 있다. 다만 주석 저장의 `new Image() → drawImage() → toDataURL()`은 URL이 cross-origin이면 서명만으로 canvas 오염이 해결되지 않는다.

따라서 다음 두 조건을 함께 구현한다.

1. 파일 응답에 개발 `http://localhost:3000` 또는 프로덕션 `app://maple`과 정확히 일치하는 CORS 헤더를 제공한다.
2. `ImageViewer`가 `img.src` 설정 전에 `img.crossOrigin = "anonymous"`를 설정한다.

서명 URL query가 바뀌더라도 CORS가 적용되어야 하며 `Access-Control-Allow-Origin: *` 또는 `Origin: null` 허용으로 되돌리지 않는다.

### 11.6 Electron 프로덕션 origin

패키징 앱은 `file://`로 렌더러를 열지 않는다. [Electron 공식 보안 권고](https://www.electronjs.org/docs/latest/tutorial/security#18-avoid-usage-of-the-file-protocol-and-prefer-usage-of-custom-protocols)에 따라 프로덕션 빌드를 표준·보안 커스텀 프로토콜로 제공한다. 구현은 현재 Electron의 [`protocol.handle`](https://www.electronjs.org/docs/latest/api/protocol) API를 사용한다.

```text
개발 origin      http://localhost:3000
프로덕션 origin  app://maple
```

클라이언트 구현 조건:

- 앱 준비 전에 `app` scheme을 `standard`, `secure`, `supportFetchAPI`, `corsEnabled` 권한으로 등록한다.
- 앱 준비 후 `protocol.handle("app", ...)`로 패키지의 `build` 디렉터리 안 파일만 제공한다.
- 프로덕션 창은 `app://maple/index.html`을 연다.
- `webSecurity`를 끄지 않는다.
- `nodeIntegration=false`, `contextIsolation=true`를 유지한다.
- CSP의 `connect-src`와 `img-src`에 실제 백엔드 origin을 허용한다.

서버는 `CLIENT_ORIGINS=http://localhost:3000,app://maple`처럼 정확한 allowlist를 사용한다. 최종 CORS와 canvas 테스트는 개발 서버가 아니라 `npm run electron:build` 패키징 산출물에서 수행한다.

---

## 12. 권한과 감사

초기 권한 정책:

- 같은 병원 의사는 병원 내 환자를 조회할 수 있다.
- 담당 의사와 등록 의사는 별도로 기록한다.
- 환자 등록, 메모 작성, 채팅, 분석 요청의 작성자는 로그인 토큰으로 결정한다.
- 다른 병원 데이터 접근은 `404` 또는 `403`으로 차단한다.
- Admin 역할이 있어도 Admin 전용 API는 이번 단계에서 구현하지 않는다.

감사 대상:

- 환자 목록 및 상세 열람
- 방문과 분석 결과 열람
- 의료 파일 열람
- 환자 등록 및 상태 변경
- 메모 작성
- 채팅 요청
- 로그인 성공/실패와 로그아웃

HTTP 미들웨어는 요청 공통정보를 기록하고, 서비스 계층은 `patient_id`, `visit_id` 같은 도메인 리소스를 기록한다.

슬라이스 1장을 받을 때마다 감사 행을 만들면 한 화면 열람이 수십 건으로 증폭된다. 파일 감사는 서명 URL 발급 시 `(doctor_id, analysis_id)` 단위로 한 번 기록하고, 개별 파일 GET은 일반 HTTP access log로만 남긴다.

---

## 13. 초기 데이터

### 13.1 의사

초기 3개 계정:

```text
Doctor 1           roles=["doctor"]
Doctor 2           roles=["doctor"]
Doctor/Admin       roles=["doctor", "admin"]
```

- 실제 ID, 이름, 초기 비밀번호는 seed 실행 시 환경변수 또는 안전한 입력으로 전달한다.
- 평문 비밀번호를 저장소에 커밋하지 않는다.
- 기존 클라이언트 데모 계정인 `chest01`, `rad02`는 호환을 위해 기본 seed 후보로 사용할 수 있다.

### 13.2 환자

환자 40명 배분:

```text
Doctor 1      14명
Doctor 2      13명
Doctor/Admin  13명
```

시드 정책:

- 같은 병원 의사는 40명 전체를 조회할 수 있다.
- 환자별 담당 의사를 저장한다.
- 기존 대표 환자의 방문, 분석, 메모, 채팅 예시를 옮긴다.
- 40명 전부를 실제 `queued` 상태로 만들지 않는다.
- 일부는 성공 분석(`done`)을 넣고, 일부는 분석 문서 없이 두어 목록에서 `waiting_for_files`로 집계한다.
- 실제 새 환자를 등록했을 때만 worker가 처리할 실 큐 작업을 생성한다.

### 13.3 데모 날짜

- 운영에서는 실제 서울 날짜를 사용한다.
- 고정된 데모 날짜가 필요하면 `DEMO_DATE` 환경변수를 seed 도구에만 사용한다.
- 클라이언트의 하드코딩된 `TODAY = '2026-07-21'`은 제거하고 실제 날짜로 계산한다.

---

## 14. 클라이언트 변경 계약

기준 클라이언트: `maple-client/dev` commit `1f552bb4b678121428574be176fedc33a2bed895`.

### 이미 반영된 항목

- 로그인과 회원가입 화면
- 기존 환자 검색 우선 / 신규 환자 탭
- `RegisterInput`의 `revisit | new` 분기
- 신규 환자 ID 읽기 전용 표시
- `autoAnalyze` 제거, 파일이 있으면 항상 분석 흐름
- 예약 시간 `08:00`~`17:00`, 10분 단위, 과거 슬롯 허용
- 실제 오늘 날짜 계산과 Today 표시 기반

위 항목은 백엔드 API로 교체할 seam이 이미 있으므로 다시 구현할 작업으로 세지 않는다.

### 인증

- 현재 회원가입 요청의 `hospital`을 서버 단일 병원과 검증한다.
- 데모 계정과 인증 폴백 제거
- 로그아웃 시 `POST /auth/logout`
- access/refresh token을 `sessionStorage` 세션에 보관
- 공통 axios 인스턴스에서 Bearer token 추가
- 401일 때 refresh 후 원 요청을 한 번만 재시도
- refresh 실패 시 세션 제거와 로그인 화면 이동

### 환자 검색과 접수

- `Workspace.searchPatients()`를 `GET /patients/search?q=`로 교체
- `kind=new`은 `POST /patients` multipart 호출
- `kind=revisit`은 `POST /patients/{patient_id}/appointments` multipart 호출
- 신규 환자의 화면용 임시 ID는 요청에서 보내지 않거나 서버가 무시하도록 유지
- 응답의 서버 발급 ID로 환자 선택
- 파일이 있으면 `queued`, 없으면 `waiting_for_files` 표시
- 서버 `409` 슬롯 충돌의 `conflict.doctor`, `scheduled_at`과 `422` 예약 검증 오류를 모달에 표시
- 예약 취소·정정은 `PATCH /appointments/{appointment_id}` 사용
- 로컬 `examLabelFromFiles()`는 제출 전 미리보기로만 사용하고 저장·재조회 후 서버 `exam_type`을 표시

### 환자 목록

- `getPatients()` 데모 생성 대신 `GET /patients?date=`
- 배열 위치 기반 `idx`를 React 상태 식별자로 사용하지 않고 `patient_id` 사용
- 목록 응답의 `appointment_id`, `latest_visit_id`, `latest_analysis_id` 보관
- `queued`, `analyzing`, `failed`, `waiting_for_files`, `done` 상태 표시
- `queued`일 때 선택적으로 대기 순번 표시
- “진료 완료”는 `appointment_status=completed` 기준
- `care_status`는 기존 4개 값만 유지

### 분석

- 현재 120초 동기 분석 호출을 enqueue + polling으로 변경
- 전역 `busy`를 환자 또는 `analysis_id`별 상태로 변경
- 완료 시 방문과 분석 결과 재조회
- `finding`, `interpretation`, `recommendation`, `risk_tier`, `confidence` 직접 사용
- `risk_status=unavailable` 또는 `risk_tier=null`이면 “미분류” 표시
- 클라이언트 정규식 파서는 구버전 응답 폴백으로만 유지
- confidence 기반 위험도 파생은 신규 DB 데이터에는 적용하지 않음

### 파일과 이미지

- 서버가 반환한 5분 서명 URL을 기존 `toSrc()`와 CSS `background-image`에 사용
- 분석 상세 응답의 URL을 만료까지 재사용
- URL 만료 시 `/analyses/{analysis_id}/access-urls` 한 번으로 전체 슬라이스 URL 갱신
- `ImageViewer`의 `new Image()`에서 `src`보다 먼저 `crossOrigin="anonymous"` 설정
- 서버의 정확한 CORS origin 응답과 함께 canvas `toDataURL()` 동작 확인
- object URL 전환과 `revokeObjectURL` 생명주기는 서명 URL 방식을 채택했으므로 필요하지 않음
- Electron 프로덕션 로딩을 `file://`에서 `app://maple` 커스텀 프로토콜로 전환

### 채팅

- 클라이언트가 최근 20개 history를 직접 만들지 않는다.
- `POST /visits/{visit_id}/chat`에 질문만 보낸다.
- 서버가 전체 환자 이력과 분석 결과를 조립한다.

### 날짜

- 실제 서울 날짜와 같은 경우에만 `(Today)` 표시
- API에는 `Today` 문자열을 보내지 않는다.

---

## 15. 설정값

추가할 환경변수 예:

```dotenv
# MongoDB transaction 전제
MONGO_URI=mongodb://localhost:27017/?replicaSet=rs0

# 인증
JWT_SECRET=
JWT_ALGORITHM=HS256
ACCESS_TOKEN_MINUTES=30
REFRESH_TOKEN_DAYS=7

# 단일 병원
HOSPITAL_ID=champion
HOSPITAL_NAME=챔피언 병원
APP_TIMEZONE=Asia/Seoul

# CORS
CLIENT_ORIGINS=http://localhost:3000,app://maple

# 파일
MAX_UPLOAD_BYTES=
GRIDFS_BUCKET=fs
FILE_SIGNING_SECRET=
FILE_SIGNED_URL_SECONDS=300

# 감사
AUDIT_RETENTION_DAYS=365

# 분석 worker
ANALYSIS_WORKER_ID=
ANALYSIS_GLOBAL_CONCURRENCY=3
ANALYSIS_LEASE_SECONDS=300
ANALYSIS_MAX_ATTEMPTS=2
ANALYSIS_POLL_INTERVAL_SECONDS=2
```

의사별 동시 실행은 항상 1이고 `ANALYSIS_GLOBAL_CONCURRENCY`는 서로 다른 의사 큐를 동시에 몇 개까지 실행할지 제한한다.

---

## 16. 구현 모듈 제안

```text
controllers/
  auth_controller.py
  patients_controller.py
  visits_controller.py
  analyses_controller.py
  files_controller.py
  chat_controller.py

services/
  auth_service.py
  patient_service.py
  visit_service.py
  analysis_queue_service.py
  clinical_inference_service.py
  file_service.py
  chat_service.py
  context_builder.py
  audit_service.py

repositories/
  doctors_repository.py
  sessions_repository.py
  patients_repository.py
  appointments_repository.py
  visits_repository.py
  analyses_repository.py
  notes_repository.py
  chat_repository.py
  files_repository.py
  audit_repository.py

models/
  auth_schemas.py
  patient_schemas.py
  visit_schemas.py
  analysis_schemas.py
  chat_schemas.py

workers/
  analysis_worker.py

scripts/
  ensure_indexes.py
  seed_clinical_demo.py
```

현재 `config/database.py`의 전역 Mongo client와 `main.py` lifespan의 Mongo client가 중복되므로 구현 전에 하나로 통일한다.

---

## 17. 구현 순서

### Phase 1 — 기반

- MongoDB를 standalone에서 single-node replica set으로 전환
- `MONGO_URI`에 `replicaSet=rs0` 적용 및 transaction smoke test
- Mongo 연결 lifecycle 통일
- 컬렉션 repository와 인덱스 초기화
- 공통 ObjectId/API 직렬화
- 설정값과 오류 응답 규격

### Phase 2 — 인증

- 의사 가입, 로그인, refresh, logout, me
- 비밀번호 해싱과 JWT
- 초기 의사 3명 seed
- 클라이언트 refresh interceptor와 sessionStorage 세션
- Electron 패키징 렌더러를 `app://maple` 커스텀 프로토콜로 전환
- 신규 임상 API와 기존 `/projects`, `/inference`, `/pipeline`, `/admin` 보호
- `/admin` 역할 검사
- CORS 전면 개방 제거 및 허용 origin 설정

### Phase 3 — 환자 흐름

- counters
- 환자·예약·방문 컬렉션
- 신규 환자 및 재방문 접수 트랜잭션
- 환자 검색
- 예약 시각 검증과 의사별 슬롯 unique index
- 예약 취소·정정·진료 완료
- 날짜별 목록, 상세, 예약·방문 이력
- 환자 40명 seed

### Phase 4 — 분석 큐

- analyses와 doctor locks
- 의사별 직렬, 의사 간 병렬 worker
- 예약 시각 우선순위
- lease와 장애 복구
- 상태 polling API
- 기존 추론 로직 service화

### Phase 5 — 결과와 파일

- 구조화 분석 결과
- 모델별 `RiskPolicyService`
- GridFS와 파일 메타데이터
- 분석 상세의 5분 서명 URL 일괄 포함과 분석 단위 갱신 API
- 서명 query 로그 제거·마스킹
- `app://maple` 파일 CORS와 canvas 익명 이미지 검증
- 원본/heat/box 슬라이스 매핑

### Phase 6 — 메모와 채팅

- 메모 API
- 채팅 저장
- 전체 방문 문맥 builder
- Agent history 및 분석 결과 전달

### Phase 7 — 운영 보안

- 감사 로그
- 업로드 제한
- 로그인 rate limit
- 민감정보 로그 마스킹
- 통합 테스트와 장애 복구 테스트

Admin 전용 UI와 관리 API는 후속 범위다.

---

## 18. 테스트 기준

최소 통합 테스트:

1. 동시에 환자를 등록해도 환자 ID가 중복되지 않는다.
2. 환자 검색이 정규화된 이름 앞자리 일치와 정규화된 환자번호를 지원한다.
3. 재방문 접수는 환자를 새로 만들지 않고 예약·방문만 추가한다.
4. 신규·재방문 접수 시 예약·방문이 모두 생성된다.
5. 진료시간 외 또는 10분 단위가 아닌 예약은 `422`이며 과거 예약은 허용한다.
6. 같은 의사의 같은 활성 슬롯 동시 예약 중 하나만 성공하고 나머지는 `409`다.
7. A 담당 환자를 B가 재방문 접수하면 환자 담당의는 A, 새 예약·방문·큐는 B다.
8. 예약 시각 변경은 queued 분석의 시각만 같은 트랜잭션에서 갱신한다.
9. 예약 취소는 queued 분석을 취소하고 analyzing 분석은 중단하지 않는다.
10. 예약 취소 후 같은 슬롯을 다시 사용할 수 있다.
11. 파일이 있으면 분석이 `queued`, 없으면 집계 상태가 `waiting_for_files`다.
12. 같은 의사의 분석 두 건은 동시에 `analyzing`이 되지 않는다.
13. 다른 의사의 분석은 동시에 `analyzing`이 될 수 있다.
14. 더 이른 예약의 queued 작업이 먼저 선택된다.
15. 유효한 lock 보유 중 E11000은 정상적인 잠금 실패로 처리된다.
16. 실행 중 worker가 죽으면 lease 만료 후 작업을 회수한다.
17. 분석 실패 후 같은 의사의 다음 작업이 진행된다.
18. `failed`, `cancelled` 분석의 `risk_status`는 `unavailable`이다.
19. 다른 병원 ID의 환자 데이터에 접근할 수 없다.
20. 일반 회원가입으로 Admin 역할을 만들 수 없다.
21. 로그아웃한 refresh token을 다시 사용할 수 없다.
22. access 만료 시 열린 클라이언트 세션이 refresh 후 요청을 한 번 재시도한다.
23. 기존 `/projects`, `/inference`, `/pipeline`, `/admin`은 무인증 호출을 거부한다.
24. 일반 doctor가 `/admin` 변경 API를 호출할 수 없다.
25. 채팅 질문에 과거 방문 분석과 채팅이 context로 포함된다.
26. 환자와 방문 날짜는 실제 오늘일 때만 UI에서 Today로 표시된다.
27. 서버 `exam_type`은 고정 순서이며 허용되지 않은 확장자는 `415`다.
28. 분석 상세 한 번으로 모든 슬라이스의 서명 URL을 받는다.
29. 분석 단위 URL 갱신이 모든 이미지 URL을 한 번에 반환한다.
30. Bearer 권한 없이 분석 URL을 발급할 수 없다.
31. 서명 없는 직접 파일 GET과 만료·변조된 서명 URL은 거부된다.
32. 감사·uvicorn·proxy 로그에 `sig`, `exp` 원문이 남지 않는다.
33. 패키징된 `app://maple` 앱에서 API CORS, CSS 배경, canvas 저장이 동작한다.
34. 목록 DTO가 polling에 필요한 최신 방문·분석 ID를 제공한다.

---

## 19. 남은 외부 확인 사항

다음 항목은 설계를 막지는 않지만 구현 중 연동 확인이 필요하다.

1. 초기 3개 의사 계정의 실제 이름, 사번, 진료과, 직급
2. 각 임상 모델의 정규화 출력과 `RiskPolicyService` 정책값
3. Agent 서버의 전체 환자 문맥 입력 한도와 요약 계약
4. 파일별 최대 크기와 허용 개수

기본 구현값:

- 가입 성공 후 토큰을 발급해 자동 로그인 가능하게 한다.
- 미래 예약 분석도 오늘 작업이 없으면 실행한다.
- 상태 확인은 2~3초 polling으로 시작한다.
- 분석 큐는 요청 의사 기준으로 동작한다.
- 파일 접근은 5분 서명 URL을 사용한다.
- 데모와 운영 모두 실제 서울 날짜를 사용한다.
- 의료 기록은 물리 삭제하지 않는다.
