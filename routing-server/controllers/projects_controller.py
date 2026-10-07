import logging
from fastapi import APIRouter, Depends, HTTPException
from services.projects_service import ProjectsService
from dependencies import get_projects_service

router = APIRouter()
logger = logging.getLogger("maple.projects.controller")

@router.get("/")
async def get_departments(service: ProjectsService = Depends(get_projects_service)):
    try:
        departments = await service.get_departments()
        return {"departments": departments}
    except Exception as e:
        logger.error(f"get_departments 오류: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{department_name}")
async def get_projects_by_department(department_name: str, service: ProjectsService = Depends(get_projects_service)):
    projects_names, projects = await service.get_projects_by_department(department_name)
    if not projects:
        raise HTTPException(status_code=404, detail="Department or Projects not found")
    return {"projects": projects}


@router.get("/{department_name}/{project_name}")
async def get_models_by_projects_by_department(department_name: str, project_name: str, service: ProjectsService = Depends(get_projects_service)):
    models_names, models = await service.get_models_by_projects_by_department(department_name, project_name)
    if not models:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"models": models}


@router.get("/{department_name}/{project_name}/{model_number}/data")
async def get_data_by_models_by_projects_by_department(department_name: str, project_name: str, model_number: str, service: ProjectsService = Depends(get_projects_service)):
    required_data = await service.get_data_by_model(department_name, project_name)
    if not required_data:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"required_data": required_data}
