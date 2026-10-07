from fastapi import APIRouter, Depends
from controllers.auth_controller import router as auth_router
from controllers.projects_controller import router as projects_router
from controllers.inference_controller import router as inference_router
from controllers.admin_controller import router as admin_router
from controllers.pipeline_controller import router as pipeline_router
from controllers.clinical_controller import (
    appointments_router,
    patients_router,
)
from controllers.analysis_controller import (
    analyses_router,
    files_router,
    patient_visits_router,
    visits_router,
)
from controllers.chat_controller import router as chat_router
from controllers.note_controller import router as note_router
from controllers.config_controller import router as config_router
from dependencies import require_admin, require_doctor

router = APIRouter()
router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(config_router, prefix="/config", tags=["config"])
router.include_router(patients_router, prefix="/patients", tags=["patients"])
router.include_router(note_router, prefix="/patients", tags=["notes"])
router.include_router(
    patient_visits_router,
    prefix="/patients",
    tags=["visits"],
)
router.include_router(
    appointments_router,
    prefix="/appointments",
    tags=["appointments"],
)
router.include_router(visits_router, prefix="/visits", tags=["visits"])
router.include_router(chat_router, prefix="/visits", tags=["chat"])
router.include_router(analyses_router, prefix="/analyses", tags=["analyses"])
router.include_router(files_router, prefix="/files", tags=["files"])
router.include_router(
    projects_router,
    prefix="/projects",
    tags=["projects"],
    dependencies=[Depends(require_doctor)],
)
router.include_router(
    inference_router,
    prefix="/inference",
    tags=["inference"],
    dependencies=[Depends(require_doctor)],
)
router.include_router(
    admin_router,
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
)
router.include_router(
    pipeline_router,
    prefix="/pipeline",
    tags=["pipeline"],
    dependencies=[Depends(require_doctor)],
)
