"""
첨부 정규화 레이어 — 기반 타입.

모든 모달리티(DICOM/NIfTI/이미지/CSV/PDF/…)를 VLM이 소비 가능한
공통 형태(images + text + metadata)로 환원하기 위한 추상 인터페이스.

설계: docs/input-normalization-design.md
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class NormalizedAttachment:
    """핸들러가 산출하는 정규화된 첨부 표현. attachments[] 계약의 단위."""

    type: str                                   # dicom | nifti | image | csv | pdf | ...
    filename: str
    images: list[str] = field(default_factory=list)   # VLM이 볼 것 (data URI). 없으면 []
    text: str = ""                              # VLM이 읽을 것 (전사/OCR/추출텍스트). 없으면 ""
    metadata: dict[str, Any] = field(default_factory=dict)  # 구조화·비식별화 메타
    tabular: list[dict] | None = None           # 표 데이터(dict 리스트). CSV 전용

    def to_dict(self) -> dict:
        return {
            "type":     self.type,
            "filename": self.filename,
            "images":   self.images,
            "text":     self.text,
            "metadata": self.metadata,
            "tabular":  self.tabular,
        }


class AttachmentHandler(ABC):
    """
    모달리티별 첨부 핸들러.

    새 타입 지원 = 이 클래스를 구현해 registry.REGISTRY에 등록.
    parse()는 async — 로컬 라이브러리 처리(자립형)와 외부 런타임 컨테이너
    위임(위임형, 예: 향후 ASR)을 동일 인터페이스로 수용한다.
    """

    type: str = "unknown"

    @abstractmethod
    def can_handle(self, filename: str, content_type: str = "") -> bool:
        """이 핸들러가 처리 가능한 파일인지 판별."""
        ...

    @abstractmethod
    async def parse(self, filename: str, content: bytes) -> NormalizedAttachment:
        """이미지 렌더 + 메타 추출 (general 모드 전체 처리)."""
        ...

    async def extract_metadata(self, filename: str, content: bytes) -> dict:
        """
        메타데이터만 추출 (이미지 렌더 없이). prediction interpret용.

        기본 구현은 parse()를 재사용하지만, 무거운 핸들러(DICOM/NIfTI)는
        오버라이드해 픽셀 렌더링을 건너뛴다.
        """
        result = await self.parse(filename, content)
        return result.metadata

    async def extract_metadata_path(self, filename: str, path: str) -> dict:
        """
        파일 경로에서 메타데이터만 추출.

        대용량 업로드는 본문 전체를 메모리에 복사하지 않도록 이 진입점을
        사용한다. 기본 구현은 하위 호환을 위해 기존 bytes API를 재사용한다.
        """
        return await self.extract_metadata(filename, Path(path).read_bytes())
