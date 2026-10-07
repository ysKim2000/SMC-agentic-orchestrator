import asyncio
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from config.settings import (
    AUDIT_RETENTION_DAYS,
    CHAT_MAX_CONTEXT_ANALYSES,
    CHAT_MAX_CONTEXT_CHARS,
    CHAT_MAX_HISTORY_MESSAGES,
)
from services.agent_service import AgentService
from services.clinical_service import ClinicalServiceError
from services.file_access_service import FileAccessError, FileAccessService


IMAGE_TOKEN_PATTERN = re.compile(r"\[IMG:([A-Za-z0-9._:-]+)\]")
IMAGE_INTENT_WORDS = (
    "영상", "이미지", "사진", "병변", "열지도", "박스", "위치", "어디", "비교",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class ClinicalChatService:
    def __init__(self, database, agent: AgentService | None = None):
        self.db = database
        self.messages = database["chat_messages"]
        self.visits = database["visits"]
        self.patients = database["patients"]
        self.analyses = database["analyses"]
        self.access_logs = database["access_logs"]
        self.response_fixtures = database["chat_response_fixtures"]
        self.agent = agent or AgentService()
        self.file_access = FileAccessService(database)

    async def _visit_and_patient(self, visit_id: str, doctor: dict) -> tuple[dict, dict]:
        visit = await self.visits.find_one({
            "visit_id": visit_id,
            "hospital_id": doctor["hospital_id"],
        })
        if not visit:
            raise ClinicalServiceError(404, "방문을 찾을 수 없습니다.")
        patient = await self.patients.find_one({
            "patient_id": visit["patient_id"],
            "hospital_id": doctor["hospital_id"],
            "is_archived": False,
        })
        if not patient:
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        return visit, patient

    async def _patient_context(self, patient: dict) -> tuple[str, list[dict], dict[str, dict]]:
        analyses = await self.analyses.find({
            "patient_id": patient["patient_id"],
            "hospital_id": patient["hospital_id"],
            "status": {"$in": ["done", "queued", "analyzing", "failed"]},
        }).sort("created_at", -1).limit(CHAT_MAX_CONTEXT_ANALYSES).to_list(None)
        analyses.reverse()

        token_registry: dict[str, dict] = {}
        analysis_context = []
        for analysis in analyses:
            result_ids = analysis.get("result_file_ids") or {}
            available_tokens = []
            for role in ("base", "heat", "box"):
                for index, file_id in enumerate(result_ids.get(role, [])):
                    token = f"{analysis['analysis_id']}:{role}:{index}"
                    token_registry[token] = {
                        "token": token,
                        "analysis_id": analysis["analysis_id"],
                        "file_id": file_id,
                        "role": role,
                        "slice_index": index,
                    }
                    available_tokens.append(token)

            analysis_context.append({
                "analysis_id": analysis["analysis_id"],
                "visit_id": analysis["visit_id"],
                "status": analysis["status"],
                "model_name": analysis.get("model_name"),
                "risk_tier": analysis.get("risk_tier"),
                "risk_status": analysis.get("risk_status"),
                "confidence": analysis.get("confidence"),
                "finding": analysis.get("finding"),
                "interpretation": analysis.get("interpretation"),
                "recommendation": analysis.get("recommendation"),
                "predictions": analysis.get("predictions"),
                "available_image_tokens": available_tokens,
                "created_at": _iso(analysis["created_at"]),
            })

        patient_context = {
            "patient_id": patient["patient_id"],
            "name": patient["name"],
            "gender": patient["gender"],
            "birth_date": patient["birth_date"],
            "care_status": patient["care_status"],
        }
        while True:
            context = {
                "patient": patient_context,
                "analyses_across_all_visits": analysis_context,
            }
            serialized = json.dumps(context, ensure_ascii=False, default=str)
            if len(serialized) <= CHAT_MAX_CONTEXT_CHARS or len(analysis_context) <= 1:
                break
            analysis_context.pop(0)
        included_ids = {item["analysis_id"] for item in analysis_context}
        included_analyses = [
            analysis
            for analysis in analyses
            if analysis["analysis_id"] in included_ids
        ]
        included_registry = {
            token: ref
            for token, ref in token_registry.items()
            if ref["analysis_id"] in included_ids
        }
        return serialized, included_analyses, included_registry

    async def _history(self, patient_id: str) -> list[dict]:
        documents = await self.messages.find({
            "patient_id": patient_id,
        }).sort("created_at", -1).limit(CHAT_MAX_HISTORY_MESSAGES).to_list(None)
        documents.reverse()
        return [
            {"role": item["role"], "content": item["content"]}
            for item in documents
        ]

    async def _save_message(
        self,
        *,
        patient_id: str,
        visit_id: str,
        doctor_id,
        role: str,
        content: str,
        context_analysis_ids: list[str],
        image_refs: list[dict] | None = None,
        fixture_key: str | None = None,
        fixture_step: int | None = None,
    ) -> dict:
        document = {
            "message_id": str(uuid.uuid4()),
            "hospital_id": None,
            "patient_id": patient_id,
            "visit_id": visit_id,
            "doctor_id": doctor_id,
            "role": role,
            "content": content,
            "context_analysis_ids": context_analysis_ids,
            "image_refs": image_refs or [],
            "created_at": _utcnow(),
        }
        if fixture_key is not None:
            document["fixture_key"] = fixture_key
            document["fixture_step"] = fixture_step
        visit = await self.visits.find_one(
            {"visit_id": visit_id},
            {"hospital_id": 1},
        )
        document["hospital_id"] = visit["hospital_id"]
        await self.messages.insert_one(document)
        return document

    @staticmethod
    def _normalize_fixture_question(content: str) -> str:
        return " ".join(content.split()).rstrip(" .?!。？！")

    async def _fixture_response(
        self,
        *,
        patient_id: str,
        visit_id: str,
        content: str,
        doctor: dict,
    ) -> dict | None:
        fixtures = await self.response_fixtures.find({
            "hospital_id": doctor["hospital_id"],
            "patient_id": patient_id,
            "doctor_employee_id": doctor["employee_id"],
            "normalized_questions": self._normalize_fixture_question(content),
            "active": True,
        }).sort("step", 1).to_list(None)
        for fixture in fixtures:
            step = int(fixture.get("step", 1))
            if step == 1:
                return fixture
            previous = await self.messages.find_one(
                {
                    "patient_id": patient_id,
                    "visit_id": visit_id,
                    "role": "assistant",
                },
                sort=[("created_at", -1)],
            )
            if (
                previous
                and previous.get("fixture_key") == fixture.get("fixture_key")
                and previous.get("fixture_step") == step - 1
            ):
                return fixture
        return None

    @staticmethod
    def _fallback_image_tokens(
        content: str,
        token_registry: dict[str, dict],
    ) -> list[str]:
        if not any(word in content for word in IMAGE_INTENT_WORDS):
            return []
        latest_analysis_id = None
        for item in token_registry.values():
            latest_analysis_id = item["analysis_id"]
        if not latest_analysis_id:
            return []
        selected = []
        for role in ("base", "heat", "box"):
            token = f"{latest_analysis_id}:{role}:0"
            if token in token_registry:
                selected.append(token)
        return selected

    async def _resolve_images(
        self,
        refs: list[dict],
        doctor: dict,
    ) -> list[dict]:
        by_analysis: dict[str, list[dict]] = defaultdict(list)
        for ref in refs:
            by_analysis[ref["analysis_id"]].append(ref)

        output = []
        for analysis_id, analysis_refs in by_analysis.items():
            try:
                access = await self.file_access.issue_analysis_urls(
                    analysis_id,
                    doctor,
                )
            except FileAccessError:
                # 과거 대화는 파일 보존 상태와 무관하게 텍스트로 계속 열려야 한다.
                continue
            url_by_file_id = {}
            for role in ("base", "heat", "box"):
                for file_id, url in zip(
                    access["image_file_ids"][role],
                    access["images"][role],
                ):
                    url_by_file_id[file_id] = url
            for ref in analysis_refs:
                url = url_by_file_id.get(ref["file_id"])
                if url:
                    output.append({
                        **ref,
                        "url": url,
                        "expires_at": access["expires_at"],
                    })
        return output

    async def message_response(self, message: dict, doctor: dict) -> dict:
        return {
            "message_id": message["message_id"],
            "patient_id": message["patient_id"],
            "visit_id": message["visit_id"],
            "role": message["role"],
            "content": message["content"],
            "context_analysis_ids": list(message.get("context_analysis_ids") or []),
            "images": await self._resolve_images(
                list(message.get("image_refs") or []),
                doctor,
            ),
            "created_at": _iso(message["created_at"]),
        }

    async def get_chat(self, visit_id: str, doctor: dict) -> list[dict]:
        await self._visit_and_patient(visit_id, doctor)
        messages = await self.messages.find({
            "visit_id": visit_id,
            "hospital_id": doctor["hospital_id"],
        }).sort("created_at", 1).to_list(None)
        return [await self.message_response(item, doctor) for item in messages]

    async def send(self, visit_id: str, content: str, doctor: dict) -> dict:
        visit, patient = await self._visit_and_patient(visit_id, doctor)
        fixture = await self._fixture_response(
            patient_id=patient["patient_id"],
            visit_id=visit_id,
            content=content,
            doctor=doctor,
        )
        context, analyses, token_registry = await self._patient_context(patient)
        context_ids = [
            item["analysis_id"]
            for item in analyses
            if item["status"] == "done"
        ]
        history = await self._history(patient["patient_id"])
        user_message = await self._save_message(
            patient_id=patient["patient_id"],
            visit_id=visit_id,
            doctor_id=doctor["_id"],
            role="user",
            content=content,
            context_analysis_ids=context_ids,
        )

        if fixture:
            delay_seconds = max(
                0.0,
                min(float(fixture.get("response_delay_seconds", 0)), 10.0),
            )
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            answer = str(fixture["response"]).strip()
        else:
            allowed_tokens = list(token_registry)
            prompt = (
                "다음 환자의 모든 방문에 걸친 임상 문맥을 바탕으로 현재 질문에 "
                "한국어로 답하세요. 모델 결과는 진단 확정이 아닌 의사결정 지원 정보로 "
                "표현하세요. 관련 이미지를 답변에 넣을 필요가 있을 때만 아래 허용된 "
                "토큰을 정확히 [IMG:토큰] 형태로 답변 문장 사이에 넣으세요.\n\n"
                f"임상 문맥:\n{context}\n\n"
                f"허용 이미지 토큰:\n{json.dumps(allowed_tokens, ensure_ascii=False)}\n\n"
                f"현재 선택 방문: {visit['visit_id']} ({visit['visit_date']})\n"
                f"현재 질문: {content}"
            )
            result = await self.agent.plan(
                mode="clinical",
                query=prompt,
                history=history,
            )
            if result.get("status") == "error":
                raise ClinicalServiceError(
                    502,
                    result.get("message") or "임상 채팅 Agent 호출에 실패했습니다.",
                )
            answer = (
                result.get("message")
                or result.get("answer")
                or result.get("response")
                or ""
            ).strip()
        if not answer:
            raise ClinicalServiceError(502, "임상 채팅 Agent가 빈 응답을 반환했습니다.")

        tokens = [
            token
            for token in dict.fromkeys(IMAGE_TOKEN_PATTERN.findall(answer))
            if token in token_registry
        ]
        if not tokens:
            tokens = self._fallback_image_tokens(content, token_registry)
            if tokens:
                answer = f"{answer}\n\n" + "\n".join(
                    f"[IMG:{token}]" for token in tokens
                )
        image_refs = [token_registry[token] for token in tokens]
        assistant_message = await self._save_message(
            patient_id=patient["patient_id"],
            visit_id=visit_id,
            doctor_id=doctor["_id"],
            role="assistant",
            content=answer,
            context_analysis_ids=context_ids,
            image_refs=image_refs,
            fixture_key=fixture.get("fixture_key") if fixture else None,
            fixture_step=fixture.get("step") if fixture else None,
        )

        now = _utcnow()
        await self.access_logs.insert_one({
            "doctor_id": doctor["_id"],
            "hospital_id": doctor["hospital_id"],
            "action": "clinical_chat",
            "resource_type": "visit",
            "resource_id": visit_id,
            "patient_id": patient["patient_id"],
            "context_analysis_ids": context_ids,
            "at": now,
            "expires_at": now + timedelta(days=AUDIT_RETENTION_DAYS),
        })
        return {
            "user": await self.message_response(user_message, doctor),
            "assistant": await self.message_response(assistant_message, doctor),
        }
