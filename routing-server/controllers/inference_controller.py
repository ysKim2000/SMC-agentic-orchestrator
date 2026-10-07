# inference_controller.py

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from services.inference_service import InferenceService
from services.pipeline_service import PipelineService
from services.agent_service import AgentService
from dependencies import (
    get_agent_service,
    get_inference_service,
    get_pipeline_service,
    require_doctor,
)
from config.database import get_database

logger = logging.getLogger("maple.inference.controller")

router = APIRouter(tags=["inference"])

SAVE_BASE_INPUT_DIR  = "./data/input"
SAVE_BASE_OUTPUT_DIR = "./data/output"
LOG_TEXT_PREVIEW_LIMIT = 200

# prediction 모드에서 파이프라인이 필요한 프로젝트 목록
# key: (department, project) → steps 목록
PIPELINE_REQUIRED: dict[tuple, list[dict]] = {
    # 다단계 파이프라인이 필요한 프로젝트는 여기에 등록
    # 예시:
    # ("Neurology", "Multi-Modal Segmentation"): [
    #     {"step": 1, "department": "Neurology", "project": "BraTS2020 T1 UNet3D"},
    #     {"step": 2, "department": "Neurology", "project": "BraTS2020 T1ce UNet3D"},
    # ],
}


def _to_container_path(host_path: Path) -> str:
    """호스트 ./data/... 경로 → 컨테이너 /data/... 경로로 변환."""
    try:
        rel = host_path.resolve().relative_to(Path("./data").resolve())
        return "/data/" + str(rel).replace("\\", "/")
    except ValueError:
        return str(host_path.resolve()).replace("\\", "/")


def _preview_text(value: str, limit: int = LOG_TEXT_PREVIEW_LIMIT) -> str:
    value = (value or "").replace("\r", "\\r").replace("\n", "\\n")
    if len(value) <= limit:
        return value
    return f"{value[:limit]}...({len(value)} chars)"


def _summarize_uploaded_files(file_dict: Dict[str, list]) -> list[dict]:
    summary = []
    for field, files in file_dict.items():
        for file in files:
            summary.append({
                "field": field,
                "filename": file.filename,
                "content_type": getattr(file, "content_type", None),
                "size": getattr(file, "size", None),
            })
    return summary


def _summarize_agent_plan_body(body: dict) -> dict:
    return {
        "mode": body.get("mode", "auto"),
        "department": body.get("department"),
        "project": body.get("project"),
        "query": _preview_text(body.get("query", "")),
        "uploaded_types": body.get("uploaded_types"),
        "images_count": len(body.get("images") or []),
        "csv_rows": len(body.get("csv_data") or []),
        "attachments_count": len(body.get("attachments") or []),
    }


def _normalize_fixture_question(content: str) -> str:
    return " ".join(content.split()).rstrip(" .?!。？！")


async def _legacy_clinical_fixture(query: str, doctor: dict) -> dict | None:
    db = get_database()
    fixtures = await db["chat_response_fixtures"].find({
        "hospital_id": doctor["hospital_id"],
        "doctor_employee_id": doctor["employee_id"],
        "normalized_questions": _normalize_fixture_question(query),
        "legacy_inference_enabled": True,
        "active": True,
    }).sort("step", 1).to_list(None)
    for fixture in fixtures:
        step = int(fixture.get("step", 1))
        state_id = (
            f"{doctor['_id']}:{fixture['fixture_key']}:"
            f"{fixture.get('patient_id', '')}"
        )
        if step == 1:
            await db["chat_response_fixture_states"].update_one(
                {"_id": state_id},
                {"$set": {
                    "fixture_key": fixture["fixture_key"],
                    "patient_id": fixture.get("patient_id"),
                    "doctor_id": doctor["_id"],
                    "last_step": 1,
                    "updated_at": datetime.now().astimezone(),
                }},
                upsert=True,
            )
            return fixture
        state = await db["chat_response_fixture_states"].find_one({
            "_id": state_id,
            "last_step": step - 1,
        })
        if state:
            await db["chat_response_fixture_states"].update_one(
                {"_id": state_id, "last_step": step - 1},
                {"$set": {
                    "last_step": step,
                    "updated_at": datetime.now().astimezone(),
                }},
            )
            return fixture
    return None


