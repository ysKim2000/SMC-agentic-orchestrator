# Runtime Architecture

> 작성일: 2026-05-25

---

## 개요

maple-platform 추론 서버는 두 가지 실행 모드를 지원합니다.

| 모드 | 엔드포인트 | 설명 |
|------|-----------|------|
| `legacy_image` | `POST /infer` | 모델별 개별 컨테이너로 직접 라우팅 (기존 방식) |
| `external_runtime` | `POST /infer/v2` | 공유 runtime 컨테이너로 라우팅 (신규 방식) |

---

## 아키텍처 다이어그램

```
Client
  │
  ▼
┌─────────────────────────────────┐
│    inference-gateway (8110)     │
│         main.py                 │
│                                 │
│  POST /infer      POST /infer/v2│
│      │                 │        │
└──────┼─────────────────┼────────┘
       │                 │
       │    ┌────────────┘
       │    │  config.yaml 읽기 → runtime 결정
       │    │
       ▼    ▼
   [legacy]  [runtime containers]
   개별 컨테이너  ┌─────────────────────────┐
   /run         │  runtime-medical (9021)  │  BraTS, ChestXray14
                │  runtime-yolo    (9022)  │  RSNA Pneumonia
                │  runtime-basic   (9020)  │  범용 GPU
                │  runtime-nnunet  (9023)  │  nnU-Net 모델
                └─────────────────────────┘
                         │
                   POST /run/v2
                         │
                  runtime_server.py
                         │
                  runner.py (동적 로드)
                         │
                  predict(input_path, output_dir, config)
```

---

## 파일 구조

```
maple-model-execution-server/
│
├── main.py                        # Gateway: /infer (legacy) + /infer/v2 (runtime)
├── Dockerfile                     # Gateway 컨테이너
├── requirements.txt
├── docker-compose.yml             # 기존 개별 모델 컨테이너 (legacy)
├── docker-compose.runtime.yml     # 신규 runtime 컨테이너
│
├── app/
│   ├── __init__.py
│   ├── model_config.py            # config.yaml 로더
│   ├── runtime_loader.py          # runtime 이름 → 컨테이너 URL
│   ├── runner_loader.py           # runner.py 동적 로드 + predict() 호출
│   └── runtime_server.py          # runtime 컨테이너용 FastAPI 서버
│
├── docker/
│   ├── Dockerfile.runtime-basic   # NV PyTorch 25.12 + 공통 의료 라이브러리
│   ├── Dockerfile.runtime-medical # runtime-basic + MONAI + TorchXRayVision
│   ├── Dockerfile.runtime-yolo    # python:3.12-slim + ultralytics
│   └── Dockerfile.runtime-nnunet  # runtime-basic + nnunetv2
│
├── models/                        # 모델별 config + runner (volume mount)
│   └── example_model/
│       ├── config.yaml            # 모델 설정
│       └── runner.py              # predict() 구현
│
└── docs/
    └── runtime_architecture.md    # 이 문서
```

---

## 새 모델 추가 방법

### 1. `models/<model_name>/` 디렉터리 생성

```
models/
└── my_new_model/
    ├── config.yaml
    ├── runner.py
    └── checkpoint/
        └── model.pt          ← volume mount, image에 포함 안 됨
```

### 2. `config.yaml` 작성

```yaml
model_name: my_new_model
execution_mode: external_runtime
runtime: runtime-medical          # 사용할 runtime 컨테이너
runner_path: /app/models/my_new_model/runner.py
model_path: /app/models/my_new_model/checkpoint/model.pt
params:
  threshold: 0.5
  device: cuda
```

### 3. `runner.py` 구현

```python
def predict(input_path: str, output_dir: str, config: dict) -> dict:
    # 1. 모델 로드 (config["model_path"] 사용)
    # 2. 입력 데이터 전처리
    # 3. 추론
    # 4. 결과를 output_dir 에 저장
    # 5. JSON-serializable dict 반환
    return {"prediction": ..., "output_file": ...}
```

### 4. 추론 요청

```bash
curl -X POST http://localhost:8110/infer/v2 \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "my_new_model",
    "input_path": "/app/inputs/sample.nii.gz",
    "params": {"threshold": 0.6}
  }'
```

---

## Runtime 컨테이너별 용도

| Runtime | 포트 | Base Image | 주요 라이브러리 | 적합한 모델 |
|---------|------|-----------|----------------|------------|
| `runtime-basic` | 9020 | nvcr.io/nvidia/pytorch:25.12-py3 | nibabel, scipy, scikit-image | 범용 GPU |
| `runtime-medical` | 9021 | runtime-basic | + MONAI 1.5.2, TorchXRayVision 1.4.0 | BraTS, ChestXray14 |
| `runtime-yolo` | 9022 | python:3.12-slim | ultralytics, pydicom | RSNA Pneumonia |
| `runtime-nnunet` | 9023 | runtime-basic | + nnunetv2 | nnU-Net 기반 모델 |

---

## 환경변수

### Gateway (main.py)

| 변수 | 설명 | 예시 |
|------|------|------|
| `RUNTIME_BASIC_URL` | runtime-basic 컨테이너 URL | `http://runtime-basic:8000` |
| `RUNTIME_MEDICAL_URL` | runtime-medical 컨테이너 URL | `http://runtime-medical:8000` |
| `RUNTIME_YOLO_URL` | runtime-yolo 컨테이너 URL | `http://runtime-yolo:8000` |
| `RUNTIME_NNUNET_URL` | runtime-nnunet 컨테이너 URL | `http://runtime-nnunet:8000` |
| `MODELS_DIR` | models/ 디렉터리 경로 | `/app/models` |

### Runtime 컨테이너 (runtime_server.py)

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `MODELS_DIR` | models/ 마운트 경로 | `/app/models` |
| `INPUTS_DIR` | 입력 파일 디렉터리 | `/app/inputs` |
| `OUTPUTS_DIR` | 출력 파일 디렉터리 | `/app/outputs` |

---

## Legacy vs Runtime 비교

| 항목 | Legacy (`/infer`) | Runtime (`/infer/v2`) |
|------|------------------|----------------------|
| 라우팅 기준 | `params.container_url` (클라이언트 지정) | `config.yaml`의 `runtime` 필드 |
| 모델 추가 | Dockerfile 작성 + 이미지 빌드 필요 | `models/` 디렉터리에 파일 추가만 |
| 가중치 위치 | 이미지 내 포함 | volume mount (`models/*/checkpoint/`) |
| 이미지 수 | 모델 수 × 1 | 런타임 종류 수 (현재 4개) |
| GPU 공유 | 컨테이너별 독립 | 같은 runtime 내 순차 처리 |
