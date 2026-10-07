# Maple AI 플랫폼 — 모델 제출 가이드

> 이 문서는 **연구원**이 새로운 추론 모델을 Maple AI 플랫폼에 등록하기 위해 제출해야 하는 파일과 작성 규칙을 안내합니다.
> `server.py`, `Dockerfile`, 플랫폼 등록 절차는 **플랫폼 관리자**가 처리합니다.

---

## 제출 파일 목록

| 파일 | 필수 여부 | 설명 |
|---|---|---|
| `inference.py` | **필수** | 추론 로직. `main()` 함수 구현 |
| `checkpoint/` | **필수** | 모델 가중치 파일 폴더 |
| `meta.json` | **필수** | 모델 메타데이터 |
| `requirements.txt` | **필수** | Python 의존성 목록 |
| `sample_data/` | **필수** | 테스트용 입력 샘플 1~2개 |
| `ref/` | 조건부 필수 | 전처리에 사용되는 참조 파일 (있는 경우) |
| `README.md` | 권장 | 모델 상세 설명 (학습 방법, 성능 지표 등) |

---

## 폴더 구조

```
AI_Models/
└── {진료과}/
    └── {모델명}/
        ├── inference.py
        ├── meta.json
        ├── requirements.txt
        ├── checkpoint/
        │   └── model.pt          # 모델 가중치
        ├── sample_data/
        │   ├── sample_01.dcm     # 테스트용 입력 샘플 1~2개
        │   └── sample_02.dcm
        ├── ref/                  # 전처리에 참조 파일이 필요한 경우
        │   └── ref_image.dcm
        └── README.md
```

**네이밍 규칙**
- 경로는 **영어**로 작성합니다.
- 폴더명에 **공백 없이** 언더스코어(`_`)로 구분합니다.
  - ✅ `ParkinsonGait_ML`, `SI_Joints_Detection`
  - ❌ `SI Joints Detection`

---

## 1. `inference.py`

플랫폼이 호출하는 핵심 파일입니다. **반드시 `main` 함수를 구현**해야 합니다.  
`main` 함수를 통한 return 값은 `결과`(GradCAM, classification result 등) 입니다.  
`return` 값은 여러 개여도 상관 없습니다. 

### 함수 시그니처

```python
def main(input_data, model_path: str):
    """
    Args:
        input_data: 입력 데이터 (아래 입력 유형별 타입 참고)
        model_path: 모델 가중치 파일/폴더의 절대 경로 (str)
                    플랫폼이 자동으로 주입합니다. 하드코딩 금지.

    Returns:
        결과 유형에 따라 아래 반환값 규격을 따릅니다.
    """
```

### 입력 데이터 유형

`meta.json`의 `required_data` 필드에 맞춰 입력 타입이 결정됩니다.

| `required_data` | `input_data` 타입 | 설명 |
|---|---|---|
| `["csv"]` | `pd.DataFrame` | CSV 파일을 DataFrame으로 변환하여 전달 |
| `["dcm"]` | `str` (파일 경로) | DICOM 파일의 절대 경로 |
| `["nii.gz", "nii"]` | `str` (파일 경로) | NIfTI 파일(`.nii.gz`)의 절대 경로 |
| `["png", "jpg", "jpeg"]` | `str` (파일 경로) | PNG/JPG 이미지 파일의 절대 경로 |

---

### 파이프라인 의존 모델 (앞 단계 결과가 필요한 경우)

모델이 단독으로 동작하지 않고, **앞 단계 모델의 출력(e.g. ROI 좌표, Bounding Box)을 입력으로 받아야 하는 경우**에 해당합니다.

**예시: BME Classification**
- 1단계 `SI_Joints_Detection`이 좌/우 천장관절의 ROI 좌표를 탐지
- 2단계 `BME_Classification`이 해당 ROI를 받아 분류 수행

이 경우 `input_data`는 `dict` 형태로 전달됩니다:

```python
def main(input_data: dict, model_path: str):
    """
    Args:
        input_data: dict
            - "image_path": str   — DICOM 파일 경로
            - "roi": dict         — 앞 단계에서 전달된 ROI 좌표
                {
                    "left":  [x1, y1, x2, y2],  # 없으면 None
                    "right": [x1, y1, x2, y2],  # 없으면 None
                }
        model_path: str
    """
    image_path = input_data["image_path"]
    roi        = input_data["roi"]
    ...
```

