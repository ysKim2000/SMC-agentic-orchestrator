import hashlib
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from config.settings import GRIDFS_BUCKET, MAX_UPLOAD_BYTES
from services.attachments import extract_metadata_path


ALLOWED_EXTENSIONS = {
    ".dcm": ("DICOM", "dcm"),
    ".dicom": ("DICOM", "dicom"),
    ".nii": ("NIfTI", "nii"),
    ".csv": ("CSV", "csv"),
    ".png": ("이미지", "png"),
    ".jpg": ("이미지", "jpg"),
    ".jpeg": ("이미지", "jpeg"),
}
EXAM_TYPE_ORDER = ("DICOM", "NIfTI", "CSV", "이미지")
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


class MedicalFileError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def classify_filename(filename: str) -> tuple[str, str]:
    lower = filename.lower()
    if lower.endswith(".nii.gz"):
        return "NIfTI", "nii.gz"
    suffix = Path(lower).suffix
    result = ALLOWED_EXTENSIONS.get(suffix)
    if not result:
        raise MedicalFileError(415, f"지원하지 않는 파일 형식: {filename}")
    return result


def derive_exam_type(classifications: list[str]) -> str:
    found = set(classifications)
    return " · ".join(value for value in EXAM_TYPE_ORDER if value in found)


class MedicalFileService:
    def __init__(self, database):
        self.database = database
        self.bucket = AsyncIOMotorGridFSBucket(database, bucket_name=GRIDFS_BUCKET)
        self.collection = database["medical_files"]

    async def stage(self, files: list[UploadFile], doctor_id) -> tuple[list[dict], str]:
        staged: list[dict] = []
        classifications: list[str] = []
        try:
            for upload in files:
                filename = upload.filename or "unnamed"
                classification, extension = classify_filename(filename)
                temp_path = None
                gridfs_id = None
                try:
                    digest = hashlib.sha256()
                    size_bytes = 0
                    suffix = f".{extension}"
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        suffix=suffix,
                        prefix="maple-upload-",
                        delete=False,
                    ) as temp:
                        temp_path = temp.name
                        while chunk := await upload.read(UPLOAD_READ_CHUNK_BYTES):
                            size_bytes += len(chunk)
                            if size_bytes > MAX_UPLOAD_BYTES:
                                limit_mib = MAX_UPLOAD_BYTES // (1024 * 1024)
                                raise MedicalFileError(
                                    413,
                                    f"파일 크기 제한 초과(최대 {limit_mib} MiB): {filename}",
                                )
                            digest.update(chunk)
                            temp.write(chunk)

                    if not size_bytes:
                        raise MedicalFileError(
                            422, f"빈 파일은 등록할 수 없습니다: {filename}"
                        )

                    now = datetime.now(timezone.utc)
                    file_id = f"F-{uuid.uuid4()}"
                    dicom_metadata = None
                    if classification == "DICOM":
                        dicom_metadata = await extract_metadata_path(
                            filename,
                            temp_path,
                            upload.content_type or "application/dicom",
                        )

                    with open(temp_path, "rb") as source:
                        gridfs_id = await self.bucket.upload_from_stream(
                            filename,
                            source,
                            metadata={"file_id": file_id, "status": "staging"},
                        )
                    document = {
                        "file_id": file_id,
                        "gridfs_id": gridfs_id,
                        "hospital_id": None,
                        "patient_id": None,
                        "visit_id": None,
                        "analysis_id": None,
                        "kind": "input",
                        "role": "source",
                        "slice_index": None,
                        "original_filename": filename,
                        "content_type": upload.content_type
                        or "application/octet-stream",
                        "extension": extension,
                        "dicom_metadata": dicom_metadata,
                        "size_bytes": size_bytes,
                        "sha256": digest.hexdigest(),
                        "status": "staging",
                        "created_by_doctor_id": doctor_id,
                        "created_at": now,
                    }
                    try:
                        result = await self.collection.insert_one(document)
                    except Exception:
                        await self.bucket.delete(gridfs_id)
                        gridfs_id = None
                        raise
                    document["_id"] = result.inserted_id
                    staged.append(document)
                    classifications.append(classification)
                finally:
                    if temp_path:
                        try:
                            os.unlink(temp_path)
                        except FileNotFoundError:
                            pass
        except Exception:
            await self.mark_orphaned(staged)
            raise
        finally:
            for upload in files:
                await upload.close()

        return staged, derive_exam_type(classifications)

    async def activate(
        self,
        staged: list[dict],
        *,
        hospital_id: str,
        patient_id: str,
        visit_id: str,
        analysis_id: str,
        session,
    ) -> None:
        if not staged:
            return
        await self.collection.update_many(
            {"_id": {"$in": [item["_id"] for item in staged]}, "status": "staging"},
            {
                "$set": {
                    "hospital_id": hospital_id,
                    "patient_id": patient_id,
                    "visit_id": visit_id,
                    "analysis_id": analysis_id,
                    "status": "active",
                }
            },
            session=session,
        )

    async def mark_orphaned(self, staged: list[dict]) -> None:
        if not staged:
            return
        await self.collection.update_many(
            {"_id": {"$in": [item["_id"] for item in staged]}},
            {"$set": {"status": "orphaned"}},
        )

    async def store_derived(
        self,
        *,
        paths_and_roles: list[tuple[Path, str]],
        analysis: dict,
    ) -> dict[str, list[str]]:
        result_ids: dict[str, list[str]] = {"base": [], "heat": [], "box": []}
        role_indexes = {"base": 0, "heat": 0, "box": 0}
        try:
            for path, role in paths_and_roles:
                slice_index = role_indexes[role]
                role_indexes[role] += 1
                content = path.read_bytes()
                file_id = f"F-{uuid.uuid4()}"
                gridfs_id = await self.bucket.upload_from_stream(
                    path.name,
                    content,
                    metadata={"file_id": file_id, "status": "active"},
                )
                now = datetime.now(timezone.utc)
                await self.collection.insert_one({
                    "file_id": file_id,
                    "gridfs_id": gridfs_id,
                    "hospital_id": analysis["hospital_id"],
                    "patient_id": analysis["patient_id"],
                    "visit_id": analysis["visit_id"],
                    "analysis_id": analysis["analysis_id"],
                    "kind": "derived_image",
                    "role": role,
                    "slice_index": slice_index,
                    "original_filename": path.name,
                    "content_type": "image/png",
                    "extension": "png",
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "status": "active",
                    "created_by_doctor_id": analysis["requested_by_doctor_id"],
                    "created_at": now,
                })
                result_ids[role].append(file_id)
        except Exception:
            created_ids = [
                file_id for values in result_ids.values() for file_id in values
            ]
            if created_ids:
                await self.collection.update_many(
                    {"file_id": {"$in": created_ids}},
                    {"$set": {"status": "orphaned"}},
                )
            raise
        return result_ids
