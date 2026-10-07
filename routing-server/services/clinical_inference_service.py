import base64
import json
import logging
import re
import tempfile
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from config.settings import CLINICAL_WORK_DIR, GRIDFS_BUCKET
from repositories.projects_repository import ProjectsRepository
from services.agent_service import AgentService
from services.inference_service import InferenceService
from services.medical_file_service import MedicalFileService
from services.pipeline_service import PipelineService
from services.risk_policy_service import RiskPolicyService


logger = logging.getLogger("maple.clinical-inference")


class _NoopResultsRepository:
    async def save_result(self, _document):
        return None


def _clinical_work_root() -> Path:
    data_root = Path("./data").resolve()
    work_root = Path(CLINICAL_WORK_DIR).resolve()
    try:
        work_root.relative_to(data_root)
    except ValueError as exc:
        raise RuntimeError(
            "CLINICAL_WORK_DIR must be inside ./data so inference runtime "
            "containers can access it through the /data shared volume."
        ) from exc
    work_root.mkdir(parents=True, exist_ok=True)
    return work_root


def _work_prefix(analysis_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", analysis_id).strip("-")
    return f"{safe or 'analysis'}-"


def _uploaded_types(files: list[dict]) -> list[str]:
    mapping = {
        "dcm": "dicom",
        "dicom": "dicom",
        "nii": "nifti",
        "nii.gz": "nifti",
        "csv": "csv",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
    }
    return sorted({mapping[item["extension"]] for item in files if item["extension"] in mapping})


def _artifact_role(role: str | None) -> str:
    normalized = (role or "").lower()
    if "heat" in normalized or "gradcam" in normalized:
        return "heat"
    if "box" in normalized or "bbox" in normalized or "detect" in normalized:
        return "box"
    return "base"


def _result_type_text(value) -> str:
    if isinstance(value, list):
        return " + ".join(str(item) for item in value) or "unknown"
    return str(value or "unknown")


def _confidence(predictions) -> float | None:
    values: list[float] = []

    def visit(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key.lower() in {"confidence", "probability", "score", "prob"}:
                    if isinstance(nested, (int, float)):
                        values.append(float(nested))
                else:
                    visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(predictions)
    return max(values) if values else None


def _execution_steps(plan: dict) -> tuple[str, list[dict]]:
    """
    Agent의 auto 응답을 실행 가능한 step 목록으로 정규화한다.

    지원 계약:
    - general: execution_plan.steps[].model_name/project/department
    - prediction: 위 DAG 계약 또는 최상위 model_name/project/department
    """
    mode = plan.get("mode") or plan.get("decided_mode") or ""
    raw_steps = (plan.get("execution_plan") or {}).get("steps") or []
    if not raw_steps and mode == "prediction":
        raw_steps = [{
            "step_id": "s1",
            "model_name": (
                plan.get("model_name") or plan.get("model") or plan.get("project")
            ),
            "project": plan.get("project"),
            "department": plan.get("department"),
            "depends_on": [],
        }]

    normalized: list[dict] = []
    for index, raw in enumerate(raw_steps, start=1):
        project = raw.get("project")
        model_name = raw.get("model_name") or raw.get("model") or project
        department = raw.get("department")
        step_id = str(raw.get("step_id") or raw.get("step") or f"s{index}")
        missing = [
            field
            for field, value in (
                ("model_name", model_name),
                ("project", project),
                ("department", department),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"Agent execution_plan step '{step_id}' 필드 누락: "
                f"{', '.join(missing)}"
            )
        normalized.append({
            **raw,
            "step_id": step_id,
            "model_name": model_name,
            "project": project,
            "department": department,
            "depends_on": [str(value) for value in (raw.get("depends_on") or [])],
        })

    if not normalized:
        raise RuntimeError(
            f"Agent 응답에 실행 가능한 모델 step이 없습니다. mode={mode or 'unknown'}"
        )
    return mode, normalized


class ClinicalInferenceService:
    def __init__(self, database):
        self.db = database
        self.bucket = AsyncIOMotorGridFSBucket(database, bucket_name=GRIDFS_BUCKET)
        self.medical_files = MedicalFileService(database)
        self.projects = ProjectsRepository(database, None)
        self.agent = AgentService()
        self.inference = InferenceService(_NoopResultsRepository(), self.projects)
        self.pipeline = PipelineService(self.projects, _NoopResultsRepository())
        self.risk_policy = RiskPolicyService(database)

    async def _download_inputs(self, analysis: dict, input_dir: Path) -> list[dict]:
        metadata = await self.db["medical_files"].find({
            "file_id": {"$in": analysis.get("input_file_ids") or []},
            "analysis_id": analysis["analysis_id"],
            "status": "active",
        }).to_list(None)
        by_id = {item["file_id"]: item for item in metadata}
        ordered = [by_id[file_id] for file_id in analysis["input_file_ids"] if file_id in by_id]
        if len(ordered) != len(analysis.get("input_file_ids") or []):
            raise RuntimeError("분석 입력 파일 일부를 찾을 수 없습니다.")
        for index, item in enumerate(ordered):
            stream = await self.bucket.open_download_stream(item["gridfs_id"])
            content = await stream.read()
            filename = Path(item["original_filename"]).name
            (input_dir / f"{index:03d}_{filename}").write_bytes(content)
        return ordered

    async def execute(self, analysis: dict) -> dict:
        work_root = _clinical_work_root()
        with tempfile.TemporaryDirectory(
            prefix=_work_prefix(analysis["analysis_id"]),
            dir=work_root,
        ) as temp_dir:
            root = Path(temp_dir)
            logger.info(
                "임상 분석 공유 작업 디렉터리 analysis_id=%s path=%s",
                analysis["analysis_id"],
                root,
            )
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            input_files = await self._download_inputs(analysis, input_dir)
            attachments = await self.inference.build_attachments_from_dir(input_dir)
            plan = await self.agent.plan(
                mode="auto",
                query=analysis.get("query") or "업로드한 영상을 분석해줘.",
                uploaded_types=_uploaded_types(input_files),
            )
            if plan.get("status") == "error":
                raise RuntimeError(plan.get("message") or "Agent plan failed")
            decided_mode, steps = _execution_steps(plan)
            logger.info(
                "Agent 실행 계획 정규화 mode=%s steps=%s",
                decided_mode,
                [
                    {
                        "step_id": step["step_id"],
                        "model_name": step["model_name"],
                        "project": step["project"],
                        "department": step["department"],
                    }
                    for step in steps
                ],
            )

            dag_result = await self.pipeline.run_dag(
                steps=steps,
                save_input_dir=input_dir,
                save_output_dir=output_dir,
            )
            if dag_result.get("status") == "error":
                errors = dag_result.get("errors") or []
                detail = "; ".join(item.get("error", "") for item in errors)
                raise RuntimeError(
                    detail or dag_result.get("message") or "모델 DAG 실행에 실패했습니다."
                )
            step_results = dag_result.get("step_results") or []
            step_by_project = {step["project"]: step for step in steps}

            paths_and_roles: list[tuple[Path, str]] = []
            base_index = 0
            for attachment in attachments:
                for data_uri in attachment.get("images") or []:
                    if not isinstance(data_uri, str) or "," not in data_uri:
                        continue
                    try:
                        image_bytes = base64.b64decode(data_uri.split(",", 1)[1])
                    except Exception:
                        logger.warning("원본 렌더 이미지 base64 디코딩 실패")
                        continue
                    base_index += 1
                    base_path = output_dir / f"base_{base_index:04d}.png"
                    base_path.write_bytes(image_bytes)
                    paths_and_roles.append((base_path, "base"))

            artifact_index = 0
            for step_result in step_results:
                for image_entry in step_result.get("images") or []:
                    data_uri = image_entry.get("data")
                    if not isinstance(data_uri, str) or "," not in data_uri:
                        continue
                    try:
                        image_bytes = base64.b64decode(data_uri.split(",", 1)[1])
                    except Exception:
                        logger.warning(
                            "step 결과 이미지 base64 디코딩 실패 step=%s",
                            step_result.get("step_id"),
                        )
                        continue
                    artifact_index += 1
                    artifact_path = output_dir / f"artifact_{artifact_index:04d}.png"
                    artifact_path.write_bytes(image_bytes)
                    paths_and_roles.append((
                        artifact_path,
                        _artifact_role(image_entry.get("role")),
                    ))

            result_file_ids = await self.medical_files.store_derived(
                paths_and_roles=paths_and_roles,
                analysis=analysis,
            )

            prediction_entries = [
                {
                    "step_id": result.get("step_id"),
                    "model_name": step_by_project.get(
                        result.get("model"), {}
                    ).get("model_name") or result.get("model"),
                    "project": result.get("model"),
                    "predictions": result.get("predictions"),
                }
                for result in step_results
            ]
            predictions = (
                prediction_entries[0]["predictions"]
                if len(prediction_entries) == 1
                else prediction_entries
            )
            risk_tier, risk_status = await self.risk_policy.assess(
                steps,
                step_results,
            )
            interpret_steps = [
                {
                    "step": result.get("step_id"),
                    "model": step_by_project.get(
                        result.get("model"), {}
                    ).get("model_name") or result.get("model"),
                    "result_type": _result_type_text(result.get("result_type")),
                    "predictions": result.get("predictions") or [],
                    "model_output": result.get("model_output"),
                    "images": result.get("images") or [],
                }
                for result in step_results
            ]
            interpret = await self.agent.interpret(
                query=analysis.get("query") or "업로드한 영상을 분석해줘.",
                step_results=interpret_steps,
                task={
                    "department": " · ".join(
                        dict.fromkeys(step["department"] for step in steps)
                    ),
                    "project": " · ".join(step["project"] for step in steps),
                },
                execution_context={
                    "mode": decided_mode,
                    "plan": plan.get("execution_plan") or plan,
                    "attachments": attachments,
                },
            )
            model_names = [step["model_name"] for step in steps]
            departments = list(dict.fromkeys(step["department"] for step in steps))
            projects = [step["project"] for step in steps]
            return {
                "department": " · ".join(departments),
                "project": " · ".join(projects),
                "model_name": " · ".join(model_names),
                "risk_tier": risk_tier,
                "risk_status": risk_status,
                "confidence": _confidence(predictions),
                "finding": interpret.get("finding") or None,
                "interpretation": interpret.get("interpretation") or None,
                "recommendation": interpret.get("recommendation") or None,
                "predictions": predictions,
                "result_file_ids": result_file_ids,
                "normalized_result": json.loads(json.dumps({
                    "mode": decided_mode,
                    "steps": [
                        {
                            "step_id": result.get("step_id"),
                            "model_name": step_by_project.get(
                                result.get("model"), {}
                            ).get("model_name") or result.get("model"),
                            "project": result.get("model"),
                            "result_type": result.get("result_type"),
                            "model_output": result.get("model_output"),
                        }
                        for result in step_results
                    ],
                    "errors": dag_result.get("errors") or [],
                }, default=str)),
            }
