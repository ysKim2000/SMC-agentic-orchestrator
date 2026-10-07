import json
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from services import wiki_service
from rag import embedder

router = APIRouter()


class ModelRegisterRequest(BaseModel):
    id: str
    model_name: str
    department: str
    project: str
    description: str
    task_type: str
    disease: str
    required_data: list[str]
    result_type: list[str]     # 출력 타입 목록 (예: ["bbox_overlay", "detection_predictions"])
    provides: list[str] = []   # 이 모델이 산출하는 상위 출력 태그 (예: ["sij_roi"])
    requires: list[str] = []   # 선행으로 필요한 상위 출력 태그 (예: ["sij_roi"])
    metrics: dict = {}         # 성능 지표 (auroc/auroc_macro/dice_mean/source 등). 없으면 {}
    # 입력 데이터 식별용 구조화 필드. 모델 선택 시 VLM이 판별한 입력(모달리티·부위)과
    # 맞춰 후보를 거른다. 자유 설명문에서 부위를 찾으면 모델마다 표기가 달라 누락된다.
    modality: str = ""         # 예: "X-ray", "CT", "MRI", "ECG" (scripts/registry_spec.MODALITIES)
    body_region: list[str] = []  # 예: ["chest"], ["knee"], ["lumbar spine"]
    view: str = ""             # 예: "frontal or lateral radiograph", "sagittal T2"

    @field_validator("result_type", mode="before")
    @classmethod
    def _result_type_to_list(cls, value):
        # 하위호환: 문자열/None으로 와도 리스트로 정규화
        if value is None:
            return []
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value



def _metrics_line(metrics: dict) -> str:
    """지표를 doc_text 한 줄로 만든다 — 모델 선택 LLM이 후보 info에서 그대로 읽는다."""
    if not metrics:
        return ""
    parts = []
    per_label = metrics.get("auroc") or {}
    if per_label:
        parts.append("AUROC " + ", ".join(f"{k} {v}" for k, v in per_label.items()))
    if metrics.get("dice_mean") is not None:
        parts.append(f"Dice {metrics['dice_mean']}")
    if metrics.get("n_eval"):
        parts.append(f"n={metrics['n_eval']}")
    if metrics.get("source"):
        parts.append(f"출처 {metrics['source']}")
    return "\nperformance: " + " | ".join(parts) if parts else ""


def _metrics_metadata(metrics: dict) -> dict:
    """ChromaDB 메타데이터는 스칼라만 허용 — dict는 JSON 문자열로 평탄화."""
    if not metrics:
        return {}
    out = {}
    for key in ("auroc_macro", "auprc_macro", "f1_at_0_5", "dice_mean", "n_eval"):
        if isinstance(metrics.get(key), (int, float)):
            out[key] = metrics[key]
    if metrics.get("auroc"):
        out["auroc_per_label"] = json.dumps(metrics["auroc"], ensure_ascii=False)
    if metrics.get("source"):
        out["metrics_source"] = metrics["source"]
    return out


@router.post("/register")
async def register_model(req: ModelRegisterRequest):
    # 1. Wiki 모델 페이지 생성/업데이트
    wiki_service.write_model_page(
        model_name=req.model_name,
        department=req.department,
        project=req.project,
        description=req.description,
        task_type=req.task_type,
        disease=req.disease,
        required_data=req.required_data,
        result_type=req.result_type,
        provides=req.provides,
        requires=req.requires,
    )

    # 2. 진료과 페이지 업데이트
    wiki_service.update_department_page(req.department, req.model_name, req.project)

    # 3. index.md 업데이트
    wiki_service.update_index(req.model_name, req.department, req.project)

    # 4. log.md 이력 추가
    wiki_service.append_log("ingest", f"{req.model_name} 모델 등록")

    # 5. ChromaDB maple_models 컬렉션에 등록
    doc_text = (
        f"모델명: {req.model_name}\n"
        f"모달리티: {req.modality}\n"
        f"부위: {', '.join(req.body_region)}\n"
        f"진료과: {req.department}\n"
        f"프로젝트: {req.project}\n"
        f"설명: {req.description}\n"
        f"질환: {req.disease}\n"
        f"task_type: {req.task_type}\n"
        f"required_data: {', '.join(req.required_data)}\n"
        f"result_type: {', '.join(req.result_type)}"
        + _metrics_line(req.metrics)
    )
    embedder.upsert_model(
        model_id=req.id,
        text=doc_text,
        metadata={
            "model_name": req.model_name,
            "department": req.department,
            "project": req.project,
            "task_type": req.task_type,
            "required_data": ", ".join(req.required_data),
            "result_type": ", ".join(req.result_type),
            "provides": ", ".join(req.provides),
            "modality": req.modality,
            "body_region": ", ".join(req.body_region),
            "view": req.view,
            "requires": ", ".join(req.requires),
            **_metrics_metadata(req.metrics),
        },
    )

    return {
        "status": "registered",
        "model_name": req.model_name,
        "wiki_page": f"wiki/models/{req.project}/{req.model_name}.md",
        "department_page": f"wiki/departments/{req.department}.md",
    }


@router.delete("/{model_id}")
async def delete_model(model_id: str):
    col = embedder._get_collection("maple_models")
    result = col.get(ids=[model_id], include=["metadatas"])
    if not result["ids"]:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in registry")

    meta = result["metadatas"][0]
    model_name = meta.get("model_name", "")
    project = meta.get("project", "")

    wiki_service.delete_model_page(project, model_name)
    wiki_service.remove_model_from_index(project, model_name)
    wiki_service.append_log("delete", f"{project}/{model_name} 모델 삭제")

    embedder.delete_model(model_id)

    return {"status": "deleted", "model_id": model_id, "model_name": model_name, "project": project}


@router.get("/lookup")
async def lookup_model(model_name: str, project: str = ""):
    if not project:
        resolved = wiki_service.resolve_model(model_name)
        if not resolved:
            raise HTTPException(status_code=404, detail=f"Model {model_name} not found in wiki")
        project, model_name = resolved

    content = wiki_service.read_model_page(project, model_name)
    if not content:
        raise HTTPException(status_code=404, detail=f"Model {project}/{model_name} not found in wiki")

    department = ""
    for line in content.splitlines():
        if line.startswith("- **진료과:**"):
            department = re.sub(r'\*+', '', line.split(":", 1)[1]).strip()
            break

    return {"model_name": model_name, "project": project, "department": department}
