"""
핸들러 레지스트리 + 디스패치.

새 모달리티 = 핸들러를 REGISTRY에 추가하면 끝.
"""
from __future__ import annotations

from .base import AttachmentHandler
from .handlers import (
    CsvHandler,
    DicomHandler,
    ImageHandler,
    NiftiHandler,
    PdfHandler,
)

# 순서 = 우선순위. 특이 확장자(nifti/dicom/csv/pdf)를 이미지보다 먼저 검사.
REGISTRY: list[AttachmentHandler] = [
    DicomHandler(),
    NiftiHandler(),
    CsvHandler(),
    PdfHandler(),
    ImageHandler(),
    # 향후 확장:
    # AudioHandler(),   # ASR 전사 (위임형 런타임 컨테이너 유력)
    # VideoHandler(),   # 키프레임 + 오디오 ASR
]


def find_handler(filename: str, content_type: str = "") -> AttachmentHandler | None:
    for handler in REGISTRY:
        try:
            if handler.can_handle(filename or "", content_type or ""):
                return handler
        except Exception:
            continue
    return None