**`meta.json`에 파이프라인 관계 명시**

앞 단계 모델이 필요한 경우 `meta.json`에 `pipeline_after` 필드를 추가해주세요.
플랫폼 관리자가 파이프라인 순서를 구성할 때 참고합니다.

```json
{
  "model_name": "GradCAM++",
  "description": "BME classification model. Takes ROI from SI_Joints_Detection and returns GradCAM++ heatmap overlays.",
  "task_type": "classification",
  "disease": "axial spondyloarthritis, bone marrow edema, BME",
  "required_data": ["dcm"],
  "result_type": ["gradcam_overlay", "classification_probabilities"],
  "output_image_role": "gradcam_overlay",
  "pipeline_after": "SI_Joints_Detection",
  "pipeline_input": {
    "image_path": "DICOM 파일 경로",
    "roi": "SI_Joints_Detection이 반환한 좌/우 ROI 좌표 dict"
  },
  "docker": {
    "service_url": "관리자 작성 예정"
  }
}
```

| 필드 | 설명 |
|---|---|
| `pipeline_after` | 이 모델 실행 전 먼저 실행되어야 하는 모델 폴더명 |
| `pipeline_input` | `input_data` dict의 키와 각 값의 출처 설명 (관리자 연동 참고용) |

> 단독으로 실행 가능한 모델은 `pipeline_after` 필드를 생략합니다.

### 반환값 규격

**분류/텍스트 결과** (`result_type: "text"`)

```python
import pandas as pd

def main(input_data, model_path):
    # ... 추론 로직 ...
    result_df = pd.DataFrame({
        "pred":      [0],           # 정수 레이블
        "pred_name": ["normal"],    # 레이블 이름 (문자열)
    })
    return result_df
```

반드시 `pred`, `pred_name` 컬럼을 포함한 `pd.DataFrame`을 반환하세요.

**이미지 결과** (`result_type: ["bbox_overlay", ...]` 등 이미지 타입 리스트)

```python
import numpy as np

def main(input_data, model_path):
    # ... 추론 로직 ...
    result_image = ...  # np.ndarray, shape (H, W, 3), dtype=uint8, RGB
    return result_image
```

이미지가 여러 장이면 리스트로 반환합니다:

```python
return [image1, image2, ...]  # list[np.ndarray]
```

이미지가 여러 장인 경우, **각 이미지가 무엇인지 `README.md`에 반드시 명시**해주세요.
플랫폼이 이미지를 순서대로 표시하므로, 순서가 의미를 가집니다.

```
# 예시 (nnUNet_SMWI_Segmentation)
[0] 세그멘테이션 3D 렌더링
[1]~[N] Segmentation이 있는 axial slice (원본 MRI + Overlay)
```

**이미지 + 분류 확률 동시 반환** (GradCAM 등)

```python
def main(input_data, model_path):
    result_image = ...   # np.ndarray
    predictions = [
        {"side": "left",  "prob": 0.87, "pred": 1, "pred_name": "BME"},
        {"side": "right", "prob": 0.12, "pred": 0, "pred_name": "normal"},
    ]
    return result_image, predictions
```

### 작성 시 주의사항

- `model_path`는 **플랫폼이 주입**합니다. 코드 내부에 경로를 하드코딩하지 마세요.

  ```python
  # ❌ 하드코딩 금지
  model = torch.load("/home/researcher/models/my_model.pt")

  # ✅ model_path 사용
  model = torch.load(model_path)
  ```

- `inference.py`는 **순수 추론 로직만** 담습니다. HTTP 서버, FastAPI 코드는 포함하지 마세요.

---

## 2. `checkpoint/`

모델 가중치 파일을 이 폴더에 넣어주세요.

```
checkpoint/
└── model.pt          # 파일명은 자유, 단 meta.json의 model_path_dict와 일치해야 함
```

- `.pt`, `.pth` (PyTorch), `.pkl` (scikit-learn), `.onnx` 등 형식 자유
- 파일이 크거나 공개 불가한 경우 관리자에게 별도 전달하세요

