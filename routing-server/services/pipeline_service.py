"""
Control Node — 파이프라인 모델 순차 실행 서비스.

Agent가 생성한 Execution Plan을 받아 Step별로 모델 컨테이너를 순차 호출하고
이전 Step의 출력을 다음 Step의 입력으로 전달한다.
"""
import os
import asyncio
import logging
import base64

from config.settings import MODEL_ROOT as _DEFAULT_MODEL_ROOT
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from services.inference_client import (
    InferenceServerClient,
    InferenceServerError,
    InferenceServerTimeout,
)

logger = logging.getLogger("maple.pipeline")


class PipelineService:
    def __init__(self, projects_repo, results_repo):
        self.projects_repo = projects_repo
        self.results_repo  = results_repo
        self.inference_client = InferenceServerClient()

    async def _get_container_url(self, department: str, project: str) -> str | None:
        """DB에서 모델의 docker.service_url 조회"""
        model_info = await self.projects_repo.get_model_by_project(department, project)
        if not model_info:
            return None
        return model_info.get("docker", {}).get("service_url")

    async def _get_model_path(self, department: str, project: str) -> str | None:
        """DB에서 모델의 checkpoint 경로 조회.
        컨테이너 환경(MODEL_ROOT=/AI_Models)이면 컨테이너 내부 경로로 변환.
        로컬 환경이면 절대경로로 변환.
        """
        model_info = await self.projects_repo.get_model_by_project(department, project)
        if not model_info:
            return None
        model_path_dict = model_info.get("model_path", {})
        if not model_path_dict:
            return None
        rel_path = list(model_path_dict.values())[0]  # 예: "AI_Models/Rheumatology/.../best.pt"

        # 컨테이너에서는 /AI_Models 볼륨 마운트, 로컬에서는 절대경로
        model_root = os.getenv("MODEL_ROOT") or _DEFAULT_MODEL_ROOT
        if model_root:
            # Docker/K8s: MODEL_ROOT 환경변수로 컨테이너 내부 경로 구성
            clean = rel_path.replace("\\", "/").lstrip("/")
            if clean.startswith("AI_Models/"):
                clean = clean[len("AI_Models/"):]
            return f"{model_root.rstrip('/')}/{clean}"
        else:
            # 로컬: 서버 자체 기본 경로 사용
            return None

    def _to_container_result(self, infer_result: dict) -> dict:
        result = infer_result.get("result") or {}
        output_images = infer_result.get("output_images") or []
        restored = {
            "status": "ok",
            "result_type": result.get("result_type") or infer_result.get("metadata", {}).get("result_type"),
            "predictions": result.get("predictions"),
        }
        if result.get("data") is not None:
            restored["data"] = result.get("data")
        restored.update(infer_result.get("model_output") or {})
        if len(output_images) == 1:
            restored["image_b64"] = output_images[0]
        elif len(output_images) > 1:
            restored["images_b64"] = output_images
        return restored

    def _to_container_path(self, host_path: Path) -> str:
        """호스트 ./data/... 경로 → 컨테이너 /data/... 경로."""
        try:
            rel = host_path.resolve().relative_to(Path("./data").resolve())
            return "/data/" + str(rel).replace("\\", "/")
        except ValueError as exc:
            raise ValueError(
                f"추론 입력은 공유 볼륨 ./data 아래에 있어야 합니다: {host_path}"
            ) from exc

    # ──────────────────────────────────────────────────
    # DAG 실행기 (general 에이전틱 파이프라인 Step 2)
    # execution_plan.steps[] 를 depends_on 기반 병렬+순차로 실행
    # ──────────────────────────────────────────────────

    @staticmethod
    def _validate_dag(by_id: dict) -> None:
        """depends_on 참조 유효성 + 사이클 검사 (Kahn 위상정렬)."""
        from collections import deque

        indeg = {sid: 0 for sid in by_id}
        adj: dict[str, list[str]] = {sid: [] for sid in by_id}
        for sid, step in by_id.items():
            for dep in (step.get("depends_on") or []):
                dep = str(dep)
                if dep not in by_id:
                    raise ValueError(f"step '{sid}'의 depends_on '{dep}' 참조가 존재하지 않음")
                indeg[sid] += 1
                adj[dep].append(sid)

        q = deque([sid for sid, deg in indeg.items() if deg == 0])
        seen = 0
        while q:
            node = q.popleft()
            seen += 1
            for nxt in adj[node]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        if seen != len(by_id):
            raise ValueError("depends_on 사이클 감지 — 실행 계획이 DAG가 아님")

    def _resolve_step_input(self, step: dict, saved_files: list[Path], dep_results: dict, save_input_dir: Path):
        """
        step 입력 구성 (Track B: 선택 + 포맷 정합, 변환 레지스트리 기반).
        - required 카테고리 직접 매칭 우선
        - 없으면 (원본→목표) 변환 레지스트리 조회해 변환 (예: dicom→png)
        - required 없으면 업로드 우선순위 폴백
        - 의존: 선행 출력의 ROI 병합
        변환/매칭 불가 시 None → run_dag가 skip + errors[] 기록
        """
        from services.file_categories import CAT_PRIORITY, categorize, required_to_category, select_input_file
        from services.format_convert import convert as convert_format, get_converter

        by_cat = categorize(saved_files)
        required = {required_to_category(r) for r in (step.get("required_data") or [])}
        sid = str(step.get("step_id") or step.get("step") or step.get("project"))

        base: Path | None = None
        if required:
            # 1) required 카테고리 직접 매칭
            for cat in CAT_PRIORITY:
                if cat in required and by_cat.get(cat):
                    base = by_cat[cat][0]
                    break
            # 2) 변환 레지스트리: (원본 카테고리 → 목표) 변환기 조회
            if base is None:
                out_dir = save_input_dir / "_converted" / sid
                for target in required:
                    for src_cat in CAT_PRIORITY:
                        src_files = by_cat.get(src_cat)
                        if not src_files or not get_converter(src_cat, target):
                            continue
                        conv = convert_format(src_files[0], src_cat, target, out_dir)
                        if conv:
                            logger.info("[DAG %s] 포맷 변환 %s→%s: %s", sid, src_cat, target, conv.name)
                            base = conv
                            break
                    if base:
                        break
        else:
            base = select_input_file(saved_files, [])

        if base is None:
            return None
        base_path = self._to_container_path(base)

        roi = None
        for dep in dep_results.values():
            if not isinstance(dep, dict):
                continue
            mo = dep.get("model_output") or {}
            data = mo.get("data") if isinstance(mo.get("data"), dict) else {}
            roi = roi or (data.get("roi") if data else None) or mo.get("roi")

        if roi:
            return {"image_path": base_path, "roi": roi}
        return base_path

    async def _exec_step(self, step: dict, input_data, save_output_dir: Path) -> dict:
        """단일 step 실행 → step_entry 반환. 실패 시 예외."""
        sid     = str(step.get("step_id") or step.get("step") or step.get("project"))
        dept    = step.get("department")
        project = step.get("project")
        logger.info("[DAG %s] 실행 - %s/%s", sid, dept, project)

        model_info = await self.projects_repo.get_model_by_project(dept, project)
        if not model_info:
            raise InferenceServerError(f"[{sid}] {project} 모델 정보 없음")

        params: dict[str, Any] = {}
        if isinstance(input_data, str):
            input_path = input_data
        elif isinstance(input_data, dict) and isinstance(
            input_data.get("image_path"), str
        ):
            input_path = input_data["image_path"]
            params.update({
                key: value
                for key, value in input_data.items()
                if key != "image_path"
            })
        else:
            raise InferenceServerError(
                f"[{sid}] v2 입력 경로 형식이 올바르지 않습니다."
            )

        step_output_dir = save_output_dir / sid
        step_output_dir.mkdir(parents=True, exist_ok=True)
        infer_result = await self.inference_client.infer_v2(
            model_info=model_info,
            model_name=project,
            input_path=input_path,
            output_dir=self._to_container_path(step_output_dir),
            params=params,
        )
        result = self._to_container_result(infer_result)
        if result.get("status") != "ok":
            raise InferenceServerError(f"[{sid}] 추론 오류: {result.get('detail')}")

        # 이미지 결과 저장 + images 필드
        output_image_role = model_info.get("output_image_role")
        images_field: list[dict] = []
        raw_b64: list[str] = []
        b64_list = result.get("images_b64") or ([result["image_b64"]] if result.get("image_b64") else [])
        for b64 in b64_list:
            img_path = save_output_dir / f"{sid}_{project}_result_{len(raw_b64) + 1}.png"
            img_path.write_bytes(base64.b64decode(b64))
            raw_b64.append(b64)
            entry: dict = {"data": f"data:image/png;base64,{b64}"}
            if output_image_role:
                entry["role"] = (
                    f"{output_image_role}_{len(raw_b64)}" if len(b64_list) > 1 else output_image_role
                )
            images_field.append(entry)

        # result_type coalesce: 컨테이너가 안 주면 DB 메타 → plan step → 이미지/예측 유무로 유도.
        # (agent interpret 스키마가 result_type을 필수 문자열로 받으므로 null 금지)
        result_type = (
            result.get("result_type")
            or model_info.get("result_type")
            or step.get("result_type")
            or ("image" if images_field else "text")
        )

        step_entry: dict = {
            "step_id":      sid,
            "step":         step.get("step"),
            "model":        project,
            "department":   dept,
            "task_type":    step.get("task_type"),
            "result_type":  result_type,
            "predictions":  result.get("predictions"),
            "model_output": {
                k: v for k, v in result.items()
                if k not in ("status", "result_type", "predictions", "image_b64", "images_b64")
            },
            "image_b64":    raw_b64[0] if len(raw_b64) == 1 else None,
            "images_b64":   raw_b64 if len(raw_b64) > 1 else [],
        }
        if images_field:
            step_entry["images"] = images_field
        logger.info("[DAG %s] 완료 - result_type=%s", sid, result_type)
        return step_entry

    async def run_dag(
        self,
        steps: list[dict],       # agent execution_plan.steps[]
        save_input_dir: Path,    # 저장된 원본 디렉터리
        save_output_dir: Path,
    ) -> dict:
        """
        execution_plan.steps[] 를 depends_on 기반 DAG로 실행 (병렬+순차).

        각 step: {step_id, model_name, department, project, task_type,
                  result_type, required_data, depends_on}
        - depends_on:[] → 루트, 원본 입력
        - depends_on:[step_id...] → 선행 완료 후 실행, 원본+선행 출력
        """
        save_output_dir.mkdir(parents=True, exist_ok=True)
        saved_files = [p for p in save_input_dir.rglob("*") if p.is_file()]

        by_id: dict[str, dict] = {}
        for s in steps:
            sid = str(s.get("step_id") or s.get("step") or s.get("project"))
            by_id[sid] = s
        if not by_id:
            return {"status": "error", "message": "실행할 step이 없습니다."}

        try:
            self._validate_dag(by_id)
        except ValueError as e:
            logger.error("DAG 검증 실패: %s", e)
            return {"status": "error", "message": str(e)}

        tasks: dict[str, asyncio.Task] = {}

        async def run_step(sid: str) -> dict:
            step = by_id[sid]
            deps = [str(d) for d in (step.get("depends_on") or [])]
            dep_results = {d: await tasks[d] for d in deps}   # 선행 완료 대기 (실패 시 전파)
            input_data = self._resolve_step_input(step, saved_files, dep_results, save_input_dir)
            if input_data is None:
                raise InferenceServerError(
                    f"[{sid}] 입력 파일 없음 (required_data={step.get('required_data')})"
                )
            return await self._exec_step(step, input_data, save_output_dir)

        for sid in by_id:
            tasks[sid] = asyncio.create_task(run_step(sid))

        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        step_results: list[dict] = []
        errors: list[dict] = []
        for sid, res in zip(tasks.keys(), gathered):
            if isinstance(res, Exception):
                logger.error("[DAG %s] 실패: %s", sid, res)
                errors.append({"step_id": sid, "error": str(res)})
            else:
                step_results.append(res)

        if not step_results:
            return {"status": "error", "message": "모든 step 실행 실패", "errors": errors}

        step_results.sort(key=lambda r: str(r.get("step_id")))

        try:
            await self.results_repo.save_result({
                "pipeline":     True,
                "dag":          True,
                "timestamp":    datetime.now(timezone.utc),
                "result_type":  "pipeline",
                "step_results": [
                    {"step_id": r.get("step_id"), "model": r.get("model"),
                     "result_type": r.get("result_type")}
                    for r in step_results
                ],
            })
        except Exception as e:
            logger.warning("DAG 결과 저장 실패: %s", e)

        return {
            "status":       "success" if not errors else "partial",
            "result_type":  "pipeline",
            "step_results": step_results,
            "errors":       errors,
        }

    async def run_pipeline(
        self,
        steps: list[dict],       # Agent Execution Plan의 steps
        image_path: str,         # 업로드된 DICOM 절대경로
        save_output_dir: Path,
    ) -> dict:
        """
        steps 예시:
        [
            { "step": 1, "model": "YOLOv12",   "department": "Rheumatology", "project": "SI Joints Detection" },
            { "step": 2, "model": "GradCAM++", "department": "Rheumatology", "project": "BME Classification" },
        ]
        """
        save_output_dir.mkdir(parents=True, exist_ok=True)

        prev_output: Any = None   # 이전 Step 출력 (다음 Step 입력으로 전달)
        step_results = []

        for step in sorted(steps, key=lambda s: s["step"]):
            step_num = step["step"]
            dept     = step["department"]
            project  = step["project"]

            logger.info(f"[Step {step_num}] 실행 시작 - {dept}/{project}")

            model_info = await self.projects_repo.get_model_by_project(dept, project)
            if not model_info:
                return {"status": "error", "message": f"[Step {step_num}] {project} 모델 정보 없음"}

            # 컨테이너 URL / 모델 경로 조회 (project 기반)
            container_url = model_info.get("docker", {}).get("service_url")
            model_path    = await self._get_model_path(dept, project)
            if not container_url:
                return {"status": "error", "message": f"[Step {step_num}] {project} 컨테이너 URL 없음"}

            # input_data 구성 — Step 1은 image_path, Step 2+는 이전 출력(ROI 좌표) 포함
            # model_path=None이면 각 서버의 기본값 사용 (로컬 개발환경)
            if step_num == 1:
                input_data = image_path
            else:
                # 이전 Step이 ROI 좌표를 반환했으면 함께 전달
                roi = prev_output.get("roi") if isinstance(prev_output, dict) else None
                if roi:
                    input_data = {
                        "image_path": prev_output.get("image_path", image_path),
                        "roi":        roi,
                    }
                else:
                    input_data = image_path
            params = {
                "container_url": container_url,
                "container_endpoint": "/run/v2",
                "model_name": project,
            }
            if model_path:
                params["model_path"] = model_path

            try:
                infer_result = await self.inference_client.infer(
                    model_info=model_info,
                    model_id=model_info.get("model_id") or f"{dept}/{project}",
                    input_data=input_data,
                    params=params,
                )
                result = self._to_container_result(infer_result)
            except InferenceServerTimeout as e:
                logger.error(f"[Step {step_num}] 추론 서버 타임아웃: {e}")
                return {"status": "error", "message": f"[Step {step_num}] {str(e)}"}
            except InferenceServerError as e:
                logger.error(f"[Step {step_num}] 추론 서버 호출 실패: {e}")
                return {"status": "error", "message": f"[Step {step_num}] {str(e)}"}

            if result.get("status") != "ok":
                return {"status": "error", "message": f"[Step {step_num}] 추론 오류: {result.get('detail')}"}

            logger.info(f"[Step {step_num}] 완료 - result_type: {result.get('result_type')}")

            # output_image_role: DB 모델 메타데이터에서 읽음
            output_image_role = (model_info or {}).get("output_image_role")

            # 이미지 결과 처리: 디스크 저장 + images 필드 구성
            images_field: list[dict] = []
            raw_b64_list: list[str]  = []

            b64_list = result.get("images_b64") or []
            if not b64_list and result.get("image_b64"):
                b64_list = [result["image_b64"]]

            for b64 in b64_list:
                img_path = save_output_dir / f"step{step_num}_{project}_result_{len(raw_b64_list)+1}.png"
                img_path.write_bytes(base64.b64decode(b64))
                logger.info(f"[Step {step_num}] 이미지 저장 → {img_path}")
                raw_b64_list.append(b64)
                img_entry: dict = {"data": f"data:image/png;base64,{b64}"}
                if output_image_role:
                    if len(b64_list) > 1:
                        img_entry["role"] = f"{output_image_role}_{len(raw_b64_list)}"
                    else:
                        img_entry["role"] = output_image_role
                images_field.append(img_entry)

            # result_type coalesce (interpret 스키마 필수 문자열 → null 금지)
            result_type = (
                result.get("result_type")
                or model_info.get("result_type")
                or ("image" if images_field else "text")
            )

            # Step 결과 기록
            step_entry: dict = {
                "step":        step_num,
                "model":       project,
                "result_type": result_type,
                "predictions": result.get("predictions"),
                "model_output": {
                    k: v for k, v in result.items()
                    if k not in ("status", "result_type", "predictions",
                                 "image_b64", "images_b64")
                },
                # 프론트엔드용 b64 목록 (1장이면 image_b64, 여러 장이면 images_b64)
                "image_b64":   raw_b64_list[0] if len(raw_b64_list) == 1 else None,
                "images_b64":  raw_b64_list if len(raw_b64_list) > 1 else [],
            }
            if images_field:
                step_entry["images"] = images_field
            step_results.append(step_entry)

            # 다음 Step에 전달할 출력 구성
            # ROI 좌표가 있으면 image_path와 함께 넘김
            if result.get("result_type") == "roi":
                prev_output = {
                    "image_path": image_path,
                    "roi": result.get("data", {}).get("roi"),
                }
            elif result.get("result_type") == "image":
                # 이미지 모델이지만 ROI도 함께 반환하는 경우 (YOLOv12 → BME 파이프라인)
                prev_output = {
                    "image_path": image_path,
                    "roi": result.get("data", {}).get("roi") if result.get("data") else None,
                }
            else:
                prev_output = result

        # DB에 파이프라인 결과 저장
        await self.results_repo.save_result({
            "pipeline":    True,
            "steps":       [{"step": s["step"], "project": s.get("project", s.get("department"))} for s in steps],
            "timestamp":   datetime.now(timezone.utc),
            "result_type": "pipeline",
            "step_results": [
                {"step": r["step"], "model": r["model"], "result_type": r["result_type"]}
                for r in step_results
            ],
        })

        return {
            "status":       "success",
            "result_type":  "pipeline",
            "step_results": step_results,
        }