@router.post("/agent/plan")
async def proxy_agent_plan(
    request: Request,
    agent_service: AgentService = Depends(get_agent_service),
):
    """프론트엔드 → 백엔드 → Agent 프록시 (브라우저는 Agent에 직접 접근 불가)"""
    body = await request.json()
    logger.info("[Client] Agent plan 입력: %s", _summarize_agent_plan_body(body))
    if body.get("uploaded_types") and not body.get("images") and not body.get("csv_data"):
        logger.info("[Client] Agent plan은 파일 본문 없이 업로드 타입만 전달됨")
    result = await agent_service.plan(
        mode           = body.get("mode", "auto"),
        query          = body.get("query", ""),
        department     = body.get("department"),
        project        = body.get("project"),
        uploaded_types = body.get("uploaded_types"),
        images         = body.get("images"),
        csv_data       = body.get("csv_data"),
    )
    return JSONResponse(result)


@router.get("/agent/models/lookup")
async def proxy_agent_lookup(
    model_name: str,
    agent_service: AgentService = Depends(get_agent_service),
):
    """프론트엔드 → 백엔드 → Agent 모델 조회 프록시"""
    result = await agent_service.lookup_model(model_name)
    return JSONResponse(result)


@router.post("/")
async def inference_endpoint(
    request: Request,
    mode:       str = Form(default="auto"),
    query:      str = Form(default=""),
    department: str = Form(default=""),
    project:    str = Form(default=""),
    inference_service: InferenceService = Depends(get_inference_service),
    pipeline_service:  PipelineService  = Depends(get_pipeline_service),
    agent_service:     AgentService     = Depends(get_agent_service),
    doctor:             dict            = Depends(require_doctor),
):
    """
    모드별 추론 실행 엔드포인트.

    Form 파라미터:
      - mode:       "auto" | "clinical" | "prediction" | "general"  (기본: "auto")
      - query:      사용자 자연어 요청
      - department: prediction 모드 전용
      - project:    prediction 모드 전용
      - file:       DICOM / NIfTI / CSV (선택)
    """
    form = await request.form()
    file_dict: Dict[str, list] = defaultdict(list)

    for key, value in form.multi_items():
        if isinstance(value, StarletteUploadFile):
            file_dict[key].append(value)

    logger.info(
        "[Client] 추론 입력: mode=%s | department=%s | project=%s | query=%s | files=%s",
        mode,
        department,
        project,
        _preview_text(query),
        _summarize_uploaded_files(file_dict),
    )

    # ── clinical 모드: 파일 없이 Agent RAG + LLM 즉시 답변 ──────────────────
    if mode == "clinical":
        fixture = await _legacy_clinical_fixture(query, doctor)
        if fixture:
            delay_seconds = max(
                0.0,
                min(float(fixture.get("response_delay_seconds", 0)), 10.0),
            )
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            logger.info(
                "[Client] 임상 테스트 응답 사용: fixture=%s step=%s doctor=%s",
                fixture["fixture_key"],
                fixture["step"],
                doctor["employee_id"],
            )
            return JSONResponse({
                "status": "success",
                "mode": "clinical",
                "message": fixture["response"],
                "sources": [],
            })
        agent_resp = await agent_service.plan(mode="clinical", query=query)
        return JSONResponse({
            "status":  agent_resp.get("status", "success"),
            "mode":    "clinical",
            "message": agent_resp.get("message") or agent_resp.get("answer", ""),
            "sources": agent_resp.get("sources", []),
        })

    # ── general 모드: 에이전틱 파이프라인 (plan→DAG 실행→집계→interpret) ─────
    if mode == "general":
        uploaded_files = [f for files in file_dict.values() for f in files]

        # 1) 원본 저장 (모델 실행에 경로 필요) + attachments 정규화
        timestamp       = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_input_dir  = Path(SAVE_BASE_INPUT_DIR)  / "general" / timestamp
        save_output_dir = Path(SAVE_BASE_OUTPUT_DIR) / "general" / timestamp
        save_input_dir.mkdir(parents=True, exist_ok=True)
        await _save_uploaded_files(file_dict, save_input_dir)

        attachments    = await inference_service.build_attachments_from_dir(save_input_dir)
        uploaded_types = _detect_uploaded_types(uploaded_files)

        # 2) Agent plan — 모델 발견 + 실행계획(execution_plan.steps[]) 또는 VLM fallback
        agent_resp = await agent_service.plan(
            mode="general",
            query=query,
            attachments=attachments,
            uploaded_types=uploaded_types,
        )
        plan  = agent_resp.get("execution_plan") or {}
        steps = plan.get("steps") or []

        # 3) fallback: 모델 없음 → VLM 답변 그대로 통과 (실행/interpret 없음)
        if agent_resp.get("fallback_vlm_only") or not steps:
            logger.info("[Agent] general fallback (VLM 단독) — 실행계획 없음")
            return JSONResponse({
                "status":  agent_resp.get("status", "success"),
                "mode":    "general",
                "message": agent_resp.get("message") or agent_resp.get("answer", ""),
                "sources": agent_resp.get("sources", []),
            })

        # 4) DAG 실행 (병렬+순차)
        logger.info("[Agent] general 실행계획 수신 — steps=%d", len(steps))
        dag_result = await pipeline_service.run_dag(
            steps=steps,
            save_input_dir=save_input_dir,
            save_output_dir=save_output_dir,
        )
        if dag_result.get("status") == "error":
            return JSONResponse(dag_result)

        step_results = dag_result.get("step_results", [])

        # 5) 집계 — 프론트 이미지 + predictions
        front_images: list[str] = []
        for s in step_results:
            for img in s.get("images") or []:
                if img.get("data"):
                    front_images.append(img["data"])
        predictions = next(
            (s.get("predictions") for s in reversed(step_results) if s.get("predictions")),
            None,
        )

        # 6) interpret — 집계 결과 + 원본 스캔 메타 (원본 이미지 채널은 #7 후속)
        interpret_steps = [
            {
                "step":         s.get("step_id"),
                "model":        s.get("model"),
                "result_type":  s.get("result_type"),
                "predictions":  _normalize_predictions(s.get("predictions")),
                "model_output": s.get("model_output"),
                "images":       s.get("images"),
            }
            for s in step_results
        ]
        interpret_result = await agent_service.interpret(
            query=query,
            step_results=interpret_steps,
            execution_context={
                "mode": "general",
                "plan": plan,
                "attachments": attachments,   # 원본 스캔 이미지 + 메타 (§1에서 이미 빌드)
            },
        )

        front_step_results = [
            {
                "step_id":     s.get("step_id"),
                "model":       s.get("model"),
                "result_type": s.get("result_type"),
                "predictions": s.get("predictions"),
            }
            for s in step_results
        ]

        return JSONResponse({
            "status":                "success" if dag_result.get("status") != "partial" else "partial",
            "mode":                  "general",
            "result_type":           "pipeline",
            "images":                front_images,
            "predictions":           predictions,
            "step_results":          front_step_results,
            "errors":                dag_result.get("errors", []),
            "interpretation":        interpret_result.get("interpretation", ""),
            "interpretation_images": interpret_result.get("images", {}),
        })

    # ── auto 모드: Agent가 파일 타입을 보고 prediction / clinical 판단 ───────
    if mode == "auto":
        uploaded_files = [f for files in file_dict.values() for f in files]
        uploaded_types = _detect_uploaded_types(uploaded_files)
        logger.info("[Client] auto 업로드 타입 감지: %s", uploaded_types)

        agent_resp = await agent_service.plan(
            mode="auto",
            query=query,
            uploaded_types=uploaded_types,
        )

        # Agent가 prediction으로 판단한 경우 — 아래 prediction 흐름으로 전환
        decided_mode = agent_resp.get("mode") or agent_resp.get("decided_mode", "")
        if decided_mode == "prediction":
            department = agent_resp.get("department", department)
            project    = agent_resp.get("project", project)
            mode = "prediction"
            logger.info("[Agent] auto 판단 결과: prediction | department=%s | project=%s", department, project)
            # 이후 prediction 블록에서 처리
        else:
            logger.info("[Agent] auto 판단 결과: %s", decided_mode or "direct_answer")
            # clinical 또는 직접 답변
            return JSONResponse({
                "status":  agent_resp.get("status", "success"),
                "mode":    "auto",
                "message": agent_resp.get("message") or agent_resp.get("answer", ""),
                "sources": agent_resp.get("sources", []),
            })

    # ── prediction 모드: 파일 저장 → Agent plan → 컨테이너 실행 → interpret ─
    agent_plan: dict = {}
    timestamp       = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_input_dir  = Path(SAVE_BASE_INPUT_DIR)  / department / project / timestamp
    save_output_dir = Path(SAVE_BASE_OUTPUT_DIR) / department / project / timestamp
    save_input_dir.mkdir(parents=True, exist_ok=True)
    save_output_dir.mkdir(parents=True, exist_ok=True)

    # 파일 저장
    await _save_uploaded_files(file_dict, save_input_dir)

    # 원본 스캔 attachments (interpret에 원본 이미지 + 메타 전달 → VLM 종합판독)
    attachments = await inference_service.build_attachments_from_dir(save_input_dir)

    # Agent plan — prediction 실행 계획 수립
    uploaded_files = [f for files in file_dict.values() for f in files]
    uploaded_types = _detect_uploaded_types(uploaded_files)
    logger.info(
        "[Client] prediction 입력 확정: department=%s | project=%s | uploaded_types=%s | input_dir=%s",
        department,
        project,
        uploaded_types,
        save_input_dir,
    )
    agent_plan = await agent_service.plan(
        mode="prediction",
        query=query,
        department=department,
        project=project,
        uploaded_types=uploaded_types,
    )

    # Agent가 type_mismatch를 반환한 경우
    if agent_plan.get("status") == "type_mismatch":
        return JSONResponse(agent_plan)

    # 파이프라인 자동 전환
    pipeline_key = (department, project)
    if pipeline_key in PIPELINE_REQUIRED:
        logger.info(f"파이프라인 자동 전환: {department}/{project}")
        steps = PIPELINE_REQUIRED[pipeline_key]

        dcm_path = next(save_input_dir.rglob("*.dcm"), None)
        if dcm_path is None:
            return JSONResponse({"status": "error", "message": "DICOM 파일을 찾을 수 없습니다."})

        result = await pipeline_service.run_pipeline(
            steps=steps,
            image_path=_to_container_path(dcm_path),
            save_output_dir=save_output_dir,
        )

        if result.get("status") != "success":
            return JSONResponse(content=result)

        step_results = result.get("step_results", [])

        # 프론트엔드용 이미지: step_entry["images"][*].data 우선,
        # 없으면 image_b64 / images_b64 하위 호환 키 사용
        front_images: list[str] = []
        for s in step_results:
            agent_images = s.get("images") or []
            if agent_images:
                front_images.extend(img["data"] for img in agent_images if img.get("data"))
            elif s.get("images_b64"):
                front_images.extend(f"data:image/png;base64,{b}" for b in s["images_b64"])
            elif s.get("image_b64"):
                front_images.append(f"data:image/png;base64,{s['image_b64']}")

        predictions = next(
            (s.get("predictions") for s in reversed(step_results) if s.get("predictions")),
            None,
        )

        # Agent interpret용: images(role+data), model_output 포함한 풀 데이터
        interpret_steps = [
            {
                "step":         s.get("step"),
                "model":        s.get("model"),
                "result_type":  s.get("result_type"),
                "predictions":  _normalize_predictions(s.get("predictions")),
                "model_output": s.get("model_output"),
                "images":       s.get("images"),
            }
            for s in step_results
        ]
        interpret_result = await agent_service.interpret(
            query=query,
            step_results=interpret_steps,
            task={"department": department, "project": project},
            execution_context={
                "mode": "prediction",
                "plan": agent_plan.get("plan") or agent_plan,
                "attachments": attachments,
            },
        )
        logger.info("[Inference] final interpretation:\n%s", interpret_result.get("interpretation", ""))
        logger.info(
            "[Inference] final interpretation_images keys: %s",
            list((interpret_result.get("images") or {}).keys()),
        )

        # 프론트용 step_results: 이미지 base64 제거, 요약 정보만
        front_step_results = [
            {
                "step":        s.get("step"),
                "model":       s.get("model"),
                "result_type": s.get("result_type"),
                "predictions": s.get("predictions"),
            }
            for s in step_results
        ]

        return JSONResponse({
            "status":                "success",
            "mode":                  "prediction",
            "result_type":           "image",
            "images":                front_images,
            "predictions":           predictions,
            "step_results":          front_step_results,
            "interpretation":        interpret_result.get("interpretation", ""),
            "interpretation_images": interpret_result.get("images", {}),
        })

    # 단건 inference
    result = await inference_service.run_inference(
        department=department,
        project=project,
        save_input_dir=save_input_dir,
        save_output_dir=save_output_dir,
    )

    if result.get("status") != "success":
        return JSONResponse(content=result)

    result_type = result.get("result_type", "text")
    # Agent interpret용 step_results: images_for_agent(role+data), model_output 포함
    interpret_steps = [{
        "step":         1,
        "model":        project,
        "result_type":  result_type,
        "predictions":  _normalize_predictions(result.get("predictions")),
        "model_output": result.get("model_output"),
        "images":       result.get("images_for_agent"),  # [{"role": ..., "data": ...}]
    }]
    interpret_result = await agent_service.interpret(
        query=query,
        step_results=interpret_steps,
        task={"department": department, "project": project},
        execution_context={
            "mode": "prediction",
            "plan": agent_plan.get("plan") or agent_plan,
            "attachments": attachments,
        },
    )
    logger.info("[Inference] final interpretation:\n%s", interpret_result.get("interpretation", ""))
    logger.info(
        "[Inference] final interpretation_images keys: %s",
        list((interpret_result.get("images") or {}).keys()),
    )
    result["mode"]                  = "prediction"
    result["interpretation"]        = interpret_result.get("interpretation", "")
    result["interpretation_images"] = interpret_result.get("images", {})
    # 프론트엔드에는 images_for_agent 노출 불필요
    result.pop("images_for_agent", None)

    return JSONResponse(content=result)