---

## 3. `sample_data/`

플랫폼 관리자가 모델 연동 테스트에 사용할 샘플 입력 파일입니다. **1~2개** 포함해주세요.

```
sample_data/
├── sample_01.dcm     # DICOM 모델
├── sample_01.nii.gz  # NIfTI 모델
└── sample_01.csv     # CSV 모델
```

- 실제 추론이 정상적으로 동작하는 파일이어야 합니다.
- 환자 정보가 포함된 경우 익명화 후 제출하세요.

---

## 4. `ref/` (조건부)

전처리 단계에서 **고정된 참조 파일**이 필요한 경우 이 폴더에 넣어주세요.

```
ref/
└── ref_image.dcm     # 히스토그램 매칭 기준 이미지 등
```

**해당하는 경우:**
- 히스토그램 매칭 기준 이미지
- 정규화 통계값 파일 (mean/std `.npy` 등)
- Atlas, template 이미지

**`inference.py`에서 참조 파일 경로 작성 방법:**

참조 파일은 `inference.py`와 같은 폴더 기준으로 상대 경로를 사용하세요.
`model_path`처럼 플랫폼이 자동 주입하지 않으므로, 코드 내부에서 아래와 같이 처리합니다.

```python
from pathlib import Path

BASE_DIR = Path(__file__).parent   # inference.py가 있는 폴더

def main(input_data, model_path):
    ref_path = BASE_DIR / "ref" / "ref_image.dcm"
    # ... 전처리에 ref_path 사용 ...
```

> 참조 파일이 없으면 `ref/` 폴더는 만들지 않아도 됩니다.

---

## 5. `meta.json`

플랫폼이 모델을 자동 인식하고 AI Agent에 등록하기 위해 사용하는 메타데이터입니다.

### 전체 형식

아래가 `meta.json`의 전체 구조입니다. **연구원 작성** 항목만 채워서 제출하면 됩니다.

```json
{
  "model_name":        "모델 표시명 (영문 필수)",
  "description":       "모델 설명 — 영어로 작성. 질환명, 입력 데이터, 출력 형태를 포함 (ChromaDB 임베딩 품질에 직접 영향)",
  "task_type":         "classification | segmentation | bbox detection | ...",
  "disease":           "comma-separated condition keywords (영어 권장)",
  "model_type":        "standalone | pipeline",
  "required_data":     ["dcm"] | ["nii.gz", "nii"] | ["png", "jpg", "jpeg"] | ["csv"],
  "result_type":       "text" | ["bbox_overlay", "detection_predictions"] | ["gradcam_overlay", "classification_probabilities"] | ["segmentation_overlay", "3d_overlay"],
  "output_image_role": "bbox_overlay | gradcam_overlay | segmentation_overlay | 3d_overlay | null",

  "pipeline_after":    "앞 단계 모델 폴더명 (pipeline일 때만 작성)",
  "pipeline_input": {
    "image_path":      "DICOM 파일 경로",
    "roi":             "앞 단계 모델이 반환하는 ROI 좌표 dict"
  },

  "docker": {
    "service_url":     "관리자 작성 예정"
  }
}
```

> `pipeline_after`, `pipeline_input`은 `model_type`이 `"pipeline"`인 경우에만 작성합니다.  
> `output_image_role`은 `result_type`이 이미지 타입 리스트인 경우에만 작성합니다. 텍스트 결과 모델은 `null`로 두세요.  
> `docker.service_url`은 **플랫폼 관리자가 컨테이너 포트를 배정한 뒤 채웁니다.** 연구원은 `"관리자 작성 예정"` 그대로 두세요.

### 필드 설명

