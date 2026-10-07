from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, Form, File
from starlette.datastructures import UploadFile as StarletteUploadFile

from services.admin_service import AdminService
from services.projects_service import ProjectsService
from dependencies import get_projects_service, get_admin_service
from models.schemas import DepartmentCreate, DepartmentUpdate, ProjectCreate, ProjectUpdate, ModelCreate, ModelUpdate, ModelDelete, RiskPolicy

import logging
from typing import List

logger = logging.getLogger("maple.admin.controller")

router = APIRouter(tags=["admin"])

# 부서 목록 조회
@router.get("/departments")
async def get_departments(service: ProjectsService = Depends(get_projects_service)):
    try:
        departments = await service.get_departments()
        return {"departments": departments}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

# 부서 추가
@router.post("/departments")
async def create_department(
    department: DepartmentCreate, 
    service: AdminService = Depends(get_admin_service)
):
    try:
        created_department = await service.create_department(department)
        return {"message": "Department created successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

# 부서 수정
@router.put("/departments/{department_name}")
async def update_department(
    department_name: str, 
    department: DepartmentUpdate, 
    service: AdminService = Depends(get_admin_service)
):
    updated_department = await service.update_department(department_name, department.new_name)
    if not updated_department:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"message": "Department updated successfully"}

# 부서 삭제 (소프트 삭제)
@router.delete("/departments/{department_name}")
async def delete_department(department_name: str, service: AdminService = Depends(get_admin_service)):
    success = await service.delete_department(department_name)
    if not success:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"message": f"Department '{department_name}' deleted successfully"}


# 특정 부서의 프로젝트 목록 조회
@router.get("/projects/{department_name}")
async def get_projects_by_department(department_name: str, service: AdminService = Depends(get_admin_service)):
    response = await service.get_projects_by_department(department_name)
    if "error" in response:
        raise HTTPException(status_code=404, detail=response["error"])
    return response

# 프로젝트 추가
@router.post("/projects/{department_name}")
async def create_project(department_name: str, project: ProjectCreate, service: AdminService = Depends(get_admin_service)):
    response = await service.create_project(department_name, project.project_name)
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])
    return response

# 프로젝트 수정
@router.put("/projects/{department_name}/{old_project_name}")
async def update_project(department_name: str, old_project_name: str, project: ProjectUpdate, service: AdminService = Depends(get_admin_service)):
    response = await service.update_project(department_name, old_project_name, project.new_name)
    if "error" in response:
        raise HTTPException(status_code=400, detail=response["error"])
    return response

# 프로젝트 삭제
@router.delete("/projects/{department_name}/{project_name}")
async def delete_project(department_name: str, project_name: str, service: AdminService = Depends(get_admin_service)):
    success = await service.delete_project(department_name, project_name)
    if not success:
        raise HTTPException(status_code=404, detail=f"Project '{project_name}' not found")
    return {"message": f"Project '{project_name}' deleted successfully"}


# --- 모델 (Model) ---
@router.get("/models/{department_name}/{project_name}") # model list-up
async def get_models_by_department_and_project(department_name: str, project_name: str, service: AdminService = Depends(get_admin_service)):
    result = await service.get_models_by_department_and_project(department_name, project_name)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/models/{department_name}/{project_name}")
async def create_model(
    request: Request,
    department_name: str,
    project_name: str,
    model_name: str = Form(...),
    model_description: str = Form(...),
    required_data: List[str] = Form(...),
    task_type: str = Form(...),
    result_type: str = Form(...),
    output_image_role: str = Form(default=None),  # "bbox_overlay" | "gradcam_overlay" | "segmentation_overlay" | None
    service: AdminService = Depends(get_admin_service)
):
    try:
        form = await request.form()
        model_path_dict = {}
        inference_script = None
        requirements_file = None

        for key, value in form.multi_items():
            if isinstance(value, (StarletteUploadFile, UploadFile)):
                if key == "inference_script":
                    inference_script = value
                elif key == "requirements_file":
                    # 이미 위 파라미터로 받으니 무시해도 되고, 여기서 받아도 됨
                    requirements_file = value
                else:
                    model_path_dict[key] = value

        result = await service.create_model(
            department_name=department_name,
            project_name=project_name,
            model_name=model_name,
            model_description=model_description,
            model_path_dict=model_path_dict,
            required_data=required_data,
            task_type=task_type,
            result_type=result_type,
            output_image_role=output_image_role or None,
            inference_script=inference_script,
            requirements_file=requirements_file,
        )

        return result

    except Exception as e:
        logger.error(f"모델 생성 중 예외 발생: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.put("/models/{model_name}") # update
async def update_model(model_name: str, model: ModelUpdate, service: AdminService = Depends(get_admin_service)):
    try:
        result = await service.update_model(
            department_name=model.department_name,
            project_name=model.project_name,
            old_model_name=model_name,
            new_model_name=model.new_name
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")

@router.delete("/models/{model_name}") # delete 
async def delete_model(model_name: str, model: ModelDelete, service: AdminService = Depends(get_admin_service)):
    try:
        result = await service.delete_model(model.department_name, model.project_name, model_name)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except Exception as e:
        logger.error(f"모델 삭제 중 예외 발생: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.put("/models/{department_name}/{project_name}/risk-policy")
async def set_model_risk_policy(
    department_name: str,
    project_name: str,
    policy: RiskPolicy,
    service: AdminService = Depends(get_admin_service),
):
    result = await service.set_risk_policy(
        department_name,
        project_name,
        policy.model_dump(),
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
