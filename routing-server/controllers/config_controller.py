from fastapi import APIRouter, Depends

from config.settings import MAX_UPLOAD_BYTES
from dependencies import require_doctor


router = APIRouter()


@router.get("/client")
async def client_config(_doctor=Depends(require_doctor)):
    """로그인 후 클라이언트가 사용할 서버 정책."""
    return {
        "upload": {
            "max_file_bytes": MAX_UPLOAD_BYTES,
            "max_file_mib": MAX_UPLOAD_BYTES // (1024 * 1024),
            "allowed_extensions": [
                ".dcm",
                ".dicom",
                ".nii",
                ".nii.gz",
                ".csv",
                ".png",
                ".jpg",
                ".jpeg",
            ],
        }
    }
