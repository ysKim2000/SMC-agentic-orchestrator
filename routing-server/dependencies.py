from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

from config.database import get_database, MAIN_DOCUMENT_ID
from repositories.doctors_repository import DoctorsRepository
from repositories.projects_repository import ProjectsRepository
from repositories.results_repository import ResultsRepository
from repositories.sessions_repository import SessionsRepository
from services.projects_service import ProjectsService
from services.inference_service import InferenceService
from services.agent_service import AgentService
from services.admin_service import AdminService
from services.auth_service import AuthService, AuthServiceError
from services.clinical_service import ClinicalService
from services.analysis_query_service import AnalysisQueryService
from services.file_access_service import FileAccessService
from services.clinical_chat_service import ClinicalChatService
from services.clinical_note_service import ClinicalNoteService
from services.pipeline_service import PipelineService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_auth_service():
    db = get_database()
    return AuthService(
        DoctorsRepository(db),
        SessionsRepository(db),
    )


def get_clinical_service():
    return ClinicalService(get_database())


def get_analysis_query_service():
    return AnalysisQueryService(get_database())


def get_file_access_service():
    return FileAccessService(get_database())


def get_clinical_chat_service():
    return ClinicalChatService(get_database())


def get_clinical_note_service():
    return ClinicalNoteService(get_database())


async def get_current_doctor(
    token: str = Depends(oauth2_scheme),
    service: AuthService = Depends(get_auth_service),
):
    try:
        return await service.authenticate_access_token(token)
    except AuthServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.detail,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def require_doctor(doctor=Depends(get_current_doctor)):
    roles = set(doctor.get("roles") or [])
    if not roles.intersection({"doctor", "admin"}):
        raise HTTPException(status_code=403, detail="doctor role required")
    return doctor


async def require_admin(doctor=Depends(get_current_doctor)):
    if "admin" not in set(doctor.get("roles") or []):
        raise HTTPException(status_code=403, detail="admin role required")
    return doctor


# 프로젝트 레포지토리
def get_projects_repository():
    db = get_database()
    return ProjectsRepository(db, MAIN_DOCUMENT_ID)

# 프로젝트 서비스
def get_projects_service():
    projects_repo = get_projects_repository()
    return ProjectsService(projects_repo)

# 결과 레포지토리
def get_inference_repository():
    db = get_database()
    return ResultsRepository(db)

# 인퍼런스 서비스 (projects_repo와 results_repo 둘 다 주입)
def get_inference_service():
    inference_repo = get_inference_repository()
    projects_repo  = get_projects_repository()
    return InferenceService(inference_repo, projects_repo)

# AI Agent 서비스
def get_agent_service():
    return AgentService()

# 관리자 서비스 (AgentService 주입)
def get_admin_service():
    projects_repo = get_projects_repository()
    agent_service = get_agent_service()
    return AdminService(projects_repo, agent_service)

# 파이프라인 서비스
def get_pipeline_service():
    projects_repo  = get_projects_repository()
    inference_repo = get_inference_repository()
    return PipelineService(projects_repo, inference_repo)
