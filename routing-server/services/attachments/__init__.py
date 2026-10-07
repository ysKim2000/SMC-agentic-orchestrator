"""
첨부 정규화 레이어 — 공개 API.

모든 모달리티를 VLM 소비 형태(images + text + metadata)로 환원한다.
설계: docs/input-normalization-design.md

  normalize_attachment  — 파일 1건 → NormalizedAttachment (렌더+메타)
  extract_metadata      — 파일 1건 → 메타데이터만 (렌더 없이, interpret용)
  extract_metadata_path — 대용량 파일 경로 → 메타데이터만
  apply_image_budget    — 요청 단위 이미지 토큰 예산 적용 (초과시 강등/캡)
"""
from __future__ import annotations

import logging

from .base import AttachmentHandler, NormalizedAttachment
from .registry import REGISTRY, find_handler

logger = logging.getLogger("maple.attachments")

__all__ = [
    "AttachmentHandler",
    "NormalizedAttachment",
    "REGISTRY",
    "find_handler",
    "normalize_attachment",
    "extract_metadata",
    "extract_metadata_path",
    "apply_image_budget",
]


async def normalize_attachment(
    filename: str, content: bytes, content_type: str = ""
) -> NormalizedAttachment | None:
    """파일 1건을 정규화. 지원 핸들러가 없거나 처리 실패 시 None."""
    handler = find_handler(filename, content_type)
    if handler is None:
        logger.warning("지원하지 않는 첨부 타입: %s (content_type=%s)", filename, content_type)
        return None
    try:
        return await handler.parse(filename, content)
    except Exception as e:
        logger.warning("첨부 정규화 실패 (%s): %s", filename, e)
        return None


async def extract_metadata(filename: str, content: bytes, content_type: str = "") -> dict:
    """파일 1건에서 메타데이터만 추출 (이미지 렌더 없이). prediction interpret용."""
    handler = find_handler(filename, content_type)
    if handler is None:
        return {}
    try:
        return await handler.extract_metadata(filename, content)
    except Exception as e:
        logger.warning("메타 추출 실패 (%s): %s", filename, e)
        return {}


async def extract_metadata_path(
    filename: str, path: str, content_type: str = ""
) -> dict:
    """대용량 파일을 메모리에 복사하지 않고 경로에서 메타데이터만 추출."""
    handler = find_handler(filename, content_type)
    if handler is None:
        return {}
    try:
        return await handler.extract_metadata_path(filename, path)
    except Exception as e:
        logger.warning("메타 추출 실패 (%s): %s", filename, e)
        return {}


def apply_image_budget(attachments: list[NormalizedAttachment], max_images: int) -> int:
    """
    요청 단위 이미지 예산 적용 (in-place). 반환: 드롭된 이미지 수.

    정책 (§4 이미지 토큰 예산):
      1) 총 이미지 <= 예산 → 그대로 (1~2 파일은 3면 전부 전송)
      2) 초과 → 다면(3D) 첨부를 첫 슬라이스(axial)로 강등
      3) 여전히 초과 → 예산까지 하드 캡. 메타데이터는 항상 유지 + 드롭 수 로깅.
    """
    if max_images <= 0:
        return 0

    total = sum(len(a.images) for a in attachments)
    if total <= max_images:
        return 0

    # 2) 3D(다면) 첨부를 axial 1장으로 강등
    for a in attachments:
        if len(a.images) > 1:
            a.images = a.images[:1]

    total = sum(len(a.images) for a in attachments)
    if total <= max_images:
        logger.info("이미지 예산: 3D 첨부를 axial 단면으로 강등 (total=%d/%d)", total, max_images)
        return 0

    # 3) 하드 캡
    dropped = 0
    budget = max_images
    for a in attachments:
        if budget <= 0:
            dropped += len(a.images)
            a.images = []
        elif len(a.images) > budget:
            dropped += len(a.images) - budget
            a.images = a.images[:budget]
            budget = 0
        else:
            budget -= len(a.images)
    logger.warning(
        "이미지 예산 초과: %d장 드롭 (cap=%d). 메타데이터는 유지됨.", dropped, max_images
    )
    return dropped
