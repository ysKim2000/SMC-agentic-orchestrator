# services/modality_service.py
"""첨부 파일에서 영상 모달리티를 직접 읽어낸다.

질의 텍스트만으로 모달리티를 추론하면 라우팅이 어긋난다. 실제로 "chest CT"라고
써도 임베딩 검색이 흉부 X선 모델을 더 높게 잡았다. 파일을 열면 확실히 알 수 있는
사실(차원, voxel spacing, HU 스케일)을 근거로 쓰는 편이 정확하다.

CT-RATE의 NIfTI는 descrip/xyzt_units가 비어 있어 헤더 텍스트에 의존할 수 없다.
대신 3D 여부와 HU 값 분포로 판별한다 — HU는 CT 고유 스케일이다.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("maple-ai-agent")

# CT-RATE 등 일부 데이터는 HU에서 RescaleIntercept(보통 +8192)를 빼지 않은 채 저장된다.
_HU_OFFSET = 8192.0
_HU_AIR_MIN, _HU_AIR_MAX = -1100.0, -800.0


def _probe_nifti(path: Path) -> dict | None:
    try:
        import nibabel as nib
        import numpy as np
    except ImportError:
        return None
    try:
        img = nib.load(str(path))
    except Exception as exc:
        logger.warning("[modality] NIfTI 로드 실패 %s: %s", path.name, exc)
        return None

    shape = tuple(int(x) for x in img.shape)
    if len(shape) < 3:
        return {"modality": "unknown", "dimensionality": "2D", "shape": shape}

    zooms = tuple(round(float(z), 3) for z in img.header.get_zooms()[:3])
    mid = shape[2] // 2
    try:
        plane = np.asarray(img.dataobj[:, :, mid], dtype=np.float32)
    except Exception:
        return {"modality": "unknown", "dimensionality": "3D", "shape": shape}
    if float(np.median(plane)) > 2000:
        plane = plane - _HU_OFFSET

    # 볼륨 바깥 패딩은 0으로 저장돼 오프셋 적용 후 -8192가 된다. 이를 빼고 봐야
    # 실제 공기(-1000 부근)가 하위 분위수로 잡힌다.
    body = plane[plane > _HU_OFFSET * -1 + 100]
    if body.size < 100:
        body = plane
    low = float(np.percentile(body, 1))
    high = float(np.percentile(body, 99.9))
    # CT는 공기(-1000)에서 뼈(+1000 이상)까지 걸치는 HU 스케일을 갖는다.
    is_hu = _HU_AIR_MIN <= low <= _HU_AIR_MAX and high > 200
    return {
        "modality": "CT" if is_hu else "MR",
        "dimensionality": "3D",
        "shape": shape,
        "slices": shape[2],
        "voxel_spacing_mm": zooms,
        "intensity_scale": "HU" if is_hu else "arbitrary",
    }


def detect(path_or_name: str | Path, declared_type: str = "") -> dict:
    """파일 하나의 모달리티 정보. 읽을 수 없으면 확장자로 추정한다."""
    path = Path(str(path_or_name))
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith((".nii", ".nii.gz")) and path.is_file():
        probed = _probe_nifti(path)
        if probed:
            return probed
    if suffixes.endswith((".nii", ".nii.gz")) or declared_type.lower() in ("nifti", "nii"):
        return {"modality": "CT", "dimensionality": "3D", "intensity_scale": "unknown"}
    if declared_type.lower() == "dicom" or suffixes.endswith(".dcm"):
        return {"modality": "unknown", "dimensionality": "unknown"}
    if suffixes.endswith((".jpg", ".jpeg", ".png")):
        return {"modality": "XR", "dimensionality": "2D"}
    return {"modality": "unknown", "dimensionality": "unknown"}


def from_attachments(attachments: list[dict]) -> dict:
    """첨부 목록 전체에서 대표 모달리티를 뽑는다.

    라우팅 서버가 metadata에 원본 경로를 넣어주면 그것을 열어 확인하고,
    없으면 filename/type으로 추정한다.
    """
    for att in attachments or []:
        meta = att.get("metadata") or {}
        declared = str(meta.get("modality") or "").strip()
        source = meta.get("source_path") or meta.get("path") or att.get("filename") or ""
        info = detect(source, att.get("type", "")) if source else {}
        if declared:
            info = {**info, "modality": declared.upper()}
        if info.get("modality") and info["modality"] != "unknown":
            return info
    return {}


def search_hint(info: dict) -> str:
    """검색 질의 앞에 붙일 짧은 모달리티 단서.

    임베딩은 문서 전체를 평균하므로 짧고 집중된 표현이 강하게 작용한다.
    실측에서 'chest CT' 두 단어가 긴 설명문보다 유사도가 높았다.
    """
    modality = info.get("modality")
    if modality == "CT":
        return "chest CT 3D volume"
    if modality == "MR":
        return "MRI volume"
    if modality == "XR":
        return "chest X-ray radiograph"
    return ""


def context_line(info: dict) -> str:
    """plan 프롬프트에 넣을 사람이 읽는 한 줄."""
    if not info or not info.get("modality") or info["modality"] == "unknown":
        return ""
    parts = [f"modality: {info['modality']}"]
    if info.get("dimensionality"):
        parts.append(info["dimensionality"])
    if info.get("slices"):
        parts.append(f"{info['slices']} slices")
    if info.get("voxel_spacing_mm"):
        z = info["voxel_spacing_mm"]
        parts.append(f"voxel {z[0]}x{z[1]}x{z[2]} mm")
    if info.get("intensity_scale") == "HU":
        parts.append("Hounsfield units")
    return ", ".join(parts)