| 필드 | 설명 |
|---|---|
| `model_name` | 모델 표시명. 영문 권장 (e.g. `YOLOv12`, `GradCAM++`) |
| `description` | 모델 요약. **질환명, 입력 데이터, 출력 형태** 포함. AI Agent 검색에 사용 |
| `task_type` | `"classification"`, `"segmentation"`, `"bbox detection"` 등 |
| `disease` | 쉼표로 구분된 질환/키워드. 사용자 질의 매칭에 사용 |
| `model_type` | `"standalone"` (단독 실행) 또는 `"pipeline"` (앞 단계 모델 필요) |
| `required_data` | 입력 파일 확장자 리스트. `["dcm"]`, `["nii.gz","nii"]`, `["png","jpg","jpeg"]`, `["csv"]` |
| `result_type` | 텍스트 결과는 `"text"`. 이미지 결과는 실제 출력 타입 리스트: `["gradcam_overlay","classification_probabilities"]` 등 |
| `output_image_role` | 결과 이미지의 주 종류. AI Agent VLM이 이미지를 해석할 때 참고. 이미지 결과 모델만 작성 |
| `pipeline_after` | `pipeline`일 때만. 직전에 실행되어야 하는 모델의 폴더명 |
| `pipeline_input` | `pipeline`일 때만. `input_data` dict의 키와 각 값의 출처 설명 |
| `docker.service_url` | **관리자가 작성**. 컨테이너 포트 배정 후 채움. 연구원은 `"관리자 작성 예정"` 그대로 두세요 |

### `output_image_role` 값 목록

| 값 | 설명 | 사용 모델 예시 |
|---|---|---|
| `"bbox_overlay"` | Bounding box가 그려진 원본 영상 오버레이 | RSNA_Pneumonia_YOLO26x |
| `"gradcam_overlay"` | GradCAM/GradCAM++ 히트맵 오버레이 | ChestXray14_Multilabel_Classification |
| `"segmentation_overlay"` | 세그멘테이션 마스크 오버레이 (axial slice) | BraTS2020_T1_UNet3D |
| `"3d_overlay"` | 3D 볼륨 렌더링 이미지 | BraTS2020_T1_UNet3D |
| `"classification_probabilities"` | 다중 레이블 분류 확률값 | ChestXray14_Multilabel_Classification |
| `"detection_predictions"` | 탐지 결과 (bbox 좌표 + confidence) | RSNA_Pneumonia_YOLO26x |
| `null` | 이미지 결과 없음 (텍스트 결과 모델) | ParkinsonGait_ML |

> 위 목록에 없는 새로운 이미지 유형을 반환하는 경우, 관리자에게 새 role 값을 요청하세요.  
> AI Agent가 이 값을 보고 이미지의 의미를 파악하여 임상 해석에 활용합니다.

### 작성 예시

**standalone — CSV 입력, 텍스트 결과**
```json
{
  "model_name":        "ParkinsonGait_ML",
  "description":       "Parkinson fall-risk prediction model. Takes clinical gait data (CSV) and classifies fall-risk category.",
  "task_type":         "classification",
  "disease":           "Parkinson disease, fall risk",
  "model_type":        "standalone",
  "required_data":     ["csv"],
  "result_type":       "text",
  "output_image_role": null,
  "docker": {
    "service_url": "관리자 작성 예정"
  }
}
```

**standalone — NIfTI 입력, 이미지 결과**
```json
{
  "model_name":        "nnUNet_SMWI_Segmentation",
  "description":       "Brain deep structure segmentation model for Parkinson disease. Takes a SMWI NIfTI MRI and returns a 3D volume rendering and axial-slice overlay images segmenting RN, SN+STN, and Pu.",
  "task_type":         "segmentation",
  "disease":           "Parkinson disease, MSA, PSP, brain deep structure segmentation",
  "model_type":        "standalone",
  "required_data":     ["nii.gz", "nii"],
  "result_type":       ["segmentation_overlay", "3d_overlay"],
  "output_image_role": "segmentation_overlay",
  "docker": {
    "service_url": "관리자 작성 예정"
  }
}
```

**standalone — DICOM 입력, 이미지 결과**
```json
{
  "model_name":        "YOLOv12",
  "description":       "Sacroiliac joint detection model. Takes a DICOM MRI and localizes left/right sacroiliac joints with bounding boxes.",
  "task_type":         "bbox detection",
  "disease":           "axial spondyloarthritis, sacroiliac joint",
  "model_type":        "standalone",
  "required_data":     ["dcm"],
  "result_type":       ["bbox_overlay", "detection_predictions"],
  "output_image_role": "bbox_overlay",
  "docker": {
    "service_url": "관리자 작성 예정"
  }
}
```

