# llm/prompts/utils.py

import json
import re
from typing import Any


def format_value(value: Any) -> str:
    """
    Convert model output values into a readable prompt string.
    """
    if value in (None, "", [], {}):
        return "없음"

    if isinstance(value, str):
        return value

    return json.dumps(value, ensure_ascii=False)


def sort_image_roles(roles: list[str]) -> list[str]:
    """
    Sort image roles in a stable and human-readable order.

    Examples:
    - seg3d_1, seg3d_2, seg3d_10
    - bbox_overlay
    """
    def sort_key(role: str):
        match = re.fullmatch(r"(.+?)_(\d+)", role)
        if match:
            return match.group(1), int(match.group(2))
        return role, -1

    return sorted(roles, key=sort_key)


def to_pretty_json(data: Any) -> str:
    """
    Convert Python objects into pretty JSON for prompt context.
    """
    return json.dumps(data, ensure_ascii=False, indent=2)


def format_dicom_age(value: Any) -> str:
    """
    Convert a DICOM PatientAge string (e.g. "045Y", "006M") into a readable form.
    Passes through non-matching values (e.g. "90+") unchanged.
    """
    match = re.fullmatch(r"0*(\d+)\s*([YMWD])", str(value).strip(), re.IGNORECASE)
    if not match:
        return str(value)
    n = int(match.group(1))
    unit = match.group(2).upper()
    return {"Y": f"{n}세", "M": f"{n}개월", "W": f"{n}주", "D": f"{n}일"}[unit]


def format_metadata(meta: dict) -> str:
    """
    Flatten a metadata dict into a readable "key=value, ..." string for prompts.
    Age fields are humanized; nested dict/list values are JSON-encoded.
    """
    parts = []
    for key, value in meta.items():
        if key.lower() in ("age", "patient_age", "patientage"):
            value = format_dicom_age(value)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}={value}")
    return ", ".join(parts)