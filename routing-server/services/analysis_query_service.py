from datetime import datetime, timezone

from services.clinical_service import ClinicalServiceError, _doctor_summary
from services.file_access_service import FileAccessService


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class AnalysisQueryService:
    def __init__(self, database):
        self.db = database
        self.analyses = database["analyses"]
        self.visits = database["visits"]
        self.patients = database["patients"]
        self.doctors = database["doctors"]
        self.file_access = FileAccessService(database)

    async def _dicom_metadata(self, analysis: dict | None) -> list[dict]:
        if not analysis:
            return []
        files = await self.db["medical_files"].find({
            "file_id": {"$in": analysis.get("input_file_ids") or []},
            "analysis_id": analysis["analysis_id"],
            "status": "active",
            "dicom_metadata": {"$ne": None},
        }).to_list(None)
        by_id = {item["file_id"]: item for item in files}
        return [
            {
                "file_id": file_id,
                "original_filename": by_id[file_id]["original_filename"],
                "metadata": by_id[file_id].get("dicom_metadata") or {},
            }
            for file_id in analysis.get("input_file_ids") or []
            if file_id in by_id
        ]

    async def _queue_position(self, analysis: dict) -> int | None:
        if analysis["status"] != "queued":
            return None
        earlier = {
            "requested_by_doctor_id": analysis["requested_by_doctor_id"],
            "status": "queued",
            "$or": [
                {"scheduled_at": {"$lt": analysis["scheduled_at"]}},
                {
                    "scheduled_at": analysis["scheduled_at"],
                    "queued_at": {"$lt": analysis["queued_at"]},
                },
                {
                    "scheduled_at": analysis["scheduled_at"],
                    "queued_at": analysis["queued_at"],
                    "_id": {"$lt": analysis["_id"]},
                },
            ],
        }
        return await self.analyses.count_documents(earlier) + 1

    async def analysis_response(
        self,
        analysis: dict,
        doctor: dict,
        *,
        include_urls: bool = True,
    ) -> dict:
        empty_files = {"base": [], "heat": [], "box": []}
        access = {
            "images": empty_files,
            "image_file_ids": {
                role: list((analysis.get("result_file_ids") or {}).get(role, []))
                for role in empty_files
            },
            "expires_at": None,
        }
        has_result_files = any(access["image_file_ids"].values())
        if include_urls and has_result_files:
            access = await self.file_access.issue_analysis_urls(
                analysis["analysis_id"], doctor
            )
        dicom_metadata = await self._dicom_metadata(analysis)
        return {
            "analysis_id": analysis["analysis_id"],
            "patient_id": analysis["patient_id"],
            "visit_id": analysis["visit_id"],
            "appointment_id": analysis["appointment_id"],
            "status": analysis["status"],
            "queue_position": await self._queue_position(analysis),
            "attempt": analysis.get("attempt", 0),
            "model_name": analysis.get("model_name"),
            "risk_tier": analysis.get("risk_tier"),
            "risk_status": analysis.get("risk_status", "unavailable"),
            "confidence": analysis.get("confidence"),
            "finding": analysis.get("finding"),
            "interpretation": analysis.get("interpretation"),
            "recommendation": analysis.get("recommendation"),
            "predictions": analysis.get("predictions"),
            "error": analysis.get("error"),
            **access,
            "dicom": dicom_metadata,
            "dicom_metadata": dicom_metadata,
            "created_at": _iso(analysis["created_at"]),
            "started_at": _iso(analysis.get("started_at")),
            "completed_at": _iso(analysis.get("completed_at")),
        }

    async def get_analysis(self, analysis_id: str, doctor: dict) -> dict:
        analysis = await self.analyses.find_one({
            "analysis_id": analysis_id,
            "hospital_id": doctor["hospital_id"],
        })
        if not analysis:
            raise ClinicalServiceError(404, "분석을 찾을 수 없습니다.")
        return await self.analysis_response(analysis, doctor)

    async def visit_analyses(self, visit_id: str, doctor: dict) -> list[dict]:
        visit = await self.visits.find_one({
            "visit_id": visit_id,
            "hospital_id": doctor["hospital_id"],
        })
        if not visit:
            raise ClinicalServiceError(404, "방문을 찾을 수 없습니다.")
        analyses = await self.analyses.find(
            {"visit_id": visit_id, "hospital_id": doctor["hospital_id"]}
        ).sort("created_at", -1).to_list(None)
        return [
            await self.analysis_response(item, doctor)
            for item in analyses
        ]

    async def patient_visits(self, patient_id: str, doctor: dict) -> list[dict]:
        patient = await self.patients.find_one({
            "patient_id": patient_id,
            "hospital_id": doctor["hospital_id"],
            "is_archived": False,
        })
        if not patient:
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        visits = await self.visits.find(
            {"patient_id": patient_id, "hospital_id": doctor["hospital_id"]}
        ).sort([("visit_date", -1), ("created_at", -1)]).to_list(None)
        doctors = {
            item["_id"]: item
            async for item in self.doctors.find({
                "_id": {"$in": list({visit["doctor_id"] for visit in visits})}
            })
        }
        output = []
        for visit in visits:
            analysis = await self.analyses.find_one(
                {"visit_id": visit["visit_id"], "status": {"$ne": "superseded"}},
                sort=[("created_at", -1)],
            )
            dicom_metadata = await self._dicom_metadata(analysis)
            output.append({
                "visit_id": visit["visit_id"],
                "appointment_id": visit["appointment_id"],
                "visit_date": visit["visit_date"],
                "exam_type": visit.get("exam_type", ""),
                "status": visit["status"],
                "doctor": _doctor_summary(doctors[visit["doctor_id"]]),
                "latest_analysis_id": analysis["analysis_id"] if analysis else None,
                "analysis_status": analysis["status"] if analysis else "waiting_for_files",
                "risk_tier": analysis.get("risk_tier") if analysis else None,
                "dicom": dicom_metadata,
                "dicom_metadata": dicom_metadata,
            })
        return output

    async def visit_detail(
        self,
        patient_id: str,
        visit_id: str,
        doctor: dict,
    ) -> dict:
        visit = await self.visits.find_one({
            "visit_id": visit_id,
            "patient_id": patient_id,
            "hospital_id": doctor["hospital_id"],
        })
        if not visit:
            raise ClinicalServiceError(404, "방문을 찾을 수 없습니다.")
        visit_doctor = await self.doctors.find_one({"_id": visit["doctor_id"]})
        analyses = await self.visit_analyses(visit_id, doctor)
        return {
            "visit_id": visit_id,
            "appointment_id": visit["appointment_id"],
            "patient_id": patient_id,
            "visit_date": visit["visit_date"],
            "exam_type": visit.get("exam_type", ""),
            "status": visit["status"],
            "doctor": _doctor_summary(visit_doctor),
            "analyses": analyses,
        }