**pipeline — DICOM 입력, 이미지 결과 (앞 단계 ROI 필요)**
```json
{
  "model_name":        "GradCAM++",
  "description":       "BME (Bone Marrow Edema) classification model. Takes a DICOM MRI and classifies BME presence in left/right sacroiliac joints, returning GradCAM++ heatmap overlays.",
  "task_type":         "classification",
  "disease":           "axial spondyloarthritis, bone marrow edema, BME",
  "model_type":        "pipeline",
  "required_data":     ["dcm"],
  "result_type":       ["gradcam_overlay", "classification_probabilities"],
  "output_image_role": "gradcam_overlay",
  "pipeline_after":    "SI_Joints_Detection",
  "pipeline_input": {
    "image_path": "원본 DICOM 파일 경로",
    "roi":        "SI_Joints_Detection이 반환한 좌/우 천장관절 ROI 좌표 dict"
  },
  "docker": {
    "service_url": "관리자 작성 예정"
  }
}
```

---

## 6. `requirements.txt`

모델 실행에 필요한 Python 패키지 목록입니다.
**의존성 충돌은 컨테이너 빌드 실패의 가장 흔한 원인**입니다. 아래 규칙과 주의사항을 꼼꼼히 읽어주세요.

---

### 기본 작성 규칙

**버전을 반드시 고정**하세요. `pip freeze` 출력을 그대로 붙여넣는 것을 권장합니다.

```
torch==2.5.1
numpy==1.26.4
scikit-learn==1.5.0
```

**실행 환경(CUDA 버전 등)을 주석으로 명시**해주세요. 관리자가 빌드 환경을 맞추는 데 사용합니다.

```
# Python 3.10 / CUDA 12.1
torch==2.5.1+cu121
torchvision==0.20.1+cu121
```

GPU가 필요 없는 CPU 전용 모델은 명시해주세요.

```
# CPU only (GPU 불필요)
torch==2.5.1
```

---

### ⚠️ 알려진 충돌 및 주의사항

#### 1. `numpy` 버전 충돌 — 가장 흔한 문제

많은 패키지가 `numpy` 버전에 민감합니다. 아래 패키지들은 **`numpy < 2.0`** 을 요구합니다.

| 패키지 | numpy 제약 | 권장 조합 |
|---|---|---|
| `pylibjpeg-libjpeg==2.1` | `numpy < 2.0` | `numpy==1.26.4` |
| `opencv-python==4.13.x` | `numpy >= 2.0` 요구 → 충돌 발생 | `opencv-python-headless==4.10.0.84` 사용 |
| `pandas==2.3.x` | `numpy >= 2.0` 요구 | `pandas==2.2.3` 으로 다운그레이드 |

**권장:** DICOM을 처리하는 모델은 `numpy==1.26.4`를 기본으로 사용하세요.

```
numpy==1.26.4
```

#### 2. `opencv` — headless 버전 사용

서버/컨테이너 환경에는 디스플레이가 없습니다. `opencv-python` 대신 반드시 `opencv-python-headless`를 사용하세요.

```
# ❌ 컨테이너에서 설치 실패 또는 런타임 오류
opencv-python==4.10.0.84

# ✅
opencv-python-headless==4.10.0.84
```

#### 3. DICOM JPEG 압축 해제 — `pylibjpeg` 필수

MRI DICOM 파일 중 상당수가 JPEG Lossless 압축을 사용합니다. 이 경우 `pylibjpeg`가 없으면 픽셀 데이터를 읽을 수 없습니다.

DICOM을 입력으로 받는 모델은 아래 패키지를 반드시 포함하세요.

```
pydicom==3.0.1
pylibjpeg==2.0
pylibjpeg-libjpeg==2.1
```

> `pylibjpeg-libjpeg==2.1`은 `numpy < 2.0`을 요구하므로, 위 numpy 충돌 항목도 함께 확인하세요.

#### 4. PyTorch CUDA 버전 — 환경에 맞게 명시

PyTorch는 CUDA 버전별로 별도 빌드가 존재합니다. 잘못된 버전을 쓰면 GPU를 인식하지 못하거나 설치 자체가 실패합니다.