# ── 헬퍼 함수 ────────────────────────────────────────────────────────────────

def _normalize_predictions(predictions) -> list:
    """predictions가 dict이면 list로 감싸고, None이면 빈 list 반환."""
    if predictions is None:
        return []
    if isinstance(predictions, list):
        return predictions
    return [predictions]


def _detect_uploaded_types(files: list) -> list[str]:
    """업로드된 파일 목록에서 타입 문자열 리스트 반환."""
    types: set[str] = set()
    for f in files:
        name = (f.filename or "").lower()
        if name.endswith(".dcm") or name.endswith(".dicom"):
            types.add("dicom")
        elif name.endswith(".nii") or name.endswith(".nii.gz"):
            types.add("nifti")
        elif name.endswith(".csv"):
            types.add("csv")
        elif name.endswith((".png", ".jpg", ".jpeg")):
            types.add("image")
    return list(types)


async def _save_uploaded_files(
    file_dict: Dict[str, list],
    save_input_dir: Path,
) -> None:
    """form-data 파일들을 save_input_dir 하위에 저장."""
    for key, files in file_dict.items():
        key_dir = save_input_dir / key
        key_dir.mkdir(parents=True, exist_ok=True)
        for idx, file in enumerate(files):
            try:
                file_bytes = await file.read()
                save_path  = key_dir / f"{idx:03d}_{file.filename}"
                save_path.write_bytes(file_bytes)
                logger.info(
                    "[Client] 업로드 파일 저장: field=%s | filename=%s | bytes=%s | path=%s",
                    key,
                    file.filename,
                    len(file_bytes),
                    save_path,
                )
            except Exception as e:
                logger.warning(f"파일 저장 실패 ({file.filename}): {e}")
    logger.info(f"파일 저장 완료 → {save_input_dir}")
