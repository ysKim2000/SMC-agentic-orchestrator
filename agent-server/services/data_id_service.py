"""입력 데이터 식별 — VLM이 첨부 영상을 보고 "어떤 데이터인지"만 판별한다.

소견은 묻지 않는다. 소견 판단은 특화 모델의 몫이고, VLM이 소견을 놓치면 그 모델을
고르지 않게 되어 선택 품질이 VLM 판독 실력에 묶인다(실측: CT 커버율 75% → 38%).
대신 모달리티·부위·촬영면처럼 VLM이 거의 틀리지 않는 정보만 받아, 레지스트리의
구조화 필드(modality, body_region)와 맞는 모델로 후보를 좁힌다.

실측(8개 데이터셋 각 1건): "frontal knee X-ray", "PA wrist X-ray",
"sagittal T2-weighted lumbar spine MRI", "axial chest CT" 등 모두 정확했다.
"""
import logging
import os
import re

from llm import client as llm_client

logger = logging.getLogger("maple-ai-agent")

_PROMPT = (
    "Identify what kind of medical data this is. State only: the modality (e.g. X-ray, CT, MRI, "
    "ECG, fundus photograph, dermoscopy, endoscopy, histopathology, mammography, ultrasound), "
    "the body region, and the view, sequence or plane if visible. Do NOT describe any findings "
    "or abnormalities. Answer in one short line, e.g. 'frontal chest X-ray' or "
    "'sagittal T2-weighted lumbar spine MRI'."
)

# VLM 문장 → 레지스트리 어휘(scripts/registry_spec.py). 순서대로 첫 일치만 쓴다.
_MODALITY = [
    (r"\bmammogra", "mammography"),
    (r"\bultraso|sonogra", "ultrasound"),
    (r"\bfundus|retina", "fundus photograph"),
    (r"slit.?lamp", "slit-lamp photograph"),
    (r"dermoscop", "dermoscopy"),
    (r"endoscop|colonoscop|gastroscop", "endoscopy"),
    (r"histopath|h&e|histolog", "histopathology"),
    (r"cytolog|pap smear", "cytology"),
    (r"\bmri\b|magnetic resonance|t1-weighted|t2-weighted|flair", "MRI"),
    (r"\bct\b|computed tomograph", "CT"),
    (r"\becg\b|electrocardiogra", "ECG"),
    (r"x-ray|xray|radiograph", "X-ray"),
    (r"photograph|clinical image", "clinical photograph"),
]
_REGION = [
    (r"lumbar", "lumbar spine"), (r"\bspine|scolio|vertebra", "whole spine"),
    (r"\bknee", "knee"), (r"\bwrist", "wrist"), (r"\bhand\b|finger", "hand"),
    (r"forearm|elbow|humer|arm\b", "upper limb"), (r"\bleg\b|femur|tibia|ankle|foot", "lower limb"),
    (r"\bhip\b|pelvi", "hip"), (r"shoulder", "shoulder"),
    (r"chest|thora|lung", "chest"), (r"brain|head|cranial", "brain"),
    (r"breast", "breast"), (r"colon|rectum|cecum", "colon"),
    (r"esophag|stomach|duoden", "upper GI tract"), (r"cervi(x|cal smear)", "cervix"),
    (r"\beye|retina|fundus|lens", "eye"), (r"skin|derm", "skin"), (r"\bheart|cardiac", "heart"),
]
# 부위가 포함 관계인 경우: 요추 영상에는 전척추 모델도 쓸 수 있다.
_REGION_ALSO = {"lumbar spine": {"whole spine"}, "wrist": {"hand", "upper limb"},
                "hand": {"wrist"}, "knee": {"lower limb"}, "hip": {"lower limb"},
                "shoulder": {"upper limb"}}


def enabled() -> bool:
    return os.getenv("DATA_ID", "off").strip().lower() == "vlm"


def parse(text: str) -> dict:
    t = (text or "").lower()
    modality = next((m for p, m in _MODALITY if re.search(p, t)), "")
    regions: set[str] = set()
    for p, r in _REGION:
        if re.search(p, t):
            regions.add(r)
    for r in list(regions):
        regions |= _REGION_ALSO.get(r, set())
    return {"text": text, "modality": modality, "regions": sorted(regions)}


async def identify(images: list[str]) -> dict:
    """첫 영상 한 장으로 판별. 영상이 없거나 실패하면 빈 결과(필터 미적용)."""
    if not images:
        return {"text": "", "modality": "", "regions": []}
    try:
        text = await llm_client.generate_with_images(_PROMPT, images[:1],
                                                     system="Answer in one short line.")
    except Exception:
        logger.exception("[data_id] VLM identification failed")
        return {"text": "", "modality": "", "regions": []}
    text = " ".join((text or "").split())[:160]
    out = parse(text)
    logger.info("[data_id] %r -> modality=%s regions=%s", text, out["modality"], out["regions"])
    return out


def matches(meta: dict, ident: dict) -> bool:
    """모델 메타가 식별 결과와 맞는가. 식별값이 비면 그 축은 통과시킨다."""
    mod = ident.get("modality")
    if mod and (meta.get("modality") or "") and meta.get("modality") != mod:
        return False
    regs = set(ident.get("regions") or [])
    model_regs = {r.strip() for r in (meta.get("body_region") or "").split(",") if r.strip()}
    if regs and model_regs and not (regs & model_regs) and "whole body" not in model_regs:
        return False
    return True