```
# CUDA 12.1 빌드
torch==2.5.1+cu121
torchvision==0.20.1+cu121

# CUDA 12.8 빌드
torch==2.9.1
# → 설치 시 --extra-index-url https://download.pytorch.org/whl/cu128 필요
#   (관리자에게 전달)
```

PyTorch 공식 버전 확인: https://pytorch.org/get-started/previous-versions/

#### 5. pypi 메타데이터 버그 패키지

일부 패키지는 pypi에 배포되어 있지만 메타데이터 오류로 `pip install`이 실패합니다.
이 경우 관리자가 GitHub에서 직접 설치하므로, requirements.txt에 적더라도 **아래 패키지명과 버전만 주석과 함께 명시**하면 됩니다.

| 패키지 | 상태 | 처리 방법 |
|---|---|---|
| `nnunetv2` | pypi 메타데이터 버그 | 관리자가 GitHub 설치로 처리 |
| `batchgeneratorsv2` | pypi 메타데이터 버그 | nnunetv2 설치 시 자동 해결 |
| `vedo >= 2026.x` | pypi 메타데이터 버그 | 관리자가 GitHub 설치로 처리 |

```
# 아래 패키지는 pypi 메타데이터 버그로 pip 직접 설치 불가
# 관리자가 GitHub에서 설치함 — 버전만 명시
nnunetv2==2.6.3
```

#### 6. `vtk` vs `vtk-osmesa` — 헤드리스 환경 충돌

3D 렌더링에 VTK를 사용하는 경우, 서버 환경(디스플레이 없음)에서는 `vtk` 대신 `vtk-osmesa`를 사용해야 합니다. **두 패키지를 동시에 설치하면 충돌**합니다.

```
# ❌ 일반 vtk는 디스플레이 필요 → 서버에서 렌더링 불가
vtk==9.3.1

# ✅ vtk-osmesa는 별도 인덱스에서 설치 (관리자 처리)
vtk-osmesa==9.3.1
# → pip install --index-url https://wheels.vtk.org vtk-osmesa==9.3.1
```

---

### 올바른 작성 예시

```
# Python 3.10 / CUDA 12.1
# DICOM 입력 모델

torch==2.5.1+cu121
torchvision==0.20.1+cu121

pydicom==3.0.1
pylibjpeg==2.0
pylibjpeg-libjpeg==2.1       # numpy < 2.0 필요

numpy==1.26.4                 # pylibjpeg-libjpeg 호환
opencv-python-headless==4.10.0.84
Pillow==11.3.0
scikit-image==0.25.2
pandas==2.2.3
```

---

### Docker 빌드 후 inference 동작 확인

**관리자가 `Dockerfile`과 `server.py`를 작성한 뒤** 연구원은 아래 절차로 컨테이너에서 inference가 정상 동작하는지 확인할 수 있습니다.

**1. 컨테이너 빌드**

```bash
# maple-model-execution-server/ 루트에서 실행
docker-compose build {서비스명}
# 예: docker-compose build bme-classifier
```

빌드 로그에서 아래 항목을 확인하세요:
- `requirements.txt`의 모든 패키지가 에러 없이 설치됐는지
- `Successfully built` 메시지가 출력됐는지

**2. 컨테이너 실행**

```bash
docker-compose up -d {서비스명}
```

**3. `sample_data`로 inference 테스트**

컨테이너가 실행된 상태에서 `POST /run/v2`에 샘플 데이터를 전송해 추론이 정상 동작하는지 확인합니다.

```bash
# 예: RSNA Pneumonia (runtime-yolo, Port 9022)
curl -X POST http://localhost:9022/run/v2 \
  -H "Content-Type: application/json" \
  -d '{"model_name": "RSNA_Pneumonia_YOLO26x", "input_path": "/app/inputs/sample_01.dcm"}'
```

정상 응답 형태:
```json
{"status": "ok", "model_name": "RSNA_Pneumonia_YOLO26x", "result": {"image_b64": "...", "predictions": [...], "detection_count": 1}}
```

**4. 빌드 실패 시 로그 확인**

