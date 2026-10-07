from typing import Dict, List
import re


class ProjectsRepository:
    def __init__(self, db, main_document_id):
        self.db = db
        self.departments_collection = self.db["departments"]
        self.main_document_id = main_document_id

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

    async def _get_doc(self):
        return await self.departments_collection.find_one({})

    async def _save_doc(self, doc):
        await self.departments_collection.replace_one({"_id": doc["_id"]}, doc)

    def _find_dept(self, doc, department_name: str) -> dict | None:
        for d in doc.get("departments", []):
            if d["department_name"] == department_name:
                return d
        return None

    def _find_project(self, dept: dict, project_name: str) -> tuple[str, dict] | tuple[None, None]:
        # Agent/Frontend/DB가 표시용 이름을 조금씩 다르게 쓰는 경우를 흡수한다.
        # 예: "Parkinson Gait" ↔ "ParkinsonGait_ML", "SMWI Segmentation" ↔ "nnUNet_SMWI_Segmentation"
        def normalize(value: str) -> str:
            value = value.lower().strip()
            value = re.sub(r"[^a-z0-9]+", "", value)
            for suffix in ("ml", "model"):
                if value.endswith(suffix):
                    value = value[: -len(suffix)]
            return value

        normalized = normalize(project_name)
        for pid, pv in dept.get("projects", {}).items():
            stored = pv.get("project_name", "")
            model_name = pv.get("model_name", "")
            stored_candidates = {
                stored,
                stored.replace("_", " "),
                model_name,
                model_name.replace("_", " "),
            }
            if stored == project_name or normalized in {normalize(v) for v in stored_candidates if v}:
                return pid, pv
        return None, None

    def _next_id(self, mapping: dict) -> str:
        if not mapping:
            return "1"
        return str(max(int(k) for k in mapping.keys()) + 1)

    # ── 부서 ───────────────────────────────────────────────────────────────────

    async def get_departments(self):
        doc = await self._get_doc()
        if not doc:
            return []
        return [{"department_name": d["department_name"]} for d in doc.get("departments", [])]

    async def get_department_by_name(self, department_name: str) -> dict | None:
        doc = await self._get_doc()
        if not doc:
            return None
        dept = self._find_dept(doc, department_name)
        return dept

    async def create_department(self, department_name: str):
        doc = await self._get_doc()
        if doc and self._find_dept(doc, department_name):
            return {"error": f"Department '{department_name}' already exists."}
        new_dept = {"department_name": department_name, "projects": {}}
        if doc:
            doc["departments"].append(new_dept)
            await self._save_doc(doc)
        else:
            await self.departments_collection.insert_one({"departments": [new_dept]})
        return {"message": f"Department '{department_name}' created."}

    async def update_department(self, old_name: str, new_name: str):
        doc = await self._get_doc()
        if not doc:
            return {"matched_count": 0, "modified_count": 0}
        dept = self._find_dept(doc, old_name)
        if not dept:
            return {"matched_count": 0, "modified_count": 0}
        dept["department_name"] = new_name
        await self._save_doc(doc)
        return {"matched_count": 1, "modified_count": 1, "acknowledged": True}

    async def delete_department(self, department_name: str):
        doc = await self._get_doc()
        if not doc:
            return {"matched_count": 0, "modified_count": 0}
        before = len(doc["departments"])
        doc["departments"] = [d for d in doc["departments"] if d["department_name"] != department_name]
        if len(doc["departments"]) == before:
            return {"matched_count": 0, "modified_count": 0}
        await self._save_doc(doc)
        return {"matched_count": 1, "modified_count": 1, "acknowledged": True}

    # ── 프로젝트 ───────────────────────────────────────────────────────────────

    async def get_projects_by_department(self, department_name: str):
        dept = await self.get_department_by_name(department_name)
        if not dept:
            return None
        return {pid: pv["project_name"] for pid, pv in dept.get("projects", {}).items()}

    async def create_project(self, department_name: str, project_name: str):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        pid, _ = self._find_project(dept, project_name)
        if pid:
            return {"error": f"Project '{project_name}' already exists."}
        new_id = self._next_id(dept["projects"])
        dept["projects"][new_id] = {"project_name": project_name}
        await self._save_doc(doc)
        return {"message": f"Project '{project_name}' created.", "project_id": new_id}

    async def update_project(self, department_name: str, old_project_name: str, new_project_name: str):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return False
        pid, pv = self._find_project(dept, old_project_name)
        if not pid:
            return False
        pv["project_name"] = new_project_name
        await self._save_doc(doc)
        return True

    async def delete_project(self, department_name: str, project_name: str):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        pid, _ = self._find_project(dept, project_name)
        if not pid:
            return {"error": f"Project '{project_name}' not found."}
        del dept["projects"][pid]
        await self._save_doc(doc)
        return True

    # ── 모델 (project에 직접 저장) ─────────────────────────────────────────────

    async def get_model_by_project(self, department_name: str, project_name: str) -> dict | None:
        """department + project_name으로 모델 정보 전체 반환."""
        dept = await self.get_department_by_name(department_name)
        if not dept:
            return None
        _, pv = self._find_project(dept, project_name)
        if not pv or "model_name" not in pv:
            return None
        return pv

    async def get_model_by_info(self, department_name: str, project_name: str, model_name: str) -> dict | None:
        """기존 인터페이스 호환: department + project_name + model_name으로 모델 정보 반환."""
        pv = await self.get_model_by_project(department_name, project_name)
        if not pv:
            return None
        if pv.get("model_name") != model_name:
            return None
        return pv

    async def get_models_by_department_and_project(self, department_name: str, project_name: str):
        """project 하나에 모델 하나이므로 단일 항목 반환."""
        pv = await self.get_model_by_project(department_name, project_name)
        if not pv:
            return {"error": f"Project '{project_name}' not found in '{department_name}'."}
        if "model_name" not in pv:
            return {"models": {}}
        return {"models": {"1": pv["model_name"]}}

    async def create_model(
        self,
        department_name: str,
        project_name: str,
        model_name: str,
        model_description: str,
        model_path: Dict[str, str],
        required_data: List[str],
        task_type: str,
        result_type: str,
        output_image_role: str | None = None,
        inference_script_path: str = None,
        requirements_path: str | None = None,
        docker_image: str | None = None,
        docker_build_context: str | None = None,
        docker_service_url: str | None = None,
        inference_server: str = "local",
        endpoint: str = "/infer",
        parallel_safe: bool = True,
    ):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}

        # project 없으면 자동 생성
        pid, pv = self._find_project(dept, project_name)
        if not pid:
            pid = self._next_id(dept["projects"])
            dept["projects"][pid] = {"project_name": project_name}
            pv = dept["projects"][pid]

        # 모델 정보를 project에 직접 저장
        pv.update({
            "model_id":          f"{department_name}/{project_name}",
            "model_name":        model_name,
            "model_description": model_description,
            "model_path":        model_path,
            "required_data":     required_data,
            "task_type":         task_type,
            "result_type":       result_type,
            "inference_script":  inference_script_path,
            "inference_server":  inference_server,
            "endpoint":          endpoint,
            "parallel_safe":     parallel_safe,
        })
        # output_image_role: 결과 이미지 종류 — Agent VLM 해석 시 참고
        # 예) "bbox_overlay" | "gradcam_overlay" | "segmentation_overlay"
        # 값이 없으면 이전 role이 남지 않도록 명시적으로 제거
        if output_image_role:
            pv["output_image_role"] = output_image_role
        else:
            pv.pop("output_image_role", None)
        if requirements_path:
            pv["requirements_path"] = requirements_path
        if docker_image or docker_build_context or docker_service_url:
            pv["docker"] = {
                "image":         docker_image or "",
                "build_context": docker_build_context or "",
                "service_url":   docker_service_url or "",
            }

        await self._save_doc(doc)
        return {"message": f"Model '{model_name}' saved to project '{project_name}' in '{department_name}'."}

    async def update_model(self, department_name: str, project_name: str, old_model_name: str, new_model_name: str):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        pid, pv = self._find_project(dept, project_name)
        if not pid or pv.get("model_name") != old_model_name:
            return {"error": f"Model '{old_model_name}' not found."}
        pv["model_name"] = new_model_name
        await self._save_doc(doc)
        return {"message": f"Model '{old_model_name}' renamed to '{new_model_name}'."}

    async def set_risk_policy(
        self,
        department_name: str,
        project_name: str,
        policy: dict,
    ):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name) if doc else None
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        _, project = self._find_project(dept, project_name)
        if not project or "model_name" not in project:
            return {"error": f"Model project '{project_name}' not found."}
        project["risk_policy"] = policy
        await self._save_doc(doc)
        return {
            "department": department_name,
            "project": project["project_name"],
            "model_name": project["model_name"],
            "risk_policy": policy,
        }

    async def delete_model(self, department_name: str, project_name: str, model_name: str):
        doc = await self._get_doc()
        dept = self._find_dept(doc, department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        pid, pv = self._find_project(dept, project_name)
        if not pid or pv.get("model_name") != model_name:
            return {"error": f"Model '{model_name}' not found."}
        # 모델 필드만 제거, project는 유지
        for key in ["model_id", "model_name", "model_description", "model_path", "required_data",
                    "task_type", "result_type", "output_image_role",
                    "inference_script", "requirements_path", "docker",
                    "inference_server", "endpoint", "parallel_safe", "risk_policy"]:
            pv.pop(key, None)
        await self._save_doc(doc)
        return {"message": f"Model '{model_name}' deleted from project '{project_name}'."}
