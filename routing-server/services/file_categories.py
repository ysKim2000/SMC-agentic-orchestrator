"""
파일 카테고리 판별 + 입력 선택 (Track B / DAG 공용).

- file_category:       저장된 파일 경로 → 카테고리
- required_to_category: 모델 required_data 문자열 → 카테고리
- select_input_file:   required_data에 맞는 입력 파일 선택
"""
from __future__ import annotations

from pathlib import Path

# 입력 선택 우선순위 (구조화 데이터 → 영상)
CAT_PRIORITY = ["csv", "json", "dicom", "nifti", "image"]


def file_category(path: Path) -> str | None:
    """저장된 파일 경로 → 입력 카테고리."""
    name = path.name.lower()
    ext = path.suffix.lower()
    if ext in (".dcm", ".dicom"):
        return "dicom"
    if ext == ".csv":
        return "csv"
    if ext == ".json":
        return "json"
    if ext in (".png", ".jpg", ".jpeg"):
        return "image"
    if ext == ".nii" or name.endswith(".nii.gz"):
        return "nifti"
    return None


def required_to_category(val: str) -> str:
    """모델 required_data 문자열 → 카테고리. 미상은 영상(dicom)으로 간주."""
    v = str(val).lower().strip().strip('"[]\'')
    if v == "csv":
        return "csv"
    if v in ("png", "jpg", "jpeg", "image"):
        return "image"
    if v in ("nii", "nii.gz", "nifti", "gz"):
        return "nifti"
    if v in ("dcm", "dicom"):
        return "dicom"
    return "dicom"  # 모달·시퀀스 명칭(T2, STIR T2, MRI, x-ray 등) 및 기본값


def categorize(files: list[Path]) -> dict[str, list[Path]]:
    """파일 목록 → 카테고리별 그룹."""
    by_cat: dict[str, list[Path]] = {}
    for p in files:
        c = file_category(p)
        if c:
            by_cat.setdefault(c, []).append(p)
    return by_cat


def select_input_file(files: list[Path], required_data: list[str]) -> Path | None:
    """
    required_data에 맞는 입력 파일 1건 선택 (Track B 로직).

    required 카테고리를 우선 선택, 없거나 미매칭 시 업로드 우선순위로 폴백.
    """
    by_cat = categorize(files)
    required = {required_to_category(r) for r in (required_data or [])}
    for cat in CAT_PRIORITY:
        if cat in required and by_cat.get(cat):
            return by_cat[cat][0]
    for cat in CAT_PRIORITY:
        if by_cat.get(cat):
            return by_cat[cat][0]
    return None
