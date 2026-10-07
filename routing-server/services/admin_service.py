import os
import re
import logging
from typing import Dict, List
from fastapi import UploadFile

from repositories.projects_repository import ProjectsRepository
from services.agent_service import AgentService
from models.schemas import DepartmentCreate
from config.settings import AI_MODELS_DIR

logger = logging.getLogger("maple.admin")

def _slugify(name: str) -> str:
    """
    Docker 이미지 이름 등에 쓰기 위한 slug.
    - 소문자
    - 영문/숫자만 남기고 나머지는 '-'로 치환
    """
    name = name.lower()
    name = re.sub(r'[^a-z0-9]+', '-', name)
    return name.strip('-')


class AdminService:
    def __init__(self, projects_repo: ProjectsRepository, agent_service: AgentService | None = None):
        self.projects_repo = projects_repo
        self.agent_service = agent_service or AgentService()

    async def create_department(self, department: DepartmentCreate):
        """새로운 부서를 추가 (유효성 검사 포함)"""

        # Step 1: 중복 체크
        existing_department = await self.projects_repo.departments_collection.find_one(
            {"departments.department_name": department.department_name}
        )

        if existing_department:
            raise ValueError(f"Department [{department.department_name}] already exists.")  # 중복 시 에러 발생

        # Step 2: 중복이 없으면 레포지토리에서 추가
        result = await self.projects_repo.create_department(department.department_name)

        return result 


    async def update_department(self, old_department_name: str, new_department_name: str):
        """부서 정보 수정"""
        return await self.projects_repo.update_department(old_department_name, new_department_name)


    async def delete_department(self, department_name: str):
        """부서 삭제"""
        return await self.projects_repo.delete_department(department_name)
    
    
    async def create_project(self, department_name: str, project_name: str):
        dept = await self.projects_repo.get_department_by_name(department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        existing = dept.get("projects", {})
        if any(pv.get("project_name") == project_name for pv in existing.values()):
            return {"error": f"Project '{project_name}' already exists in '{department_name}'."}
        return await self.projects_repo.create_project(department_name, project_name)

    async def get_projects_by_department(self, department_name: str):
        dept = await self.projects_repo.get_department_by_name(department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        projects = dept.get("projects", {})
        return {"projects": {pid: pv["project_name"] for pid, pv in projects.items()}}

    async def update_project(self, department_name: str, old_project_name: str, new_project_name: str):
        if old_project_name == new_project_name:
            return {"error": "이미 같은 이름입니다."}
        dept = await self.projects_repo.get_department_by_name(department_name)
        if not dept:
            return {"error": f"Department '{department_name}' not found."}
        existing = dept.get("projects", {})
        if any(pv.get("project_name") == new_project_name for pv in existing.values()):
            return {"error": f"Project '{new_project_name}' already exists."}
        ok = await self.projects_repo.update_project(department_name, old_project_name, new_project_name)
        if not ok:
            return {"error": f"Project '{old_project_name}' not found."}
        return {"message": f"Project '{old_project_name}' renamed to '{new_project_name}'."}

    async def delete_project(self, department_name: str, project_name: str):
        result = await self.projects_repo.delete_project(department_name, project_name)
        if isinstance(result, dict) and "error" in result:
            return result
        return {"message": f"Project '{project_name}' deleted."}


    # ----- 모델 (Model) CRUD -----
    async def get_models_by_department_and_project(self, department_name: str, project_name: str):
        # 1) 부서/프로젝트 이름이 비어있는지 검사
        if not department_name.strip():
            return {"error": "Department name cannot be empty."}
        if not project_name.strip():
            return {"error": "Project name cannot be empty."}

        # 2) 레포지토리 호출
        models_data = await self.projects_repo.get_models_by_department_and_project(department_name, project_name)

        # 3) 레포지토리에서 에러 반환 시 처리
        if isinstance(models_data, dict) and "error" in models_data:
            return models_data

        # 4) 정상 데이터 반환 (예: { "models": { "1": "modelA", "2": "modelB" } })
        return models_data
    

    async def create_model(
        self,
        department_name: str,
        project_name: str,
        model_name: str,
        model_description: str,
        model_path_dict: Dict[str, UploadFile],
        required_data: List[str],
        task_type: str,
        result_type: str,
        output_image_role: str | None = None,
        inference_script: UploadFile = None,
        requirements_file: UploadFile | None = None
    ):
        # 새 구조: 추론 워크스페이스의 AI_Models/{dept}/{project}/checkpoint/
        ai_models_root = os.getenv("AI_MODELS_DIR", AI_MODELS_DIR)
        model_dir  = os.path.join(ai_models_root, department_name, project_name)
        base_dir   = os.path.join(model_dir, "checkpoint")
        os.makedirs(base_dir, exist_ok=True)

        saved_files = {}
        for key, file in model_path_dict.items():
            save_path = os.path.join(base_dir, key)
            content = await file.read()
            with open(save_path, "wb") as f:
                f.write(content)
            saved_files[key] = os.path.join("AI_Models", department_name, project_name, "checkpoint", key)
            logger.info(f"모델 weight 저장됨: {save_path}")

        script_path = None
        if inference_script:
            script_path = os.path.join(model_dir, "inference.py")
            script_content = await inference_script.read()
            with open(script_path, "wb") as f:
                f.write(script_content)
            logger.info(f"inference.py 저장됨: {script_path}")

        requirements_path = None
        if requirements_file is not None:
            requirements_path = os.path.join(base_dir, "requirements.txt")
            content = await requirements_file.read()
            with open(requirements_path, "wb") as f:
                f.write(content)
            logger.info(f"requirements.txt 저장됨: {requirements_path}")

            dockerfile_path = os.path.join(model_dir, "Dockerfile")
            dockerfile_content = (
                "FROM python:3.10-slim\n\nWORKDIR /app\n\n"
                "COPY checkpoint/requirements.txt .\n"
                "RUN pip install --no-cache-dir -r requirements.txt\n\n"
                "COPY . .\n\nCMD [\"uvicorn\", \"server:app\", \"--host\", \"0.0.0.0\", \"--port\", \"9000\"]\n"
            )
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)
            logger.info(f"Dockerfile 생성됨: {dockerfile_path}")

        # Docker 이미지 이름 / 빌드 컨텍스트
        dept_slug  = _slugify(department_name)
        proj_slug  = _slugify(project_name)
        model_slug = _slugify(model_name)

        docker_image         = f"{dept_slug}-{proj_slug}:latest"
        docker_build_context = model_dir   # Dockerfile이 있는 폴더

        # 2. DB 저장 호출
        result = await self.projects_repo.create_model(
            department_name=department_name,
            project_name=project_name,
            model_name=model_name,
            model_description=model_description,
            model_path=saved_files,
            required_data=required_data,
            task_type=task_type,
            result_type=result_type,
            output_image_role=output_image_role,
            inference_script_path=script_path,
            requirements_path=requirements_path,
            docker_image=docker_image,
            docker_build_context=docker_build_context,
        )

        # 3. Agent Wiki + ChromaDB 등록
        doc_id = f"{dept_slug}-{proj_slug}-{model_slug}"
        await self.agent_service.register_model(
            model_id=doc_id,
            model_name=model_name,
            department=department_name,
            project=project_name,
            description=model_description,
            task_type=task_type,
            required_data=required_data,
            result_type=result_type,
            output_image_role=output_image_role,
        )

        return result



    async def update_model(self, department_name: str, project_name: str, old_model_name: str, new_model_name: str):
        return await self.projects_repo.update_model(department_name, project_name, old_model_name, new_model_name)

    async def set_risk_policy(
        self,
        department_name: str,
        project_name: str,
        policy: dict,
    ):
        return await self.projects_repo.set_risk_policy(
            department_name,
            project_name,
            policy,
        )

    async def delete_model(self, department_name: str, project_name: str, model_name: str):
        if not department_name.strip():
            return {"error": "Department name cannot be empty."}
        if not project_name.strip():
            return {"error": "Project name cannot be empty."}
        if not model_name.strip():
            return {"error": "Model name cannot be empty."}

        result = await self.projects_repo.delete_model(department_name, project_name, model_name)

        # Agent Wiki + ChromaDB 동기 삭제
        dept_slug  = _slugify(department_name)
        proj_slug  = _slugify(project_name)
        model_slug = _slugify(model_name)
        doc_id = f"{dept_slug}-{proj_slug}-{model_slug}"
        await self.agent_service.delete_model(doc_id)

        return {"message": f"Model '{model_name}' deleted successfully."}
    
    