```bash
# 컨테이너 로그
docker-compose logs {서비스명}

# 빌드 실패 시 —no-cache로 재빌드
docker-compose build --no-cache {서비스명}
```

> 빌드나 런타임 오류가 발생하면 `requirements.txt`의 패키지 버전 충돌 또는 위 [알려진 충돌 및 주의사항](#️-알려진-충돌-및-주의사항) 항목을 먼저 확인하세요.
> 해결되지 않으면 오류 로그를 관리자에게 전달하세요.

---

## 7. LLM Wiki — 모델 정보 등록 구조

모델이 플랫폼에 등록되면(`POST /admin/models`), 백엔드는 자동으로 AI Agent에 `POST /agent/models/register`를 호출합니다.
Agent는 이를 받아 두 곳에 모델 정보를 저장합니다.

```
모델 등록 흐름
POST /admin/models
  └── MongoDB에 메타데이터 저장
  └── POST /agent/models/register 자동 호출
        ├── ChromaDB maple_models — 벡터 임베딩 등록 (모델 탐색용)
        └── /wiki/{model_id}.md  — Wiki 페이지 생성 (임상 해석용)
```

### Wiki 페이지에 저장되는 정보

Agent가 생성하는 Wiki 페이지(`/wiki/{model_id}.md`)에는 `meta.json`의 아래 필드가 그대로 기록됩니다.

| `meta.json` 필드 | Wiki 용도 |
|---|---|
| `model_name` | 모델 식별 |
| `description` | 모델이 무엇을 하는지 — Agent가 사용자 쿼리와 매칭할 때 핵심 |
| `disease` | 관련 질환 키워드 — 임상지식모드 RAG 검색에서 함께 활용 |
| `task_type` | 어떤 종류의 분석인지 |
| `required_data` | 어떤 파일을 올려야 하는지 |
| `result_type` | 이미지 결과인지 텍스트 결과인지 |
| `output_image_role` | 반환 이미지의 의미 — Agent VLM이 이미지를 보고 해석할 때 참고 |

추론 결과가 쌓이면서 **임상 해석 패턴도 Wiki에 누적**됩니다. Agent는 다음 해석 요청 시 이 패턴을 참고하여 해석 품질을 높입니다.

### `description`이 중요한 이유

`description`은 ChromaDB에 벡터로 임베딩되어, **특화모델모드에서 "이 쿼리에 어떤 모델을 써야 하나" 결정하는 핵심 기준**이 됩니다.

```
좋은 예 ✅
"BME(Bone Marrow Edema) 분류 모델. DICOM MRI를 입력받아 좌우 천장관절의
BME 여부를 분류하고 GradCAM++ 히트맵 이미지를 반환한다."

나쁜 예 ❌
"BME 분류기"  ← 질환명·입력·출력 정보가 없어 모델 탐색 정확도 저하
```

**질환명, 입력 데이터 형식, 출력 형태를 모두 포함해서 작성하세요.**

### 모델 삭제 시

`DELETE /admin/models/{model_name}` 호출 시 백엔드가 Agent에 `DELETE /agent/models/{model_id}`를 자동 호출하여 Wiki 페이지와 ChromaDB 임베딩을 함께 삭제합니다.

---

## 8. `README.md` (권장 양식)

모델별 README는 필수는 아니지만, 플랫폼 관리자가 연동 작업을 빠르게 진행할 수 있도록 아래 양식을 권장합니다.

```markdown
# {모델명}

## 개요
- **진료과**: Neurology / Rheumatology / ...
- **모델 타입**: standalone / pipeline
- **task_type**: classification / segmentation / bbox detection / ...
- **result_type**: text / image
- **output_image_role**: bbox_overlay / gradcam_overlay / segmentation_overlay / null

## 대상 질환
- ...

## 입력 데이터
| 항목 | 타입 | 설명 |
|------|------|------|
| ... | dicom / nifti / csv | ... |

## 출력
- **출력 타입**: image / text
- **output_image_role**: (이미지 모델인 경우 — AI Agent VLM 해석 참고용)
- **반환**: `np.ndarray` (H, W, 3) RGB / `pd.DataFrame`
- 이미지가 여러 장인 경우 각 인덱스 설명:

| 인덱스 | 내용 |
|--------|------|
| `[0]` | ... |
| `[1]~[N]` | ... |

## 모델 파일
| 파일 | 설명 |
|------|------|
| checkpoint/model.pt | ... |

## 모델 상세
| 항목 | 값 |
|------|----|
| 아키텍처 | ... |
| 학습 데이터 수 | ... |
| 성능 지표 | ... |

## 전처리
1. ...
2. ...

## 실행 예시
```python
from inference import main

result = main(
    input_data="sample_data/sample_01.dcm",
    model_path="checkpoint/model.pt"
)
```

## 비고
- ...
```

---

## 9. 제출 체크리스트

제출 전 아래 항목을 확인해주세요.

- [ ] 폴더명이 영어이고 공백이 없는지 확인
- [ ] `inference.py` — `main(input_data, model_path)` 함수 구현 여부
- [ ] `inference.py` — `python {진료과}/{모델 폴더명}/inference.py` 실행 완료 여부 
- [ ] `inference.py` — 모델 경로 하드코딩 없이 `model_path` 인자 사용 여부
- [ ] `inference.py` — 반환값이 규격에 맞는지 확인 (`DataFrame` 또는 `np.ndarray`)
- [ ] `checkpoint/` — 가중치 파일이 들어있는지 확인
- [ ] `sample_data/` — 정상 동작하는 입력 샘플 1~2개 포함 여부
- [ ] `ref/` — 전처리 참조 파일이 필요한 경우 포함 여부
- [ ] `meta.json` — 모든 필드 작성, `docker.service_url`은 "관리자 작성 예정"으로 두고 나머지 필드 작성 여부 확인
- [ ] `meta.json` — `model_type` 필드 (`standalone` / `pipeline`) 작성 여부
- [ ] `meta.json` — 이미지 결과 모델은 `result_type`을 타입 리스트로 작성 (예: `["gradcam_overlay", "classification_probabilities"]`), `output_image_role` 필드 작성 여부
- [ ] `requirements.txt` — 버전 고정, 실제 실행 환경과 일치하는지 확인
- [ ] `requirements.txt` — CUDA 버전 주석 명시 여부 (GPU 모델)
- [ ] `requirements.txt` — `opencv-python` 대신 `opencv-python-headless` 사용 여부
- [ ] Docker 빌드 성공 여부 (`docker-compose build` 에러 없음)
- [ ] `sample_data`로 컨테이너 `POST /run` 호출 시 정상 응답 확인
- [ ] 이미지 여러 장 반환 시 `README.md`에 각 인덱스 의미 명시 여부
- [ ] `README.md` — 실행 예시(`sample_data` 경로 기준) 포함 여부
- [ ] 로컬에서 `main()` 함수 직접 호출하여 정상 동작 확인
- [ ] `meta.json` — `description` 필드에 질환명·입력 데이터 형식·출력 형태 모두 포함 여부 (LLM Wiki 검색 품질에 직접 영향)

---

## 현재 등록된 모델 (참고)

| 진료과 | 모델 폴더명 | 입력 | result_type | output_image_role |
|---|---|---|---|---|
| Neurology | BraTS2020_T1_UNet3D | NIfTI (.nii.gz) | `["segmentation_overlay", "3d_overlay"]` | `segmentation_overlay` |
| Neurology | BraTS2020_T1ce_UNet3D | NIfTI (.nii.gz) | `["segmentation_overlay", "3d_overlay"]` | `segmentation_overlay` |
| Neurology | BraTS2020_T2_UNet3D | NIfTI (.nii.gz) | `["segmentation_overlay", "3d_overlay"]` | `segmentation_overlay` |
| Neurology | BraTS2020_FLAIR_UNet3D | NIfTI (.nii.gz) | `["segmentation_overlay", "3d_overlay"]` | `segmentation_overlay` |
| Pulmonology | ChestXray14_Multilabel_Classification | PNG/JPG | `["gradcam_overlay", "classification_probabilities"]` | `gradcam_overlay` |
| Pulmonology | RSNA_Pneumonia_YOLO26x | DICOM (.dcm) | `["bbox_overlay", "detection_predictions"]` | `bbox_overlay` |
