"""
모델 입력 포맷 정합 — 변환 레지스트리 (Level 1).

모델이 required_data로 선언한 목표 포맷과 업로드 원본이 다를 때,
(원본 카테고리 → 목표 카테고리) 변환기를 조회해 변환한다.
새 변환쌍은 CONVERTERS에 한 줄 추가로 확장.

전처리(채널·크기·정규화)는 각 모델 컨테이너가 담당한다. 여기선 포맷(확장자)만 맞춘다.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger("maple.convert")


def _render_first_slice_png(src: Path, out_dir: Path, handler) -> Path | None:
    """attachments 핸들러의 렌더(windowing 등)를 재사용해 첫 슬라이스를 PNG 파일로 저장."""
    na = handler._parse_sync(src.name, src.read_bytes())
    if not na.images:
        return None
    data_uri = na.images[0]  # 첫(axial) 슬라이스
    b64 = data_uri.split(",", 1)[1] if "," in data_uri else data_uri
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.stem}.png"
    out_path.write_bytes(base64.b64decode(b64))
    return out_path


def dicom_to_png(src: Path, out_dir: Path) -> Path | None:
    from services.attachments.handlers import DicomHandler
    return _render_first_slice_png(src, out_dir, DicomHandler())


def nifti_to_png(src: Path, out_dir: Path) -> Path | None:
    from services.attachments.handlers import NiftiHandler
    return _render_first_slice_png(src, out_dir, NiftiHandler())


# (원본 카테고리, 목표 카테고리) → 변환 함수(src, out_dir) -> Path|None
# 목표 카테고리는 file_categories.required_to_category 기준 ("image", "nifti", ...)
CONVERTERS: dict[tuple[str, str], Callable[[Path, Path], Path | None]] = {
    ("dicom", "image"): dicom_to_png,
    ("nifti", "image"): nifti_to_png,
    # 필요 시 확장: ("dicom", "nifti"): dicom_series_to_nifti, ...
}


def get_converter(src_cat: str, target_cat: str) -> Callable | None:
    return CONVERTERS.get((src_cat, target_cat))


def convert(src: Path, src_cat: str, target_cat: str, out_dir: Path) -> Path | None:
    """src_cat → target_cat 변환. 변환기 없거나 실패 시 None."""
    fn = CONVERTERS.get((src_cat, target_cat))
    if fn is None:
        return None
    try:
        return fn(src, out_dir)
    except Exception as e:
        logger.warning("포맷 변환 실패 (%s→%s, %s): %s", src_cat, target_cat, src, e)
        return None
