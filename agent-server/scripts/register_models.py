"""모델 초기 등록 스크립트 — agent-server 단독 부트스트랩용.

ChromaDB `maple_models`가 비어있을 때 한 번 실행하면 아래 MODELS를 agent의
`/agent/models/register`로 등록한다 (ChromaDB + wiki 생성). **MongoDB·타 레포 불필요.**

- 운영 등록의 원천(source of truth)은 maple-model-execution-server의 `AI_Models/*/meta.json`이며,
  실제 배포 등록은 maple-routing-server의 `scan_and_register.py`가 담당한다.
- 이 스크립트는 그 meta.json 값을 미러링한 **개발/협업용 편의 스크립트**다. 모델이 바뀌면 갱신 필요.

사용:
    # agent 서버(+ChromaDB)가 떠 있는 상태에서
    python scripts/register_models.py
    # 다른 주소면: AGENT_URL=http://host:8101 python scripts/register_models.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx

BASE_URL = os.getenv("AGENT_URL", "http://localhost:8101")

# maple-model-execution-server AI_Models/*/meta.json 미러 (department = 폴더 기준)
MODELS = [
    {
        "id": "neurology-brats2020-flair-unet3d",
        "model_name": "BraTS2020_FLAIR_UNet3D",
        "department": "Neurology",
        "project": "BraTS2020_FLAIR_UNet3D",
        "description": "Brain tumor segmentation model. Takes a FLAIR (Fluid-Attenuated Inversion Recovery) NIfTI brain MRI and returns axial-slice overlay images segmenting three sub-regions: Whole Tumor (WT), Tumor Core (TC), and Enhancing Tumor (ET). For glioma / glioblastoma (BraTS).",
        "task_type": "segmentation",
        "disease": "brain tumor, glioma, glioblastoma, BraTS, whole tumor, tumor core, enhancing tumor",
        "required_data": ["nii.gz", "nii"],
        "result_type": ["segmentation_overlay", "3d_overlay"],
        "provides": [],
        "requires": [],
    },
    {
        "id": "neurology-brats2020-t1-unet3d",
        "model_name": "BraTS2020_T1_UNet3D",
        "department": "Neurology",
        "project": "BraTS2020_T1_UNet3D",
        "description": "Brain tumor segmentation model. Takes a T1-weighted NIfTI brain MRI and returns axial-slice overlay images segmenting three sub-regions: Whole Tumor (WT), Tumor Core (TC), and Enhancing Tumor (ET). For glioma / glioblastoma (BraTS).",
        "task_type": "segmentation",
        "disease": "brain tumor, glioma, glioblastoma, BraTS, whole tumor, tumor core, enhancing tumor",
        "required_data": ["nii.gz", "nii"],
        "result_type": ["segmentation_overlay", "3d_overlay"],
        "provides": [],
        "requires": [],
    },
    {
        "id": "neurology-brats2020-t1ce-unet3d",
        "model_name": "BraTS2020_T1ce_UNet3D",
        "department": "Neurology",
        "project": "BraTS2020_T1ce_UNet3D",
        "description": "Brain tumor segmentation model. Takes a T1 contrast-enhanced (T1ce) NIfTI brain MRI and returns axial-slice overlay images segmenting three sub-regions: Whole Tumor (WT), Tumor Core (TC), and Enhancing Tumor (ET). For glioma / glioblastoma (BraTS).",
        "task_type": "segmentation",
        "disease": "brain tumor, glioma, glioblastoma, BraTS, whole tumor, tumor core, enhancing tumor",
        "required_data": ["nii.gz", "nii"],
        "result_type": ["segmentation_overlay", "3d_overlay"],
        "provides": [],
        "requires": [],
    },
    {
        "id": "neurology-brats2020-t2-unet3d",
        "model_name": "BraTS2020_T2_UNet3D",
        "department": "Neurology",
        "project": "BraTS2020_T2_UNet3D",
        "description": "Brain tumor segmentation model. Takes a T2-weighted NIfTI brain MRI and returns axial-slice overlay images segmenting three sub-regions: Whole Tumor (WT), Tumor Core (TC), and Enhancing Tumor (ET). For glioma / glioblastoma (BraTS).",
        "task_type": "segmentation",
        "disease": "brain tumor, glioma, glioblastoma, BraTS, whole tumor, tumor core, enhancing tumor",
        "required_data": ["nii.gz", "nii"],
        "result_type": ["segmentation_overlay", "3d_overlay"],
        "provides": [],
        "requires": [],
    },
    {
        "id": "pulmonology-chestxray14-multilabel-classification",
        "model_name": "ChestXray14_Multilabel_Classification",
        "department": "Pulmonology",
        "project": "ChestXray14_Multilabel_Classification",
        "description": "Chest X-ray multi-label classification model (TorchXRayVision resnet50-res512-all). Analyzes a PNG/JPG chest X-ray and returns a Grad-CAM overlay plus per-finding probabilities for 14 thoracic findings: atelectasis, cardiomegaly, effusion, infiltration, mass, nodule, pneumonia, pneumothorax, consolidation, edema, emphysema, fibrosis, pleural thickening, and hernia.",
        "task_type": "classification",
        "disease": "chest x-ray, thoracic abnormality, atelectasis, cardiomegaly, effusion, infiltration, mass, nodule, pneumonia, pneumothorax, consolidation, edema, emphysema, fibrosis, pleural thickening, hernia",
        "required_data": ["png", "jpg", "jpeg"],
        "result_type": ["gradcam_overlay", "classification_probabilities"],
        "provides": [],
        "requires": [],
    },
    {
        "id": "pulmonology-rsna-pneumonia-yolo26x",
        "model_name": "YOLO26x_RSNA_Pneumonia",
        "department": "Pulmonology",
        "project": "RSNA_Pneumonia_YOLO26x",
        "description": "Pneumonia detection model on chest X-ray. Takes a DICOM chest X-ray and detects pneumonia-related lung opacity regions, returning bounding-box overlay images. For pneumonia / lung opacity screening on chest radiographs.",
        "task_type": "bbox detection",
        "disease": "pneumonia, lung opacity, chest X-ray",
        "required_data": ["dcm"],
        "result_type": ["bbox_overlay", "detection_predictions"],
        "provides": [],
        "requires": [],
    },
]


def main():
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        for m in MODELS:
            res = client.post("/agent/models/register", json=m)
            if res.status_code == 200:
                print(f"✓ {m['model_name']} 등록 완료")
            else:
                print(f"✗ {m['model_name']} 실패: {res.status_code} {res.text}")


if __name__ == "__main__":
    main()
