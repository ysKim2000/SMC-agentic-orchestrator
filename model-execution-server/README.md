# Model Execution Server

여러 진료과의 의료 AI 모델을 **하나의 추론 게이트웨이 뒤에서 실행**하는 서버입니다.
라우팅 서버는 모델별 컨테이너 위치를 알 필요 없이 `model_name`만 넘기고,
게이트웨이가 모델 설정을 읽어 알맞은 runtime 컨테이너로 요청을 보낸 뒤 응답을 하나의 형식으로 정규화합니다.

> 모델 가중치와 모델별 패키지(`AI_Models/`, `models/`)는 이 저장소에 포함하지 않습니다.
> 구조를 보여주기 위한 템플릿 `models/example_model/`과 모델 제출 규격 [AI_Models/Manual.md](AI_Models/Manual.md)만 들어 있습니다.

## 아키텍처

```
routing-server
     │  POST /infer/v2 {model_name, input_path}
     ▼
inference-gateway (:8110)
     │  models/<model_name>/config.yaml  →  runtime 선택
     ▼
runtime 컨테이너 (basic · medical · yolo · nnunet)
     │  runner.py 동적 로드
     ▼
predict(input_path, output_dir, config)  →  inference.py (모델 패키지)
```

| 계층 | 역할 |
|------|------|
| **Gateway** ([main.py](main.py)) | 모델 설정 검증, runtime 라우팅, 응답 정규화, 모델별 타임아웃, 헬스/레디니스 |
| **Runtime** ([app/runtime_server.py](app/runtime_server.py)) | 공용 GPU 컨테이너. 요청마다 해당 모델의 `runner.py`를 불러와 실행 |
| **Model package** | `config.yaml` + `runner.py` + `inference.py` + 가중치. 볼륨으로 마운트 |

## 설계 포인트

- **모델마다 이미지를 만들지 않습니다.** 초기에는 모델마다 Docker 이미지를 따로 빌드했지만, 의존성이 비슷한 모델끼리 묶어 공용 runtime 4종으로 통합했습니다. 새 모델은 `config.yaml`과 `runner.py`만 추가하면 되고 이미지를 다시 빌드할 필요가 없습니다.
- **설정 기반 라우팅.** 게이트웨이는 `config.yaml`의 `runtime` 값으로만 대상을 결정합니다. 클라이언트가 보낸 `container_url`은 v2 경로에서 무시되고, legacy 경로에서도 절대 HTTP(S) URL만 허용합니다.
- **조용한 실패를 막는 readiness.** `GET /ready`는 모든 모델 설정과 4개 runtime의 상태를 검사합니다. 설정이 잘못된 모델은 건너뛰지 않고 `errors`에 그대로 드러냅니다.
- **모델별 타임아웃.** 3D CT 분할처럼 오래 걸리는 모델은 `config.yaml`의 `timeout:`으로 개별 제한을 둡니다.
- **재현 가능한 runtime.** 체크포인트가 학습 당시 프레임워크 버전에 묶이는 경우(nnU-Net 2.8.1 등)에는 Dockerfile에서 버전을 고정했습니다.

## Runtime 구성

| Runtime | Base | 주요 라이브러리 | 대상 |
|---------|------|-----------------|------|
| `runtime-basic` | NVIDIA PyTorch 25.12 | nibabel, scipy, scikit-image | 일반 분류, 임상 점수 |
| `runtime-medical` | runtime-basic | MONAI, TorchXRayVision, sktime | 영상 분류·분할, ECG |
| `runtime-yolo` | python 3.12-slim | ultralytics, pydicom | 객체 탐지 |
| `runtime-nnunet` | runtime-basic | nnunetv2 2.8.1, TotalSegmentator | 3D 분할 |

## 운영 중인 모델

8개 진료과, 89개 모델 (분류, 분할, 탐지, 키포인트, 회귀)

| 진료과 | 모델 수 | 주요 모달리티 |
|--------|:------:|---------------|
| Pulmonology | 33 | Chest X-ray, CT |
| Orthopedics | 19 | X-ray, CT, MRI |
| Neurology | 10 | Brain MRI, 임상 변수 |
| Cardiology | 7 | 12-lead ECG |
| Dermatology | 7 | 임상 사진, 더모스코피 |
| Obstetrics | 5 | 유방촬영, 초음파, 세포·조직병리 |
| Gastroenterology | 4 | 내시경, 조직병리 |
| Ophthalmology | 4 | 안저 사진, 세극등 사진 |

## 디렉터리 구조

```
model-execution-server/
├── main.py                      # inference gateway
├── app/
│   ├── model_config.py          # config.yaml 로드·검증
│   ├── runtime_loader.py        # runtime 이름 → URL
│   ├── runner_loader.py         # runner.py 동적 로드
│   └── runtime_server.py        # runtime 컨테이너 FastAPI 서버
├── docker/                      # runtime 4종 Dockerfile
├── docker-compose.yml           # gateway
├── docker-compose.runtime.yml   # runtime 컨테이너
├── models/example_model/        # 모델 등록 템플릿
├── AI_Models/Manual.md          # 모델 패키지 제출 규격
└── docs/runtime_architecture.md
```

## 실행

```bash
# runtime-basic을 먼저 빌드 (medical, nnunet이 이 이미지를 기반으로 함)
docker compose -f docker-compose.runtime.yml build runtime-basic

# gateway + runtime 전체 실행
docker compose -f docker-compose.yml -f docker-compose.runtime.yml up -d --build

curl http://localhost:8110/ready
```

## 모델 추가

```yaml
# models/<model_name>/config.yaml
model_name: my_model
execution_mode: external_runtime
runtime: runtime-medical
runner_path: /app/models/my_model/runner.py
inference_path: /app/AI_Models/<Department>/my_model/inference.py
model_path: /app/AI_Models/<Department>/my_model/checkpoint/best.pt
```

```python
# models/<model_name>/runner.py
def predict(input_path: str, output_dir: str, config: dict) -> dict:
    ...
    return {"images_b64": [...], "output_files": [...]}
```

```bash
curl -X POST http://localhost:8110/infer/v2 \
  -H "Content-Type: application/json" \
  -d '{"model_name": "my_model", "input_path": "/app/inputs/sample.nii.gz"}'
```

자세한 구조는 [docs/runtime_architecture.md](docs/runtime_architecture.md)를 참고하세요.
