import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from config.settings import (
    AUDIT_RETENTION_DAYS,
    FILE_SIGNED_URL_SECONDS,
    FILE_SIGNING_SECRET,
    GRIDFS_BUCKET,
)


class FileAccessError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class FileAccessService:
    def __init__(self, database, clock=lambda: datetime.now(timezone.utc)):
        self.db = database
        self.files = database["medical_files"]
        self.analyses = database["analyses"]
        self.access_logs = database["access_logs"]
        self.bucket = AsyncIOMotorGridFSBucket(database, bucket_name=GRIDFS_BUCKET)
        self.clock = clock

    @staticmethod
    def _signature(file_id: str, expires: int) -> str:
        message = f"{file_id}:{expires}".encode()
        return hmac.new(
            FILE_SIGNING_SECRET.encode(),
            message,
            hashlib.sha256,
        ).hexdigest()

    def verify(self, file_id: str, expires: int, signature: str) -> None:
        now = int(self.clock().timestamp())
        if expires < now:
            raise FileAccessError(401, "파일 URL이 만료되었습니다.")
        expected = self._signature(file_id, expires)
        if not hmac.compare_digest(expected, signature):
            raise FileAccessError(401, "유효하지 않은 파일 서명입니다.")

    async def issue_analysis_urls(self, analysis_id: str, doctor: dict) -> dict:
        analysis = await self.analyses.find_one({
            "analysis_id": analysis_id,
            "hospital_id": doctor["hospital_id"],
        })
        if not analysis:
            raise FileAccessError(404, "분석을 찾을 수 없습니다.")

        result_ids = analysis.get("result_file_ids") or {}
        all_ids = [
            file_id
            for role in ("base", "heat", "box")
            for file_id in result_ids.get(role, [])
        ]
        if all_ids:
            active_count = await self.files.count_documents({
                "file_id": {"$in": all_ids},
                "hospital_id": doctor["hospital_id"],
                "analysis_id": analysis_id,
                "status": "active",
            })
            if active_count != len(set(all_ids)):
                raise FileAccessError(409, "분석 파일 메타데이터가 일치하지 않습니다.")

        expires_at = self.clock() + timedelta(seconds=FILE_SIGNED_URL_SECONDS)
        expires = int(expires_at.timestamp())

        def signed(file_id: str) -> str:
            signature = self._signature(file_id, expires)
            return (
                f"/files/{quote(file_id, safe='')}?"
                f"exp={expires}&sig={signature}"
            )

        images = {
            role: [signed(file_id) for file_id in result_ids.get(role, [])]
            for role in ("base", "heat", "box")
        }
        now = self.clock()
        await self.access_logs.insert_one({
            "doctor_id": doctor["_id"],
            "hospital_id": doctor["hospital_id"],
            "action": "issue_analysis_file_urls",
            "resource_type": "analysis",
            "resource_id": analysis_id,
            "patient_id": analysis["patient_id"],
            "visit_id": analysis["visit_id"],
            "file_count": len(all_ids),
            "at": now,
            "expires_at": now + timedelta(days=AUDIT_RETENTION_DAYS),
        })
        return {
            "images": images,
            "image_file_ids": {
                role: list(result_ids.get(role, []))
                for role in ("base", "heat", "box")
            },
            "expires_at": expires_at.isoformat(),
        }

    async def get_metadata(self, file_id: str, doctor: dict) -> dict:
        metadata = await self.files.find_one({
            "file_id": file_id,
            "hospital_id": doctor["hospital_id"],
            "status": "active",
        })
        if not metadata:
            raise FileAccessError(404, "파일을 찾을 수 없습니다.")
        now = self.clock()
        await self.access_logs.insert_one({
            "doctor_id": doctor["_id"],
            "hospital_id": doctor["hospital_id"],
            "action": "read_medical_file_metadata",
            "resource_type": "file",
            "resource_id": file_id,
            "patient_id": metadata["patient_id"],
            "visit_id": metadata["visit_id"],
            "analysis_id": metadata["analysis_id"],
            "at": now,
            "expires_at": now + timedelta(days=AUDIT_RETENTION_DAYS),
        })
        return {
            "file_id": metadata["file_id"],
            "patient_id": metadata["patient_id"],
            "visit_id": metadata["visit_id"],
            "analysis_id": metadata["analysis_id"],
            "kind": metadata["kind"],
            "role": metadata["role"],
            "original_filename": metadata["original_filename"],
            "content_type": metadata["content_type"],
            "extension": metadata["extension"],
            "size_bytes": metadata["size_bytes"],
            "dicom_metadata": metadata.get("dicom_metadata"),
        }

    async def open_file(self, file_id: str, expires: int, signature: str):
        self.verify(file_id, expires, signature)
        metadata = await self.files.find_one({"file_id": file_id, "status": "active"})
        if not metadata:
            raise FileAccessError(404, "파일을 찾을 수 없습니다.")
        try:
            stream = await self.bucket.open_download_stream(metadata["gridfs_id"])
        except Exception as exc:
            raise FileAccessError(404, "파일 본문을 찾을 수 없습니다.") from exc
        return metadata, stream
