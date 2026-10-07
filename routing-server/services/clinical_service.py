import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config.settings import APP_TIMEZONE, HOSPITAL_ID
from models.clinical_schemas import (
    AppointmentForm,
    AppointmentPatch,
    NewPatientForm,
)
from repositories.clinical_repository import ClinicalRepository
from services.medical_file_service import MedicalFileError, MedicalFileService


class ClinicalServiceError(Exception):
    def __init__(self, status_code: int, detail: str, extra: dict | None = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_name(value: str) -> str:
    return "".join(value.split()).lower()


def _normalize_patient_id_query(value: str) -> str | None:
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if compact.startswith("PT"):
        compact = compact[2:]
    if not compact.isdigit():
        return None
    return f"PT-{int(compact)}"


def _doctor_summary(doctor: dict | None) -> dict | None:
    if not doctor:
        return None
    return {"employee_id": doctor["employee_id"], "name": doctor["name"]}


class ClinicalService:
    def __init__(self, database, clock=_utcnow):
        self.db = database
        self.repository = ClinicalRepository(database)
        self.files = MedicalFileService(database)
        self.clock = clock
        self.timezone = ZoneInfo(APP_TIMEZONE)

    async def _dicom_metadata(self, analysis: dict | None) -> list[dict]:
        if not analysis:
            return []
        files = await self.repository.medical_files.find({
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

    def _scheduled_at(self, scheduled_date: date, scheduled_time: time) -> datetime:
        if scheduled_time.second or scheduled_time.microsecond:
            raise ClinicalServiceError(422, "예약 시각의 초 단위는 허용하지 않습니다.")
        if scheduled_time.minute % 10:
            raise ClinicalServiceError(422, "예약 시각은 10분 단위여야 합니다.")
        if scheduled_time < time(8, 0) or scheduled_time > time(17, 0):
            raise ClinicalServiceError(422, "예약 가능 시간은 08:00부터 17:00까지입니다.")

        local_value = datetime.combine(
            scheduled_date,
            scheduled_time,
            tzinfo=self.timezone,
        )
        return local_value.astimezone(timezone.utc)

    async def _next_ids(self, scheduled_date: date, session) -> tuple[str, str, str]:
        date_key = scheduled_date.strftime("%Y%m%d")
        appointment_seq = await self.repository.next_sequence(
            f"appointment:{date_key}", session=session
        )
        visit_seq = await self.repository.next_sequence(
            f"visit:{date_key}", session=session
        )
        analysis_seq = await self.repository.next_sequence(
            f"analysis:{date_key}", session=session
        )
        return (
            f"A-{date_key}-{appointment_seq:04d}",
            f"V-{date_key}-{visit_seq:04d}",
            f"AN-{date_key}-{analysis_seq:04d}",
        )

    async def _queue_position(self, analysis: dict, session=None) -> int:
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
        return await self.repository.analyses.count_documents(
            earlier, session=session
        ) + 1

    async def _register(
        self,
        *,
        doctor: dict,
        appointment_form: AppointmentForm,
        staged_files: list[dict],
        exam_type: str,
        new_patient: NewPatientForm | None = None,
        patient_id: str | None = None,
    ) -> dict:
        scheduled_at = self._scheduled_at(
            appointment_form.appt_date,
            appointment_form.appt_time,
        )
        now = self.clock()
        analysis_document = None

        try:
            async with await self.db.client.start_session() as session:
                async with session.start_transaction():
                    if new_patient is not None:
                        patient_seq = await self.repository.next_sequence(
                            "patient_id",
                            session=session,
                            initial_value=1000,
                        )
                        patient_id = f"PT-{patient_seq}"
                        await self.repository.patients.insert_one(
                            {
                                "patient_id": patient_id,
                                "hospital_id": HOSPITAL_ID,
                                "name": new_patient.name,
                                "search_name": _normalize_name(new_patient.name),
                                "gender": new_patient.gender,
                                "birth_date": new_patient.birth_date.isoformat(),
                                "assigned_doctor_id": doctor["_id"],
                                "created_by_doctor_id": doctor["_id"],
                                "care_status": "관찰중",
                                "is_archived": False,
                                "created_at": now,
                                "updated_at": now,
                            },
                            session=session,
                        )
                    else:
                        patient = await self.repository.get_patient(
                            patient_id, HOSPITAL_ID, session=session
                        )
                        if not patient:
                            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")

                    appointment_id, visit_id, analysis_id = await self._next_ids(
                        appointment_form.appt_date,
                        session,
                    )
                    appointment_document = {
                        "appointment_id": appointment_id,
                        "hospital_id": HOSPITAL_ID,
                        "patient_id": patient_id,
                        "doctor_id": doctor["_id"],
                        "scheduled_at": scheduled_at,
                        "scheduled_date": appointment_form.appt_date.isoformat(),
                        "scheduled_time": appointment_form.appt_time.strftime("%H:%M"),
                        "exam_type": exam_type,
                        "status": "scheduled",
                        "slot_claimed": True,
                        "created_at": now,
                        "updated_at": now,
                    }
                    await self.repository.appointments.insert_one(
                        appointment_document, session=session
                    )
                    await self.repository.visits.insert_one(
                        {
                            "visit_id": visit_id,
                            "hospital_id": HOSPITAL_ID,
                            "patient_id": patient_id,
                            "appointment_id": appointment_id,
                            "doctor_id": doctor["_id"],
                            "visit_date": appointment_form.appt_date.isoformat(),
                            "exam_type": exam_type,
                            "modalities": [],
                            "status": "open",
                            "created_at": now,
                            "updated_at": now,
                            "completed_at": None,
                        },
                        session=session,
                    )
                    if appointment_form.note:
                        await self.repository.notes.insert_one(
                            {
                                "note_id": str(ObjectId()),
                                "hospital_id": HOSPITAL_ID,
                                "patient_id": patient_id,
                                "visit_id": visit_id,
                                "author_id": doctor["_id"],
                                "author_name": f"{doctor['name']} · 접수",
                                "source": "registration",
                                "text": appointment_form.note,
                                "created_at": now,
                                "updated_at": now,
                            },
                            session=session,
                        )

                    if staged_files:
                        analysis_document = {
                            "_id": ObjectId(),
                            "analysis_id": analysis_id,
                            "hospital_id": HOSPITAL_ID,
                            "patient_id": patient_id,
                            "visit_id": visit_id,
                            "appointment_id": appointment_id,
                            "requested_by_doctor_id": doctor["_id"],
                            "visit_doctor_id": doctor["_id"],
                            "scheduled_at": scheduled_at,
                            "queued_at": now,
                            "started_at": None,
                            "completed_at": None,
                            "cancel_requested_at": None,
                            "status": "queued",
                            "attempt": 0,
                            "worker_id": None,
                            "lease_expires_at": None,
                            "mode": "auto",
                            "query": appointment_form.note or "업로드한 영상을 분석해줘.",
                            "department": None,
                            "project": None,
                            "model_name": None,
                            "risk_tier": None,
                            "risk_status": "pending",
                            "confidence": None,
                            "finding": None,
                            "interpretation": None,
                            "recommendation": None,
                            "predictions": None,
                            "input_file_ids": [item["file_id"] for item in staged_files],
                            "result_file_ids": {"base": [], "heat": [], "box": []},
                            "error": None,
                            "created_at": now,
                            "updated_at": now,
                            "superseded_at": None,
                        }
                        await self.repository.analyses.insert_one(
                            analysis_document, session=session
                        )
                        await self.files.activate(
                            staged_files,
                            hospital_id=HOSPITAL_ID,
                            patient_id=patient_id,
                            visit_id=visit_id,
                            analysis_id=analysis_id,
                            session=session,
                        )
        except DuplicateKeyError as exc:
            await self.files.mark_orphaned(staged_files)
            if "uq_appointments_active_doctor_slot" in str(exc):
                raise ClinicalServiceError(
                    409,
                    "슬롯 충돌",
                    extra={
                        "conflict": {
                            "doctor": _doctor_summary(doctor),
                            "scheduled_at": scheduled_at.astimezone(
                                self.timezone
                            ).isoformat(),
                        }
                    },
                ) from exc
            raise ClinicalServiceError(409, "중복된 데이터입니다.") from exc
        except Exception:
            await self.files.mark_orphaned(staged_files)
            raise

        analysis_response = None
        if analysis_document:
            analysis_response = {
                "analysis_id": analysis_document["analysis_id"],
                "status": "queued",
                "queue_position": await self._queue_position(analysis_document),
            }
        return {
            "patient_id": patient_id,
            "appointment_id": appointment_id,
            "visit_id": visit_id,
            "analysis": analysis_response,
            "visit_status": "open",
            "analysis_status": "queued" if analysis_document else "waiting_for_files",
        }

    async def register_new(
        self,
        request: NewPatientForm,
        files,
        doctor: dict,
    ) -> dict:
        if request.birth_date > self.clock().astimezone(self.timezone).date():
            raise ClinicalServiceError(422, "생년월일은 미래일 수 없습니다.")
        self._scheduled_at(request.appt_date, request.appt_time)
        appointment = AppointmentForm(
            appt_date=request.appt_date,
            appt_time=request.appt_time,
            note=request.note,
        )
        staged, exam_type = await self.files.stage(files, doctor["_id"])
        return await self._register(
            doctor=doctor,
            appointment_form=appointment,
            staged_files=staged,
            exam_type=exam_type,
            new_patient=request,
        )

    async def register_revisit(
        self,
        patient_id: str,
        request: AppointmentForm,
        files,
        doctor: dict,
    ) -> dict:
        self._scheduled_at(request.appt_date, request.appt_time)
        staged, exam_type = await self.files.stage(files, doctor["_id"])
        return await self._register(
            doctor=doctor,
            appointment_form=request,
            staged_files=staged,
            exam_type=exam_type,
            patient_id=patient_id,
        )

    async def search_patients(self, query: str, doctor: dict) -> dict:
        query = query.strip()
        if not query:
            return {"results": []}

        patient_id = _normalize_patient_id_query(query)
        if patient_id:
            match = {"patient_id": patient_id}
        else:
            normalized = _normalize_name(query)
            match = {"search_name": {"$regex": f"^{re.escape(normalized)}"}}
        match.update({"hospital_id": doctor["hospital_id"], "is_archived": False})
        patients = await self.repository.patients.find(match).limit(20).to_list(20)

        results = []
        for patient in patients:
            visits = await self.repository.visits.find(
                {"patient_id": patient["patient_id"], "status": {"$ne": "cancelled"}}
            ).sort([("visit_date", -1), ("created_at", -1)]).to_list(None)
            results.append({
                "patient_id": patient["patient_id"],
                "name": patient["name"],
                "gender": patient["gender"],
                "birth_date": patient["birth_date"],
                "last_visit_date": visits[0]["visit_date"] if visits else None,
                "visit_count": len(visits),
            })
        return {"results": results}

    async def list_patients(self, selected_date: date, doctor: dict) -> dict:
        appointments = await self.repository.appointments.find({
            "hospital_id": doctor["hospital_id"],
            "scheduled_date": selected_date.isoformat(),
            "status": {"$ne": "cancelled"},
        }).sort("scheduled_at", 1).to_list(None)
        patient_ids = [item["patient_id"] for item in appointments]
        patients = {
            item["patient_id"]: item
            for item in await self.repository.patients.find(
                {"patient_id": {"$in": patient_ids}}
            ).to_list(None)
        }
        doctor_ids = {
            value
            for item in appointments
            for value in (
                item["doctor_id"],
                patients.get(item["patient_id"], {}).get("assigned_doctor_id"),
            )
            if value
        }
        doctors = await self.repository.get_doctors(doctor_ids)
        output = []
        for appointment in appointments:
            patient = patients.get(appointment["patient_id"])
            if not patient:
                continue
            visit = await self.repository.visits.find_one(
                {"appointment_id": appointment["appointment_id"]}
            )
            analysis = None
            if visit:
                analysis = await self.repository.analyses.find_one(
                    {
                        "visit_id": visit["visit_id"],
                        "status": {"$ne": "superseded"},
                    },
                    sort=[("created_at", -1)],
                )
            visit_count = await self.repository.visits.count_documents({
                "patient_id": patient["patient_id"],
                "status": {"$ne": "cancelled"},
            })
            analysis_status = analysis["status"] if analysis else "waiting_for_files"
            queue_position = (
                await self._queue_position(analysis)
                if analysis and analysis["status"] == "queued"
                else None
            )
            dicom_metadata = await self._dicom_metadata(analysis)
            output.append({
                "patient_id": patient["patient_id"],
                "name": patient["name"],
                "gender": patient["gender"],
                "birth_date": patient["birth_date"],
                "appt_time": appointment["scheduled_time"],
                "exam_type": appointment.get("exam_type", ""),
                "care_status": patient["care_status"],
                "assigned_doctor": _doctor_summary(
                    doctors.get(patient.get("assigned_doctor_id"))
                ),
                "appointment_doctor": _doctor_summary(doctors[appointment["doctor_id"]]),
                "appointment_id": appointment["appointment_id"],
                "latest_visit_id": visit["visit_id"],
                "latest_analysis_id": analysis["analysis_id"] if analysis else None,
                "appointment_status": appointment["status"],
                "analysis_status": analysis_status,
                "queue_position": queue_position,
                "risk_tier": analysis.get("risk_tier") if analysis else None,
                "confidence": analysis.get("confidence") if analysis else None,
                "visit_count": visit_count,
                "dicom": dicom_metadata,
                "dicom_metadata": dicom_metadata,
            })
        return {"date": selected_date, "patients": output}

    async def get_patient_detail(self, patient_id: str, doctor: dict) -> dict:
        patient = await self.repository.get_patient(patient_id, doctor["hospital_id"])
        if not patient:
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        assigned = await self.repository.doctors.find_one(
            {"_id": patient.get("assigned_doctor_id")}
        )
        visit_count = await self.repository.visits.count_documents({
            "patient_id": patient_id,
            "status": {"$ne": "cancelled"},
        })
        latest_analysis = await self.repository.analyses.find_one(
            {
                "patient_id": patient_id,
                "status": {"$ne": "superseded"},
            },
            sort=[("created_at", -1)],
        )
        dicom_metadata = await self._dicom_metadata(latest_analysis)
        return {
            "patient_id": patient_id,
            "name": patient["name"],
            "gender": patient["gender"],
            "birth_date": patient["birth_date"],
            "care_status": patient["care_status"],
            "assigned_doctor": _doctor_summary(assigned),
            "visit_count": visit_count,
            "created_at": patient["created_at"].isoformat(),
            "dicom": dicom_metadata,
            "dicom_metadata": dicom_metadata,
        }

    async def update_patient(self, patient_id: str, care_status: str, doctor: dict) -> dict:
        result = await self.repository.patients.find_one_and_update(
            {
                "patient_id": patient_id,
                "hospital_id": doctor["hospital_id"],
                "is_archived": False,
            },
            {"$set": {"care_status": care_status, "updated_at": self.clock()}},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        return await self.get_patient_detail(patient_id, doctor)

    async def appointment_history(self, patient_id: str, doctor: dict) -> list[dict]:
        if not await self.repository.get_patient(patient_id, doctor["hospital_id"]):
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        appointments = await self.repository.appointments.find(
            {"patient_id": patient_id, "hospital_id": doctor["hospital_id"]}
        ).sort("scheduled_at", -1).to_list(None)
        doctors = await self.repository.get_doctors(
            {item["doctor_id"] for item in appointments}
        )
        output = []
        for appointment in appointments:
            visit = await self.repository.visits.find_one(
                {"appointment_id": appointment["appointment_id"]}
            )
            analysis = (
                await self.repository.analyses.find_one(
                    {"visit_id": visit["visit_id"], "status": {"$ne": "superseded"}},
                    sort=[("created_at", -1)],
                )
                if visit
                else None
            )
            dicom_metadata = await self._dicom_metadata(analysis)
            output.append({
                "appointment_id": appointment["appointment_id"],
                "scheduled_at": appointment["scheduled_at"].isoformat(),
                "scheduled_date": appointment["scheduled_date"],
                "scheduled_time": appointment["scheduled_time"],
                "exam_type": appointment.get("exam_type", ""),
                "status": appointment["status"],
                "doctor": _doctor_summary(doctors[appointment["doctor_id"]]),
                "visit_id": visit["visit_id"] if visit else None,
                "analysis_id": analysis["analysis_id"] if analysis else None,
                "analysis_status": analysis["status"] if analysis else "waiting_for_files",
                "dicom": dicom_metadata,
                "dicom_metadata": dicom_metadata,
            })
        return output

    async def patch_appointment(
        self,
        appointment_id: str,
        patch: AppointmentPatch,
        doctor: dict,
    ) -> dict:
        now = self.clock()
        cancelled_ids: list[str] = []
        running_ids: list[str] = []
        try:
            async with await self.db.client.start_session() as session:
                async with session.start_transaction():
                    appointment = await self.repository.get_appointment(
                        appointment_id, doctor["hospital_id"], session=session
                    )
                    if not appointment:
                        raise ClinicalServiceError(404, "예약을 찾을 수 없습니다.")

                    if patch.scheduled_date is not None:
                        if appointment["status"] != "scheduled":
                            raise ClinicalServiceError(
                                409, "시작하지 않은 예약만 시간을 변경할 수 있습니다."
                            )
                        scheduled_at = self._scheduled_at(
                            patch.scheduled_date, patch.scheduled_time
                        )
                        await self.repository.appointments.update_one(
                            {"_id": appointment["_id"]},
                            {"$set": {
                                "scheduled_at": scheduled_at,
                                "scheduled_date": patch.scheduled_date.isoformat(),
                                "scheduled_time": patch.scheduled_time.strftime("%H:%M"),
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        await self.repository.analyses.update_many(
                            {"appointment_id": appointment_id, "status": "queued"},
                            {"$set": {"scheduled_at": scheduled_at, "updated_at": now}},
                            session=session,
                        )
                        await self.repository.visits.update_one(
                            {"appointment_id": appointment_id},
                            {"$set": {
                                "visit_date": patch.scheduled_date.isoformat(),
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        appointment.update({
                            "scheduled_at": scheduled_at,
                            "scheduled_date": patch.scheduled_date.isoformat(),
                            "scheduled_time": patch.scheduled_time.strftime("%H:%M"),
                        })
                    elif patch.status == "completed":
                        if appointment["status"] == "cancelled":
                            raise ClinicalServiceError(409, "취소된 예약은 완료할 수 없습니다.")
                        await self.repository.appointments.update_one(
                            {"_id": appointment["_id"]},
                            {"$set": {
                                "status": "completed",
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        await self.repository.visits.update_one(
                            {"appointment_id": appointment_id},
                            {"$set": {
                                "status": "completed",
                                "completed_at": now,
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        appointment["status"] = "completed"
                    elif patch.status == "cancelled":
                        if appointment["status"] == "completed":
                            raise ClinicalServiceError(409, "완료된 예약은 취소할 수 없습니다.")
                        queued = await self.repository.analyses.find(
                            {"appointment_id": appointment_id, "status": "queued"},
                            session=session,
                        ).to_list(None)
                        analyzing = await self.repository.analyses.find(
                            {"appointment_id": appointment_id, "status": "analyzing"},
                            session=session,
                        ).to_list(None)
                        cancelled_ids = [item["analysis_id"] for item in queued]
                        running_ids = [item["analysis_id"] for item in analyzing]
                        await self.repository.appointments.update_one(
                            {"_id": appointment["_id"]},
                            {"$set": {
                                "status": "cancelled",
                                "slot_claimed": False,
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        await self.repository.visits.update_one(
                            {"appointment_id": appointment_id},
                            {"$set": {"status": "cancelled", "updated_at": now}},
                            session=session,
                        )
                        await self.repository.analyses.update_many(
                            {"appointment_id": appointment_id, "status": "queued"},
                            {"$set": {
                                "status": "cancelled",
                                "risk_status": "unavailable",
                                "completed_at": now,
                                "updated_at": now,
                            }},
                            session=session,
                        )
                        await self.repository.analyses.update_many(
                            {"appointment_id": appointment_id, "status": "analyzing"},
                            {"$set": {"cancel_requested_at": now, "updated_at": now}},
                            session=session,
                        )
                        appointment["status"] = "cancelled"
        except DuplicateKeyError as exc:
            raise ClinicalServiceError(409, "슬롯 충돌") from exc

        return {
            "appointment_id": appointment_id,
            "status": appointment["status"],
            "scheduled_at": appointment["scheduled_at"].isoformat(),
            "cancelled_analysis_ids": cancelled_ids,
            "running_analysis_ids": running_ids,
        }

    async def enqueue_visit_analysis(
        self,
        visit_id: str,
        query: str | None,
        files,
        doctor: dict,
    ) -> dict:
        if not files:
            raise ClinicalServiceError(422, "분석 파일을 한 개 이상 첨부해야 합니다.")
        staged, exam_type = await self.files.stage(files, doctor["_id"])
        now = self.clock()
        try:
            async with await self.db.client.start_session() as session:
                async with session.start_transaction():
                    visit = await self.repository.visits.find_one(
                        {
                            "visit_id": visit_id,
                            "hospital_id": doctor["hospital_id"],
                            "status": {"$ne": "cancelled"},
                        },
                        session=session,
                    )
                    if not visit:
                        raise ClinicalServiceError(404, "방문을 찾을 수 없습니다.")
                    appointment = await self.repository.appointments.find_one(
                        {"appointment_id": visit["appointment_id"]},
                        session=session,
                    )
                    if not appointment or appointment["status"] == "cancelled":
                        raise ClinicalServiceError(409, "취소된 예약에는 분석을 추가할 수 없습니다.")

                    date_key = visit["visit_date"].replace("-", "")
                    sequence = await self.repository.next_sequence(
                        f"analysis:{date_key}", session=session
                    )
                    analysis_id = f"AN-{date_key}-{sequence:04d}"
                    analysis = {
                        "_id": ObjectId(),
                        "analysis_id": analysis_id,
                        "hospital_id": doctor["hospital_id"],
                        "patient_id": visit["patient_id"],
                        "visit_id": visit_id,
                        "appointment_id": visit["appointment_id"],
                        "requested_by_doctor_id": doctor["_id"],
                        "visit_doctor_id": visit["doctor_id"],
                        "scheduled_at": appointment["scheduled_at"],
                        "queued_at": now,
                        "started_at": None,
                        "completed_at": None,
                        "cancel_requested_at": None,
                        "status": "queued",
                        "attempt": 0,
                        "worker_id": None,
                        "lease_expires_at": None,
                        "mode": "auto",
                        "query": query.strip() if query and query.strip() else "업로드한 영상을 분석해줘.",
                        "department": None,
                        "project": None,
                        "model_name": None,
                        "risk_tier": None,
                        "risk_status": "pending",
                        "confidence": None,
                        "finding": None,
                        "interpretation": None,
                        "recommendation": None,
                        "predictions": None,
                        "input_file_ids": [item["file_id"] for item in staged],
                        "result_file_ids": {"base": [], "heat": [], "box": []},
                        "error": None,
                        "created_at": now,
                        "updated_at": now,
                        "superseded_at": None,
                    }
                    await self.repository.analyses.insert_one(analysis, session=session)
                    await self.repository.visits.update_one(
                        {"_id": visit["_id"]},
                        {"$set": {"exam_type": exam_type, "updated_at": now}},
                        session=session,
                    )
                    await self.repository.appointments.update_one(
                        {"_id": appointment["_id"]},
                        {"$set": {"exam_type": exam_type, "updated_at": now}},
                        session=session,
                    )
                    await self.files.activate(
                        staged,
                        hospital_id=doctor["hospital_id"],
                        patient_id=visit["patient_id"],
                        visit_id=visit_id,
                        analysis_id=analysis_id,
                        session=session,
                    )
        except Exception:
            await self.files.mark_orphaned(staged)
            raise

        return {
            "analysis_id": analysis_id,
            "status": "queued",
            "queue_position": await self._queue_position(analysis),
        }
