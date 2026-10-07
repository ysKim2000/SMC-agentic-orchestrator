import json
import logging
import os
import re
import httpx
import yaml

from services import wiki_service, board_service, modality_service, data_id_service
from services.rag_service import retrieve
from rag.retriever import search_models
from rag import embedder
from llm import client as llm_client
from llm.prompts import (
    SYSTEM_PLAN,
    SYSTEM_CLINICAL,
    SYSTEM_INTERPRET,
    SYSTEM_INTERPRET_EN,
    SYSTEM_INTENT,
    SYSTEM_GENERAL,
    build_plan_prompt,
    build_clinical_prompt,
    build_interpret_prompt,
    build_intent_prompt,
    build_model_select_prompt,
    build_general_prompt,
    build_general_prompt_from_attachments,
)
from llm.prompts.builders import _report_style_enabled
from llm.prompts.utils import sort_image_roles

logger = logging.getLogger("maple-ai-agent")


async def plan(
    query: str,
    uploaded_types: list[str],
    history: list[dict],
    mode: str = "auto",
    images: list[str] = [],
    csv_data: list[dict] = [],
    attachments: list[dict] = [],
) -> dict:
    # attachments만 오고 uploaded_types가 비면 type에서 파생 (prediction/auto 매칭 보존)
    if not uploaded_types and attachments:
        uploaded_types = [a.get("type", "") for a in attachments if a.get("type")]

    if mode == "clinical":
        return await _clinical(query)

    if mode == "prediction":
        return await _prediction(query, uploaded_types, attachments)

    if mode == "general":
        return await _general(query, images, csv_data, attachments, uploaded_types)

    return await _auto(query, uploaded_types, images, csv_data, attachments)


# ── 파일 타입 정규화 ───────────────────────────────────────────────────────────

# 의료 영상 파일 확장자 → 표준 포맷명 매핑
_EXT_NORMALIZE: dict[str, str] = {
    # DICOM
    "dcm": "dicom",
    "dicom": "dicom",
    "ima": "dicom",         # Siemens DICOM
    # NIfTI (Neuroimaging Informatics Technology Initiative)
    "nii": "nifti",
    "nii.gz": "nifti",      # gzip 압축 NIfTI (복합 확장자)
    # Analyze (NIfTI 이전 포맷, 대부분의 파이프라인에서 호환)
    "hdr": "analyze",
    "img": "analyze",
    # FreeSurfer MGH/MGZ
    "mgh": "mgh",
    "mgz": "mgh",           # gzip 압축 MGH
    # MINC (Montreal Neurological Institute)
    "mnc": "minc",
    # NRRD (Nearly Raw Raster Data, ITK 표준)
    "nrrd": "nrrd",
    "nhdr": "nrrd",
    "seg.nrrd": "nrrd",
    # MetaImage (ITK/SimpleITK)
    "mha": "metaimage",
    "mhd": "metaimage",
    # Whole Slide Imaging (병리학)
    "svs": "wsi",           # Aperio
    "ndpi": "wsi",          # Hamamatsu
    "scn": "wsi",           # Leica
    "mrxs": "wsi",          # MIRAX/3DHistech
    "ome.tif": "wsi",       # OME-TIFF
    # 표준 이미지 (안저/피부과/병리 등)
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "bmp": "image",
    "tif": "tiff",
    "tiff": "tiff",
    # 임상 데이터
    "csv": "csv",
    "xlsx": "excel",
    "xls": "excel",
    "json": "json",
    # ML/데이터 포맷
    "npy": "numpy",
    "npz": "numpy",
    "h5": "hdf5",
    "hdf5": "hdf5",
}

# NIfTI 포맷과 호환되는 원시 확장자 목록
# - 백엔드가 "nii.gz" 파일을 처리할 때 마지막 확장자인 "gz"만 전송하는 경우 대응
# - 단, "gz" 단독은 모호함 (mnc.gz, tar.gz 등 존재) → nifti 요구 모델일 때만 조건부 허용
_NIFTI_COMPAT_RAW = {"gz", "nii", "nii.gz", "nifti"}


def _normalize_types(uploaded_types: list[str]) -> list[str]:
    """업로드된 파일 확장자를 표준 포맷명으로 정규화.
    복합 확장자(nii.gz 등)는 그대로 우선 처리하고, 단순 확장자는 매핑 적용.
    "gz" 단독은 포맷 불명확으로 정규화하지 않고 원본 유지."""
    normalized = []
    for t in uploaded_types:
        t_lower = t.lower().strip()
        mapped = _EXT_NORMALIZE.get(t_lower, t_lower)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


def _types_match(required_data: list[str], uploaded_types: list[str]) -> bool:
    """모델의 required_data와 업로드된 파일 타입 매칭 여부 판단.

    NIfTI 조건부 처리:
    - 모델이 "nifti"를 요구하는 경우, "gz" 확장자도 허용
      (백엔드가 .nii.gz 파일의 확장자를 "gz"로만 전송할 수 있으므로)
    """
    normalized = _normalize_types(uploaded_types)
    raw_lower = {t.lower().strip() for t in uploaded_types}

    for req in required_data:
        req_norm = _EXT_NORMALIZE.get(req.lower().strip(), req.lower().strip())
        # 정규화 후 직접 매칭
        if req_norm in normalized:
            return True
        # NIfTI 조건부 매칭: 모델이 nifti 요구 시 gz 등 호환 확장자 허용
        if req_norm == "nifti" and raw_lower & _NIFTI_COMPAT_RAW:
            return True
    return False


# ── mode 핸들러 ───────────────────────────────────────────────────────────────

async def _clinical(query: str) -> dict:
    """RAG + Wiki 즉시 검색 → 임상 지식 답변"""
    wiki_context = wiki_service.search_wiki(query)
    model_results, knowledge_results, rag_context = await retrieve(query)

    prompt = build_clinical_prompt(query, wiki_context, rag_context)
    message = await llm_client.generate(prompt, system=SYSTEM_CLINICAL)

    wiki_service.upsert_concept(query, message)

    sources = []
    for r in knowledge_results:
        sources.append({"source": "pubmedqa", "text": r["text"][:80], "score": r["score"]})

    # 관련 특화 모델 추천 문구 생성
    related_models = []
    for r in model_results:
        meta = r.get("metadata", {})
        project = meta.get("project", "")
        model_name = meta.get("model_name", "")
        if project and model_name:
            related_models.append(f"{project} ({model_name})")

    model_suggestion = None
    if related_models:
        model_list = ", ".join(related_models)
        model_suggestion = f"관련 질환에 사용할 수 있는 특화 AI 모델이 있습니다: {model_list}"

    return {
        "query_type": "knowledge",
        "mode": "clinical",
        "message": message,
        "sources": sources,
        "model_suggestion": model_suggestion,
    }


_QUERY_STOP = {"determine", "whether", "present", "study", "write", "report", "describing",
               "findings", "finding", "this", "then", "the", "is", "in", "a", "an", "of", "and",
               "or", "for", "on", "with", "any", "there", "evidence", "check"}


def _keyword_overlap(query: str, result: dict) -> int:
    """질의 내용어 중 모델 질환 필드에 그대로 등장하는 단어 수. 동점이면 원래 순서(유사도) 유지."""
    words = {w for w in re.findall(r"[a-z][a-z-]+", (query or "").lower()) if w not in _QUERY_STOP and len(w) > 2}
    m = re.search(r"질환:\s*(.*)", result.get("text", "") or "")
    disease = (m.group(1) if m else "").lower()
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}", disease))


def _scope_multilabel(query: str, step_results: list[dict]) -> list[dict]:
    """다중 라벨 모델(예: ChestXray14 14개 라벨)의 예측을 질의 소견에 해당하는 라벨로 좁힌다.

    흉수만 물었는데 같은 모델의 다른 라벨(Infiltration, Nodule…) 위양성까지 Reader에게 넘어가면
    판독문에 묻지 않은 소견으로 적힌다(MIMIC 지어낸 언급의 75%가 모델 위양성). 질의 단어와
    겹치는 라벨이 없으면 원래대로 둔다.
    """
    words = [w for w in re.findall(r"[a-z][a-z-]+", (query or "").lower()) if w not in _QUERY_STOP and len(w) > 2]
    label_words = lambda p: set(re.findall(r"[a-z][a-z-]+", str(p.get("label", "")).lower().replace("_", " ")))
    out = []
    for step in step_results:
        preds = step.get("predictions")
        if isinstance(preds, list) and len({p.get("label") for p in preds if isinstance(p, dict)}) > 1:
            dicts = [p for p in preds if isinstance(p, dict)]
            # 소견명의 중심어(마지막 단어, 예: pleural "effusion")가 맞는 라벨을 먼저, 없으면 아무 단어나
            keep = [p for p in dicts if words and words[-1] in label_words(p)] or \
                   [p for p in dicts if set(words) & label_words(p)]
            if keep:
                step = {**step, "predictions": keep}
        out.append(step)
    return out


_RAG_QUERY_PROMPT = """You are preparing a search of a general medical knowledge base for the request below.
User request: {request}
Data type: {ident}

Look at the image and return only JSON (no markdown):
{{"observations": "<one or two lines: the notable things visible in this image, in plain clinical terms>",
 "search_query": "<one concise English search query combining the data type, the topic of the request, and the key observations>"}}"""


async def _build_rag_hint(query: str, original_images: list[str]) -> dict | None:
    """RAG_QUERY=structured — VLM으로 데이터 종류와 영상 관찰을 얻어 검색어를 만든다.
    검색어만 바꾸고 지식베이스·판정 근거는 건드리지 않는다. 실패하면 None(기존 방식)."""
    if not original_images:
        return None
    ident = (await data_id_service.identify(original_images)).get("text", "")
    request = " ".join(query.split())[:400]
    try:
        raw = await llm_client.generate_with_images(
            _RAG_QUERY_PROMPT.format(request=request, ident=ident or "unknown"),
            original_images[:1], system="Return only valid JSON.")
        m = re.search(r"\{.*\}", raw or "", re.S)
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        logger.exception("[interpret] rag query build failed")
        parsed = {}
    search_query = str(parsed.get("search_query") or "").strip() or f"{ident} {request}"
    hint = {"ident": ident, "observations": str(parsed.get("observations") or ""), "search_query": search_query}
    logger.info("[interpret] rag hint=%s", hint)
    return hint


async def _prediction(query: str, uploaded_types: list[str],
                      attachments: list[dict] | None = None) -> dict:
    """모델 레지스트리 검색 → required_data 매칭 → 실행 계획 반환"""
    # 첨부에서 모달리티를 직접 읽어 검색 질의를 보강한다(_auto와 동일 근거).
    modality_info = modality_service.from_attachments(attachments or [])
    hint = modality_service.search_hint(modality_info)
    search_query = f"{hint}. {query}" if hint else query
    if hint:
        logger.info("[prediction] modality=%s hint=%r", modality_info.get("modality"), hint)
    # DATA_ID=vlm: VLM이 첨부 영상을 보고 데이터 종류(모달리티·부위)만 판별한다.
    # 그 결과와 레지스트리 구조화 필드가 맞는 모델로 후보를 좁힌다.
    ident = {"text": "", "modality": "", "regions": []}
    if data_id_service.enabled():
        imgs = []
        for att in attachments or []:
            for img in att.get("images") or []:
                if isinstance(img, str) and img:
                    imgs.append(img.split(",", 1)[1] if img.startswith("data:") else img)
        ident = await data_id_service.identify(imgs)
        if ident["text"]:
            search_query = f"{ident['text']}. {query}"
    try:
        model_results = search_models(search_query, n_results=40 if ident["text"] else 20)
    except Exception:
        model_results = []
    identified = []
    if ident["modality"] or ident["regions"]:
        identified = [r for r in model_results
                      if data_id_service.matches(r.get("metadata") or {}, ident)]

    if not model_results:
        return {
            "status": "no_model",
            "mode": "prediction",
            "message": (
                "요청에 맞는 등록된 AI 모델을 찾지 못했습니다. "
                "다른 표현으로 다시 시도하거나 모델 등록 후 사용해 주세요."
            ),
        }

    # 유사도 낮은 모델 제거 (threshold 이하는 관련 없음으로 판단)
    SCORE_THRESHOLD = 0.40
    if identified:
        # 데이터 종류가 맞는 모델만 남았으므로 유사도 문턱은 적용하지 않는다.
        # 문턱을 두면 설명문 표현이 질의와 덜 비슷한 전용 모델이 빠진다(SPIDER 실측).
        # 질의의 소견 단어가 모델 질환 필드에 그대로 있으면 앞으로 올린다(하이브리드 검색).
        # 임베딩만으로는 "medical material" 질의에서 해당 모델이 CT 모델 중 9위였다.
        ranked = sorted(identified, key=lambda r: -_keyword_overlap(query, r))
        # 질의 소견과 관련된 모델(질환 목록에 질의 단어가 있는 모델)만 실행한다. 상한은
        # 호출 측이 정한다(실험: 5). 관련 모델이 없을 때만 유사도 순 후보를 그대로 쓴다.
        # 이전에는 상한까지 채우느라 결핵·촬영방향 모델이 무관한 질의의 37%·19%에 끼었다.
        relevant = [r for r in ranked if _keyword_overlap(query, r) > 0]
        model_results = (relevant or ranked)[:20]
    else:
        above = [r for r in model_results if r.get("score", 0) >= SCORE_THRESHOLD]
        # 영상이 없는 입력(ECG CSV 등)은 VLM 식별이 불가능하고, 설명문 유사도가
        # 문턱에 못 미쳐 후보가 0개가 될 수 있다(PTB-XL 실측). 이때는 문턱을 버리고
        # 상위 후보를 넘긴다 — 바로 아래 입력 형식 필터가 맞지 않는 모델을 거른다.
        model_results = above if above else model_results[:20]
    _excl = _excluded_models()
    model_results = [r for r in model_results
                     if (r.get("metadata") or {}).get("model_name") not in _excl]

    if not model_results:
        return {
            "status": "no_model",
            "mode": "prediction",
            "message": (
                "요청에 맞는 등록된 AI 모델을 찾지 못했습니다. "
                "다른 표현으로 다시 시도하거나 모델 등록 후 사용해 주세요."
            ),
        }

    # uploaded_types와 required_data 매칭 필터링
    matched = []
    mismatched = []
    for r in model_results:
        meta = r.get("metadata", {})
        model_name = meta.get("model_name", "")
        # ChromaDB metadata에 required_data가 없으면 → Wiki에서 보완
        required_data = _get_required_data(meta, model_name)

        if not required_data:
            # required_data 정보 자체가 없으면 일단 포함
            matched.append(meta)
        elif _types_match(required_data, uploaded_types):
            matched.append(meta)
        else:
            mismatched.append({
                "model": model_name,
                "required": required_data,
                "uploaded": uploaded_types,
            })

    if not matched:
        # 검색은 됐지만 데이터 타입이 안 맞는 경우
        mismatch_desc = "; ".join(
            f"{m['model']} (필요: {m['required']}, 업로드: {m['uploaded']})"
            for m in mismatched
        )
        return {
            "status": "type_mismatch",
            "mode": "prediction",
            "message": (
                "검색된 모델이 요구하는 데이터 타입과 업로드된 파일이 일치하지 않습니다. "
                f"상세: {mismatch_desc}"
            ),
            "mismatched_models": mismatched,
        }

    matched = _with_always_on(matched, modality_info.get("modality"))
    steps = [
        {
            "step": i + 1,
            "model": m.get("model_name", ""),
            "department": m.get("department", ""),
            "project": m.get("project", ""),
            "task_type": m.get("task_type", ""),
            "result_type": _get_list_field(m, "result_type"),
        }
        for i, m in enumerate(matched)
    ]

    return {
        "status": "ready",
        "mode": "prediction",
        "execution_plan": {"steps": steps},
        "missing_inputs": [],
        "message": f"{len(steps)}개 모델 실행 계획이 수립되었습니다.",
    }


def _get_required_data(meta: dict, model_name: str) -> list[str]:
    """ChromaDB metadata에 required_data 없으면 Wiki 페이지에서 파싱"""
    # metadata에 직접 있는 경우 (등록 시 저장했으면)
    if "required_data" in meta:
        val = meta["required_data"]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        if isinstance(val, list):
            return val

    # Wiki 페이지에서 파싱 (project + model_name으로 조회)
    project = meta.get("project", "")
    if project and model_name:
        page = wiki_service.read_model_page(project, model_name)
        for line in page.splitlines():
            if "required_data" in line:
                start = line.find("[")
                end = line.find("]")
                if start != -1 and end != -1:
                    return [v.strip() for v in line[start+1:end].split(",") if v.strip()]
    return []


GENERAL_MAX_MODELS = 5      # general 실행계획 최대 모델 수
VECTOR_RECALL_K = 8         # 벡터 recall 후보 수 (문턱 없이 넓게)


async def _general(
    query: str,
    images: list[str],
    csv_data: list[dict],
    attachments: list[dict] | None = None,
    uploaded_types: list[str] | None = None,
) -> dict:
    """general 오케스트레이션:
    의도분석 → 하이브리드 후보 recall(벡터+메타 키워드) → LLM 모델 선택 →
    provides/requires 기반 DAG 실행계획 반환. 선택 모델 0개면 VLM 단독 fallback.
    확장자 불일치로 탈락시키지 않는다 — 포맷 정합은 라우팅(Track B) 몫이며 required_data로 전달."""
    attachments = attachments or []
    uploaded_types = uploaded_types or []

    # 1. LLM 의도 분석 (한글→영어 브릿지, 신체부위·질환군 추출)
    intent = await _analyze_intent(query, uploaded_types)

    # 2. 하이브리드 후보 recall — 벡터 + 메타데이터 키워드 (문턱 없이 넓게)
    candidates = _discover_models(query, intent)

    # 3. LLM이 후보 중 적절한 모델 선택 (매직 문턱 대체)
    selected = (await _select_models(query, intent, candidates))[:GENERAL_MAX_MODELS]

    # 4. 매칭 모델 0개 → VLM 단독 fallback
    if not selected:
        message = await _run_general_vlm(query, images, csv_data, attachments)
        return {
            "status": "ready",
            "query_type": "general",
            "mode": "general",
            "execution_plan": {"steps": []},
            "fallback_vlm_only": True,
            "message": message,
        }

    # 5. provides/requires 기반 DAG 실행계획 생성
    steps = _build_execution_dag(selected)
    if not steps:
        # 선행 조건을 못 채워 전부 제외된 경우도 VLM fallback
        message = await _run_general_vlm(query, images, csv_data, attachments)
        return {
            "status": "ready",
            "query_type": "general",
            "mode": "general",
            "execution_plan": {"steps": []},
            "fallback_vlm_only": True,
            "message": message,
        }

    return {
        "status": "ready",
        "query_type": "general",
        "mode": "general",
        "execution_plan": {"steps": steps},
        "fallback_vlm_only": False,
        "message": "",
    }


async def _analyze_intent(query: str, uploaded_types: list[str]) -> dict:
    """LLM 의도 분석 — 신체부위·질환군·모달리티·검색쿼리 추출. 실패 시 빈 dict."""
    try:
        raw = await llm_client.generate(
            build_intent_prompt(query, uploaded_types), system=SYSTEM_INTENT
        )
        parsed = _parse_json_response(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.exception("[general] intent analysis failed")
        return {}


# 불용어 — 키워드 매칭에서 잡음 제거 (질환/부위 같은 실질 키워드만 남김)
_KW_STOP = {
    "of", "in", "the", "a", "an", "and", "or", "on", "for", "with", "to",
    "detection", "detect", "classification", "classify", "imaging", "image",
    "analysis", "analyze", "scan", "study", "model", "chest",
}


def _intent_keywords(intent: dict) -> set[str]:
    """의도분석 결과에서 키워드 매칭용 실질 단어 집합 추출 (질환·부위 중심)."""
    terms: set[str] = set()
    for key in ("disease_group", "body_part", "modality"):
        val = (intent.get(key) or "").lower()
        for t in re.split(r"[,\s]+", val):
            if t:
                terms.add(t)
    for t in re.split(r"[,\s]+", (intent.get("search_query") or "").lower()):
        if t:
            terms.add(t)
    return {t for t in terms if len(t) > 2 and t not in _KW_STOP}


def _discover_models(query: str, intent: dict) -> list[dict]:
    """하이브리드 후보 recall — 벡터 검색 + 메타데이터 키워드 매칭 (문턱 없이 넓게).
    반환: [{"metadata": {...}, "text": "<doc_text>"}] (model_name 기준 dedup)"""
    candidates: dict[str, dict] = {}   # model_name → {"metadata", "text"}

    # 벡터 recall (의도분석 영어 쿼리 우선, 없으면 원본)
    search_query = intent.get("search_query") or query
    try:
        for r in search_models(search_query, n_results=VECTOR_RECALL_K):
            meta = r.get("metadata") or {}
            name = meta.get("model_name")
            if name and name not in candidates:
                candidates[name] = {"metadata": meta, "text": r.get("text", "")}
    except Exception:
        logger.exception("[general] vector recall failed")

    # 키워드 recall — 등록 모델 전체의 doc_text/메타에 의도 키워드가 있으면 포함
    #   (임베딩 점수와 무관하게 'pneumonia' 같은 명시 키워드를 확정 포착)
    kw = _intent_keywords(intent)
    if kw:
        try:
            for m in embedder.get_all_models():
                name = m.get("model_name")
                if not name or name in candidates:
                    continue
                doc = (m.get("_document") or "").lower()
                hay = f"{doc} {name} {m.get('task_type', '')}".lower()
                if any(t in hay for t in kw):
                    meta = {k: v for k, v in m.items() if k != "_document"}
                    candidates[name] = {"metadata": meta, "text": m.get("_document", "")}
        except Exception:
            logger.exception("[general] keyword recall failed")

    return list(candidates.values())


async def _select_models(query: str, intent: dict, candidates: list[dict]) -> list[dict]:
    """후보 중 적절한 모델을 LLM이 선택. 반환: 선택된 메타데이터 목록."""
    if not candidates:
        return []
    try:
        raw = await llm_client.generate(
            build_model_select_prompt(query, intent, candidates), system=SYSTEM_PLAN
        )
        parsed = _parse_json_response(raw)
    except Exception:
        logger.exception("[general] model selection failed")
        return []

    names = parsed.get("selected_models") or parsed.get("models") or []
    if not isinstance(names, list):
        return []
    by_name = {c["metadata"].get("model_name"): c["metadata"] for c in candidates}
    selected = []
    for n in names:
        meta = by_name.get(n)
        if meta is not None and meta not in selected:
            selected.append(meta)

    # LLM이 지목한 주 근거 모델을 표시한다. 성능 지표뿐 아니라 질환 적합성·산출물
    # 유형·지표의 신뢰도를 함께 보고 고르게 되어 있다(build_model_select_prompt 참조).
    primary = parsed.get("primary_model")
    reason = (parsed.get("primary_reason") or "").strip()
    if primary not in by_name and selected:
        primary = selected[0].get("model_name")
        reason = reason or "fallback: first selected model"
    for meta in selected:
        is_primary = meta.get("model_name") == primary
        meta["evidence_role"] = "primary" if is_primary else "supporting"
        if is_primary and reason:
            meta["primary_reason"] = reason
    return selected


async def _run_general_vlm(
    query: str,
    images: list[str],
    csv_data: list[dict],
    attachments: list[dict],
) -> str:
    """이미지 + CSV + 메타데이터 → VLM 범용 종합 분석 (모델 미매칭 fallback)."""
    if attachments:
        prompt, vlm_images = build_general_prompt_from_attachments(query, attachments)
    else:
        prompt = build_general_prompt(query, csv_data)
        vlm_images = images
    try:
        if vlm_images:
            return await llm_client.generate_with_images(prompt, vlm_images, system=SYSTEM_GENERAL)
        return await llm_client.generate(prompt, system=SYSTEM_GENERAL)
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300] if e.response is not None else str(e)
        fallback_prompt = (
            f"{prompt}\n\n"
            "## 시스템 참고\n"
            "첨부 이미지의 VLM 분석 요청이 실패하여 텍스트/수치 데이터만 기반으로 답변하세요. "
            "응답은 반드시 영어로 작성하세요. "
            f"LLM 오류 요약:{detail}"
        )
        return await llm_client.generate(fallback_prompt, system=SYSTEM_GENERAL)


# ── general 실행계획 DAG ────────────────────────────────────────────────────────

def _get_list_field(meta: dict, key: str) -> list[str]:
    """ChromaDB 메타데이터의 콤마 결합 리스트 값을 파싱."""
    val = meta.get(key)
    if isinstance(val, str):
        return [v.strip() for v in val.split(",") if v.strip()]
    if isinstance(val, list):
        return val
    return []


def _find_provider(tag: str) -> dict | None:
    """레지스트리 전체에서 tag를 provides하는 모델 메타 탐색."""
    try:
        all_models = embedder.get_all_models()
    except Exception:
        return None
    for meta in all_models:
        if tag in _get_list_field(meta, "provides"):
            return meta
    return None


def _build_execution_dag(selected: list[dict]) -> list[dict]:
    """선택된 모델들 + provides/requires 선행조건으로 DAG 실행계획을 구성.
    - 선행 태그를 provides하는 모델을 (선택목록 또는 레지스트리에서) 찾아 step 포함 + depends_on wiring
    - 선행을 못 채우는 모델은 제외(연쇄 제외)
    - 사이클은 제거"""
    nodes: list[dict] = []              # {step_id, meta, depends_on}
    id_by_model: dict[str, str] = {}    # model_name → step_id
    provider_of: dict[str, str] = {}    # tag → step_id

    def _add(meta: dict) -> str:
        model_name = meta.get("model_name", "")
        if model_name in id_by_model:
            return id_by_model[model_name]
        sid = f"s{len(nodes) + 1}"
        nodes.append({"step_id": sid, "meta": meta, "depends_on": []})
        id_by_model[model_name] = sid
        for tag in _get_list_field(meta, "provides"):
            provider_of.setdefault(tag, sid)
        return sid

    for meta in selected:
        _add(meta)

    # 선행조건 wiring (nodes가 커질 수 있으므로 인덱스 순회)
    dropped: set[str] = set()
    i = 0
    while i < len(nodes):
        node = nodes[i]
        deps: list[str] = []
        satisfied = True
        for tag in _get_list_field(node["meta"], "requires"):
            provider_sid = provider_of.get(tag)
            if provider_sid is None:
                provider_meta = _find_provider(tag)
                if provider_meta is None:
                    satisfied = False
                    break
                provider_sid = _add(provider_meta)
            deps.append(provider_sid)
        if not satisfied:
            dropped.add(node["step_id"])
        node["depends_on"] = list(dict.fromkeys(deps))
        i += 1

    # 선행 미충족 노드에 의존하는 노드도 연쇄 제외
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if node["step_id"] in dropped:
                continue
            if any(d in dropped for d in node["depends_on"]):
                dropped.add(node["step_id"])
                changed = True

    remaining = [n for n in nodes if n["step_id"] not in dropped]
    remaining = _resolve_cycles(remaining)

    steps = []
    for n in remaining:
        m = n["meta"]
        steps.append({
            "step_id": n["step_id"],
            "model_name": m.get("model_name", ""),
            "department": m.get("department", ""),
            "project": m.get("project", ""),
            "task_type": m.get("task_type", ""),
            "result_type": _get_list_field(m, "result_type"),
            "required_data": _get_required_data(m, m.get("model_name", "")),
            "depends_on": n["depends_on"],
            # 주 근거 / 보조 근거 구분 — 해석 단계에서 어느 결과를 우선 근거로 삼을지 판단한다
            "evidence_role": m.get("evidence_role", "supporting"),
            **({"primary_reason": m["primary_reason"]} if m.get("primary_reason") else {}),
        })
    return steps


def _resolve_cycles(nodes: list[dict]) -> list[dict]:
    """위상정렬 불가능한(사이클에 속한) 노드를 제거. (Kahn 알고리즘)"""
    ids = {n["step_id"] for n in nodes}
    deps = {n["step_id"]: [d for d in n["depends_on"] if d in ids] for n in nodes}
    indeg = {sid: len(deps[sid]) for sid in ids}
    radj: dict[str, list[str]] = {sid: [] for sid in ids}
    for sid, ds in deps.items():
        for d in ds:
            radj[d].append(sid)

    queue = [sid for sid in ids if indeg[sid] == 0]
    ordered: set[str] = set()
    while queue:
        n = queue.pop()
        ordered.add(n)
        for m in radj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    if len(ordered) != len(ids):
        cyclic = ids - ordered
        logger.warning("[general] dropping models in dependency cycle: %s", cyclic)
        return [n for n in nodes if n["step_id"] in ordered]
    return nodes


def _excluded_models() -> set[str]:
    """EXCLUDE_MODELS=a,b — 후보에서 뺄 모델. 기본은 비어 있어 대화형 동작은 그대로다.

    ChestXray14_Multilabel은 NIH에서 민감도=특이도로 맞춘 임계값(0.01~0.06)을 써서
    MIMIC 전체 테스트셋 2,145건에서 Fibrosis 52%·Pleural_Thickening 48%를 양성으로
    냈다(실제 Pleural Other 4.4%). MARS 오탐의 Pleural Other 1,290건·Lung Lesion 859건이
    전부 이 모델 양성에서 왔다. 평가 설정에서 제외할 수 있게 한다.
    """
    raw = os.getenv("EXCLUDE_MODELS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


# 모달리티별 상시 실행 모델.
# 선정 규칙: 평가셋 양성 빈도 상위이면서 AUROC 0.8 이상인데 벡터 검색이 자력으로
# 못 뽑는 모델. 임베딩은 질환명 위주 질의와 매칭하므로, 장치 검출처럼 질환명이
# 아닌 소견이나 서술이 일반적인 소견은 순위가 밀린다. 실측(MIMIC 50건):
#   Support Devices  빈도 29/50, AUROC 0.869, 중복제거 후 12위 -> top7 진입 불가
#   Edema            빈도 16/50, AUROC 0.834, 중복제거 후 15위 -> top7 진입 불가
# 두 모델 모두 유사도 0.40 문턱에도 걸린다(0.378 / 0.354). 케이스별 선택이 아니라
# 데이터셋 단위 설계 결정이므로 질의 내용과 무관하게 항상 포함한다.
#   Edema는 제외한다. 상시 투입 실측에서 F1이 0.683 -> 0.571로 떨어졌다. 모델
#   예측이 Reader의 영상 판독을 덮어써서, 모델 없이 영상만 보던 편이 나았다.
#   Support Devices는 +0.013로 미미하지만 플러스이므로 남긴다(실패 29건 중 7건은
#   문턱 0.5 미달 false negative, 6건은 CheXbert가 봉합선·리드를 안 세는 문제).
_ALWAYS_ON_MODELS = {
    "XR": ("MIMIC_CXR_Support_Devices",),
}


def _with_always_on(available: list[dict], modality: str | None) -> list[dict]:
    """상시 실행 모델을 후보 목록 앞에 채워 넣는다(이미 있으면 그대로)."""
    # ALWAYS_ON=off: 상시 실행 규칙을 쓰지 않는다. 이 목록은 MIMIC 테스트셋의 소견 빈도를
    # 보고 정한 것이라, 평가 설정에서는 끄는 것이 공정하다.
    if os.getenv("ALWAYS_ON", "on").strip().lower() == "off":
        return available
    names = _ALWAYS_ON_MODELS.get(modality or "")
    if not names:
        return available
    present = {m.get("model_name") for m in available if isinstance(m, dict)}
    missing = [n for n in names if n not in present]
    if not missing:
        return available
    forced = []
    for meta in embedder.get_all_models():
        if meta.get("model_name") in missing:
            forced.append(meta)
    if forced:
        logger.info("[plan] always-on 모델 추가: %s",
                    [m.get("model_name") for m in forced])
    return forced + available


async def _auto(
    query: str,
    uploaded_types: list[str],
    images: list[str] | None = None,
    csv_data: list[dict] | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """LLM 기반 자동 분류 — execution / knowledge / general 중 하나로 라우팅"""
    images = images or []
    csv_data = csv_data or []
    attachments = attachments or []

    # 첨부 파일에서 모달리티를 직접 읽어 검색 질의를 보강한다. 질의 텍스트만으로는
    # "chest CT"라고 써도 임베딩이 흉부 X선 모델을 더 높게 잡는다.
    modality_info = modality_service.from_attachments(attachments)
    hint = modality_service.search_hint(modality_info)
    search_query = f"{hint}. {query}" if hint else query
    if hint:
        logger.info("[plan] modality=%s hint=%r", modality_info.get("modality"), hint)

    wiki_context = wiki_service.search_wiki(query)
    # 계획 경로는 후보를 넓게 받는다. 기본 3개로는 상위가 전부 심장 계열로 차서
    # Support Devices(빈도 29/50, AUROC 0.869)나 Edema가 top7에도 못 든다.
    model_results, knowledge_results, rag_context = await retrieve(search_query, n_models=20)

    SCORE_THRESHOLD = 0.40
    available_models = [
        r["metadata"] for r in model_results
        if r.get("score", 0) >= SCORE_THRESHOLD and r.get("metadata")
    ]
    always_on = ([] if os.getenv("ALWAYS_ON", "on").strip().lower() == "off"
                 else list(_ALWAYS_ON_MODELS.get(modality_info.get("modality") or "", ())))
    available_models = _with_always_on(available_models, modality_info.get("modality"))
    _excl = _excluded_models()
    available_models = [m for m in available_models if m.get("model_name") not in _excl]
    prompt = build_plan_prompt(
        query, _normalize_types(uploaded_types), wiki_context, rag_context,
        available_models=available_models,
        modality=modality_service.context_line(modality_info),
        always_on=always_on,
    )
    raw = await llm_client.generate(prompt, system=SYSTEM_PLAN)
    parsed = _parse_json_response(raw)

    if parsed.get("type") == "execution":
        steps = parsed.get("steps", [])
        missing_inputs = parsed.get("missing_inputs", [])
        return {
            "status": "ready" if not missing_inputs else "missing_inputs",
            "mode": "auto",
            "execution_plan": {"steps": steps},
            "missing_inputs": missing_inputs,
            "message": parsed.get("message", "실행 계획이 수립되었습니다."),
        }
    elif parsed.get("type") == "general":
        return await _general(query, images, csv_data, attachments, uploaded_types)
    else:
        sources = []
        for r in (model_results + knowledge_results):
            src = "wiki" if r["source"] == "maple_models" else "pubmedqa"
            sources.append({"source": src, "text": r["text"][:80], "score": r["score"]})
        return {
            "query_type": "knowledge",
            "mode": "auto",
            "message": parsed.get("message", raw),
            "sources": sources,
        }


# ── interpret ─────────────────────────────────────────────────────────────────

async def interpret(
    query: str,
    task: dict,
    execution_context: dict,
    step_results: list[dict],
) -> dict:
    step_image_logs: list[dict] = []
    if os.getenv("SCOPE_MULTILABEL", "off").strip().lower() == "on":
        step_results = _scope_multilabel(query, step_results)

    # 1. 관련 모델 Wiki 컨텍스트 수집
    model_names = [r.get("model", "") for r in step_results]
    wiki_ctx_parts = []
    for name in model_names:
        resolved = wiki_service.resolve_model(name)
        if resolved:
            page = wiki_service.read_model_page(resolved[0], resolved[1])
            if page:
                wiki_ctx_parts.append(f"### {name}\n{page[:600]}")
    wiki_context = "\n\n".join(wiki_ctx_parts)

    # 2. step_results에서 이미지 추출 (role → data 매핑 유지)
    image_map: dict[str, str] = {}   # role → "data:image/png;base64,..." (원본, 프론트엔드용)
    raw_images: list[str] = []       # 순수 base64 목록 (Ollama VLM용)
    ordered_roles: list[str] = []
    for step in step_results:
        step_roles: list[str] = []
        for img in step.get("images", []):
            if isinstance(img, str):
                role = "output_image"
                data = img
            else:
                role = img.get("role", "")
                data = img.get("data", "")
            if not data:
                continue
            if role:
                if role in image_map:
                    logger.warning("[interpret] duplicate image role '%s' — overwriting with step %s data", role, step.get("step"))
                image_map[role] = data
                ordered_roles.append(role)
                step_roles.append(role)
            raw = data.split(",", 1)[1] if data.startswith("data:") else data
            raw_images.append(raw)
        step_image_logs.append({
            "step": step.get("step"),
            "model": step.get("model", ""),
            "count": len(step_roles),
            "roles": step_roles,
        })

    # 2.5 원본 스캔 이미지를 맨 앞에 둔다.
    # 뒤에 붙이면 모델 출력 이미지(Grad-CAM·오버레이) 4~7장에 가려 Reader가 원본을
    # 거의 읽지 않는다. 실측에서 지원 장치(튜브·카테터)를 VLM은 기술하는데 MARS는
    # 놓쳤고(F1 0.830 vs 0.625), 그 차이가 MIMIC micro-14 격차의 43%를 차지했다.
    original_images: list[str] = []
    for att in (execution_context.get("attachments") or []):
        for img in att.get("images") or []:
            if not isinstance(img, str) or not img:
                continue
            raw = img.split(",", 1)[1] if img.startswith("data:") else img
            if raw:
                original_images.append(raw)
    raw_images = original_images + raw_images
    rag_hint = await _build_rag_hint(query, original_images) if board_service.rag_structured() else None

    image_roles = _unique_preserve_order(ordered_roles)
    logger.info(
        "[interpret] input step image summary=%s",
        step_image_logs,
    )

    board_mode = os.getenv("BOARD_MODE", "on").strip().lower()
    # reader: Board 기여 측정용 — 같은 Reader 프롬프트로 Reader만 돌리고 나머지 역할은 건너뛴다.
    if board_mode not in {"on", "off", "reader"}:
        logger.warning("[interpret] invalid BOARD_MODE=%r; using 'on'", board_mode)
        board_mode = "on"
    if board_mode == "off":
        return await _interpret_without_board(
            query=query,
            task=task,
            execution_context=execution_context,
            step_results=step_results,
            wiki_context=wiki_context,
            raw_images=raw_images,
            image_roles=image_roles,
            image_map=image_map,
            model_names=model_names,
        )

    # 3. Board 실행 (Orchestrator) — Reader→Challenger→Evidence→Guardian→Calibration→Escalation
    #    각 _run_* 은 try/except로 감싸 실패 시 즉시 pending_review 강제 (fail-safe)
    board_failed = False

    try:
        reader_out = await board_service._run_reader(
            query, task, execution_context, step_results, wiki_context,
            raw_images, image_roles, rag_hint=rag_hint,
        )
    except Exception:
        logger.exception("[board] reader failed")
        reader_out, board_failed = _empty_reader(), True

    reader_only = board_mode == "reader"
    if reader_only:
        challenger_out, evidence_out, guardian_out = _empty_challenger(), _empty_evidence(), _empty_guardian()
    elif not board_failed:
        try:
            challenger_out = await board_service._run_challenger(
                query, task, step_results, wiki_context, raw_images,
                alt_model_output=None, rag_hint=rag_hint,
            )
        except Exception:
            logger.exception("[board] challenger failed")
            challenger_out, board_failed = _empty_challenger(), True
    else:
        challenger_out = _empty_challenger()

    if reader_only:
        pass
    elif not board_failed:
        try:
            evidence_out = await board_service._run_evidence(
                query, reader_out.get("claims", []), challenger_out.get("differential", []),
                rag_hint=rag_hint,
            )
        except Exception:
            logger.exception("[board] evidence failed")
            evidence_out, board_failed = _empty_evidence(), True
    else:
        evidence_out = _empty_evidence()

    if reader_only:
        pass
    elif not board_failed:
        try:
            guardian_out = await board_service._run_guardian(
                query, task, step_results, reader_out, challenger_out, evidence_out,
                raw_images, rag_hint=rag_hint,
            )
        except Exception:
            logger.exception("[board] guardian failed")
            guardian_out, board_failed = _empty_guardian(), True
    else:
        guardian_out = _empty_guardian()

    # Calibration
    confidence = _build_confidence(reader_out.get("claims", []), step_results)
    calib = _calibrate(reader_out, challenger_out, guardian_out, confidence)

    # Escalation 게이트
    if board_failed:
        status = "pending_review"
        escalation_reason = "board_component_failure"
    else:
        status, escalation_reason = _decide_escalation(calib)

    # 최종 조립 — UI용 5필드와 기존 markdown 호환 필드를 함께 반환
    english = "english only" in query.lower()
    report_policy = task.get("report_policy") or {}
    structured_result = _synthesize_final(
        reader_out, evidence_out, challenger_out, guardian_out, confidence,
        english=english, target_only=bool(report_policy.get("target_only")),
    )
    interpretation = _render_legacy_interpretation(structured_result, english=english)
    if report_policy.get("target_only"):
        interpretation = _strip_differential_sections(interpretation)

    board = {
        "reader": {
            "finding": reader_out.get("finding", ""),
            "interpretation": reader_out.get("interpretation", ""),
            "recommendation": reader_out.get("recommendation", ""),
            "claims": reader_out.get("claims", []),
        },
        "challenger": {
            "differential": challenger_out.get("differential", []),
            "source": challenger_out.get("source", "blind_same_model"),
        },
        "evidence": {
            "evidence_map": evidence_out.get("evidence_map", []),
            "unsupported_claims": evidence_out.get("unsupported_claims", []),
        },
        "guardian": guardian_out,
        "calibration": {
            "agreement_score": calib.get("agreement_score"),
            "score_gap": calib.get("score_gap"),
            "model_conflict": calib.get("model_conflict", False),
        },
    }

    # 구조화 본문 품질이 부족하면 보수적 폴백 + 사람 검토 요청
    if _needs_structured_result_retry(structured_result):
        logger.warning(
            "[interpret] synthesized text lacks analysis; applying server fallback"
        )
        fallback_text = _build_interpretation_fallback(
            task=task,
            step_results=step_results,
            image_roles=image_roles,
        )
        structured_result = {
            **structured_result,
            "finding": fallback_text,
            "interpretation": "",
            "recommendation": ("Specialist review of the complete original study and clinical "
                               "context is required." if english else
                               "전체 원본 영상과 임상 정보를 바탕으로 전문의 검토가 필요합니다."),
        }
        interpretation = _render_legacy_interpretation(structured_result, english=english)
        status = "pending_review"
        escalation_reason = escalation_reason or "insufficient_board_output"

    interpretation = _ensure_classification_probabilities(interpretation, step_results)
    interpretation = _ensure_all_image_tokens(interpretation, image_roles)
    token_count = len(re.findall(r"\[IMG:[^\]]+\]", interpretation))
    logger.info(
        "[interpret] output token_count=%s image_keys=%s roles=%s status=%s",
        token_count,
        len(image_map),
        image_roles,
        status,
    )

    # 5. Wiki에 해석 패턴 누적 — 파이프라인의 모든 모델에 저장
    for name in dict.fromkeys(n for n in model_names if n):  # 중복 제거, 순서 유지
        resolved = wiki_service.resolve_model(name)
        if resolved:
            project, model_name = resolved
            if os.getenv("WIKI_APPEND", "on").strip().lower() != "off":  # 실험 중에는 off
                wiki_service.append_interpretation(project, model_name, interpretation)
                wiki_service.append_log("interpret", f"{project}/{model_name} 결과 해석 완료")

    return {
        "status": status,
        "result": structured_result,
        "interpretation": interpretation,
        "interpretation_raw": interpretation,
        "board": board,
        "escalation_reason": escalation_reason,
        "images": image_map,
    }


# ── Board 오케스트레이터 헬퍼 ─────────────────────────────────────────────────────

async def _interpret_without_board(
    query: str,
    task: dict,
    execution_context: dict,
    step_results: list[dict],
    wiki_context: str,
    raw_images: list[str],
    image_roles: list[str],
    image_map: dict[str, str],
    model_names: list[str],
) -> dict:
    """BOARD_MODE=off: 변경 전 단일 VLM/LLM interpret 경로."""
    prompt = build_interpret_prompt(
        query, task, execution_context, step_results, wiki_context,
        image_roles=image_roles,
    )
    english = "english only" in query.lower()
    interpret_system = SYSTEM_INTERPRET_EN if english else SYSTEM_INTERPRET
    try:
        if raw_images:
            interpretation = await llm_client.generate_with_images(
                prompt, raw_images, system=interpret_system,
            )
        else:
            interpretation = await llm_client.generate(prompt, system=interpret_system)
    except httpx.HTTPError as exc:
        logger.exception("[interpret:off] VLM/LLM HTTP error; retrying text-only")
        fallback_prompt = (
            f"{prompt}\n\n## 시스템 참고\n"
            "이미지 해석 요청이 실패했습니다. 제공된 모델 출력만으로 보수적으로 해석하세요. "
            f"오류 유형: {type(exc).__name__}"
        )
        try:
            interpretation = await llm_client.generate(
                fallback_prompt, system=interpret_system,
            )
        except Exception:
            logger.exception("[interpret:off] text fallback failed")
            interpretation = ""
    except Exception:
        logger.exception("[interpret:off] generation failed")
        interpretation = ""

    if _needs_interpretation_retry(interpretation):
        # 재시도 지시가 섹션 구성만 요구하면 원 요청의 출력 형식(예: 마지막 줄 확률)이
        # 재생성 과정에서 사라진다. 형식 요구를 다시 명시한다.
        keep_format = ("Also keep any exact output line the User Request demands "
                       "(for example `TARGET_PRESENT_PROBABILITY: <0-100>`) as the very last line."
                       if english else
                       "또한 User Request가 요구한 정확한 출력 줄(예: `TARGET_PRESENT_PROBABILITY: <0-100>`)을 "
                       "응답의 맨 마지막 줄에 그대로 유지하세요.")
        retry_prompt = (f"{prompt}\n\n## Retry instruction\nThe previous response was insufficient. "
                        "Write the complete response in English only and include Summary, Key Imaging "
                        f"Findings, Clinical Interpretation, and Recommendations or Limitations. {keep_format}"
                        if english else f"{prompt}\n\n## 재출력 지시\n이전 응답의 분석이 충분하지 않습니다. "
                        "Summary, Key Imaging Findings, Clinical Interpretation, Recommendations or "
                        f"Limitations를 포함해 다시 작성하세요. {keep_format}")
        try:
            if raw_images:
                interpretation = await llm_client.generate_with_images(
                    retry_prompt, raw_images, system=interpret_system,
                )
            else:
                interpretation = await llm_client.generate(
                    retry_prompt, system=interpret_system,
                )
        except Exception:
            logger.exception("[interpret:off] quality retry failed")

    if _needs_interpretation_retry(interpretation):
        interpretation = _build_interpretation_fallback(
            task=task, step_results=step_results, image_roles=image_roles,
        )
    interpretation = _ensure_classification_probabilities(interpretation, step_results)
    interpretation = _ensure_all_image_tokens(interpretation, image_roles)

    for name in dict.fromkeys(n for n in model_names if n):
        resolved = wiki_service.resolve_model(name)
        if resolved:
            project, model_name = resolved
            if os.getenv("WIKI_APPEND", "on").strip().lower() != "off":  # 실험 중에는 off
                wiki_service.append_interpretation(project, model_name, interpretation)
                wiki_service.append_log("interpret", f"{project}/{model_name} 결과 해석 완료")

    return {
        "status": "confirmed",
        "result": {
            "finding": interpretation,
            "interpretation": "",
            "recommendation": "",
            "risk_tier": "moderate",
            "confidence": {
                "display": None,
                "source_model": None,
                "task_type": None,
                "model_scores": [],
                "conflict": False,
                "score_gap": None,
            },
        },
        "interpretation": interpretation,
        "interpretation_raw": interpretation,
        "board": {},
        "escalation_reason": None,
        "images": image_map,
    }

def _empty_reader() -> dict:
    return {
        "finding": "", "interpretation": "", "recommendation": "",
        "claims": [], "sentence_map": [],
    }


def _empty_challenger() -> dict:
    return {"differential": [], "source": "blind_same_model"}


def _empty_evidence() -> dict:
    return {"evidence_map": [], "unsupported_claims": []}


def _empty_guardian() -> dict:
    return {
        "veto": False, "risk_tier": "moderate", "flags": [],
        "rationale": "", "evidence_refs": [],
    }


_BOARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "board")


def _load_thresholds() -> dict:
    """board/thresholds.yaml 로드."""
    path = os.path.join(_BOARD_DIR, "thresholds.yaml")
    out = {
        "score_gap_max": 0.2,
    }
    try:
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            out.update(loaded)
    except Exception:
        logger.exception("[board] thresholds.yaml load failed; using defaults")
    return out


def _normalize_label(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(text or "").strip().lower())


def _iter_pred_entries(preds):
    """다양한 predictions 스키마에서 (label, score) 후보를 산출.
    - [{"label": "...", "score"/"probability"/"confidence": 0.x}, ...]
    - {"label": score, ...} (dict of label→prob)
    """
    if isinstance(preds, list):
        for p in preds:
            if isinstance(p, dict):
                label = (
                    p.get("label") or p.get("class") or p.get("name")
                    or p.get("finding") or p.get("pred_name")
                )
                score = p.get("score")
                if score is None:
                    score = p.get("probability")
                if score is None:
                    score = p.get("confidence")
                if score is None:
                    score = p.get("prob")
                if score is None:
                    score = p.get("conf")
                yield label, score
    elif isinstance(preds, dict):
        for k, v in preds.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                yield k, v


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _step_task_type(step: dict) -> str:
    explicit = str(step.get("task_type") or "").strip().lower()
    if explicit:
        if "classif" in explicit:
            return "classification"
        if "detect" in explicit or "bbox" in explicit:
            return "detection"
        if "segment" in explicit:
            return "segmentation"
        return explicit
    result_type = str(step.get("result_type") or "").lower()
    if "classification" in result_type or "probabil" in result_type:
        return "classification"
    if "detection" in result_type or "bbox" in result_type:
        return "detection"
    if "segmentation" in result_type:
        return "segmentation"
    return "unknown"


def _labels_match(a: str, b: str) -> bool:
    a_norm, b_norm = _normalize_label(a), _normalize_label(b)
    if not a_norm or not b_norm:
        return False
    return a_norm == b_norm or a_norm in b_norm or b_norm in a_norm


def _build_confidence(claims: list[dict], step_results: list[dict]) -> dict:
    """UI confidence와 동일-태스크 모델 이견을 계산한다.

    classification은 질환 확률의 대표값 후보이고 detection confidence는 근거
    위치의 신뢰도이므로 서로 score_gap을 계산하지 않는다.
    """
    claim_labels = [
        str(c.get("label", "")) for c in claims
        if isinstance(c, dict) and c.get("label")
    ]
    primary_label = claim_labels[0] if claim_labels else None
    scores: list[dict] = []
    if primary_label is None:
        return {
            "display": None, "source_model": None, "task_type": None,
            "model_scores": [], "conflict": False, "score_gap": None,
        }
    for step in step_results:
        task_type = _step_task_type(step)
        for label, score in _iter_pred_entries(step.get("predictions")):
            if label is None or not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            if not _labels_match(str(label), primary_label):
                continue
            scores.append({
                "model": str(step.get("model") or ""),
                "task_type": task_type,
                "label": str(label),
                "score": float(score),
            })

    classification_scores = [s for s in scores if s["task_type"] == "classification"]
    comparable = classification_scores
    gap = None
    conflict = False
    comparable_model_count = len({s["model"] for s in comparable})
    if comparable_model_count >= 2:
        values = [s["score"] for s in comparable]
        gap = max(values) - min(values)
        conflict = gap > float(_load_thresholds().get("score_gap_max", 0.2))

    # 동일 classification 모델이 둘 이상이면 임의의 대표값이나 평균으로 이견을
    # 숨기지 않고 model_scores를 그대로 노출한다.
    display_source = (
        classification_scores[0]
        if classification_scores and comparable_model_count < 2
        else None
    )
    return {
        "display": display_source["score"] if display_source else None,
        "source_model": display_source["model"] if display_source else None,
        "task_type": "classification" if display_source else None,
        "model_scores": scores,
        "conflict": conflict,
        "score_gap": gap,
    }


def _calibrate(
    reader_out: dict,
    challenger_out: dict,
    guardian_out: dict,
    confidence: dict,
) -> dict:
    """역할 간 일치는 관찰값, 동일 classification 모델의 score gap은 게이트 신호."""
    reader_labels = {
        _normalize_label(c.get("label", ""))
        for c in reader_out.get("claims", []) if isinstance(c, dict) and c.get("label")
    }
    challenger_labels = {
        _normalize_label(d.get("label", ""))
        for d in challenger_out.get("differential", []) if isinstance(d, dict) and d.get("label")
    }
    agreement_score = _jaccard(reader_labels, challenger_labels)

    return {
        "agreement_score": agreement_score,
        "score_gap": confidence.get("score_gap"),
        "model_conflict": bool(confidence.get("conflict")),
        "veto": bool(guardian_out.get("veto", False)),
        "risk_tier": guardian_out.get("risk_tier", "moderate"),
    }


def _decide_escalation(calib: dict) -> tuple[str, str | None]:
    """트리거 = Guardian veto/high-risk OR 동일 classification 모델 score gap."""
    thresholds = _load_thresholds()
    score_gap_max = thresholds.get("score_gap_max", 0.2)

    veto = bool(calib.get("veto", False))
    risk_tier = calib.get("risk_tier")
    score_gap = calib.get("score_gap")

    if veto:
        return "pending_review", "guardian_veto"
    if risk_tier in {"high", "critical"}:
        return "pending_review", "high_risk"
    # label 문자열 Jaccard는 관찰 지표로만 보존한다. 동의어/표현 차이를 임상 이견으로
    # 오판할 수 있어 단독 escalation 근거로 사용하지 않는다.
    if isinstance(score_gap, (int, float)) and score_gap > score_gap_max:
        return "pending_review", "model_disagreement"
    return "confirmed", None


def _synthesize_final(
    reader_out: dict,
    evidence_out: dict,
    challenger_out: dict,
    guardian_out: dict,
    confidence: dict,
    english: bool = False,
    target_only: bool = False,
) -> dict:
    """Reader 3필드를 근거 검증 후 UI용 5필드로 조립한다."""
    fields = {
        key: str(reader_out.get(key, "") or "")
        for key in ("finding", "interpretation", "recommendation")
    }
    unsupported = {
        _normalize_label(c) for c in evidence_out.get("unsupported_claims", []) if c
    }
    if unsupported:
        sentence_map = reader_out.get("sentence_map", [])
        for entry in sentence_map:
            if not isinstance(entry, dict):
                continue
            claim_label = _normalize_label(entry.get("claim_label", ""))
            sentence = entry.get("sentence", "")
            if sentence and (claim_label in unsupported):
                field = entry.get("field")
                targets = [field] if field in fields else list(fields)
                # 판독문 문체에서는 표시 없이 지운다. "(Withheld …: atelectasis)" 문구가 남으면
                # 라벨러가 그 소견을 양성으로 읽는다(MIMIC 62건).
                note = "" if _report_style_enabled() else (
                    f"(Withheld because supporting evidence was not verified: {claim_label})"
                    if english else f"(근거 미확인으로 보류됨: {claim_label})")
                for target in targets:
                    fields[target] = re.sub(r"  +", " ", fields[target].replace(sentence, note))

    # Broad interactive reports retain the differential section. A caller that
    # explicitly requests a single-target report must not have Challenger's
    # low-probability alternatives promoted into human-facing findings.
    # REPORT_STYLE 평가에서는 정답 판독문과 대조 범위를 맞춘다. 감별진단은 정답
    # 판독문에 없는 내용이라, 포함하면 전부 개체 불일치로 잡힌다.
    suppress_differential = target_only or _report_style_enabled()
    differential = [] if suppress_differential else challenger_out.get("differential", [])
    if differential:
        lines = ["\n\n### Differential considerations" if english else "\n\n### 감별진단 고려"]
        for d in differential:
            if isinstance(d, dict):
                label = d.get("label", "")
                rationale = d.get("rationale", "")
                if label:
                    lines.append(f"- {label}" + (f": {rationale}" if rationale else ""))
        if len(lines) > 1:
            fields["interpretation"] = fields["interpretation"].rstrip() + "\n".join(lines)

    # Reader가 보고한 0-100 확신도는 3필드 조립 과정에서 사라진다. 별도 필드로 보존해
    # 최종 렌더링이 마지막 줄에 복원할 수 있게 한다.
    reader_confidence = reader_out.get("confidence_0_100")
    try:
        reader_confidence = max(0, min(100, int(float(reader_confidence))))
    except (TypeError, ValueError):
        reader_confidence = None

    return {
        **{key: value.strip() for key, value in fields.items()},
        "risk_tier": guardian_out.get("risk_tier", "moderate"),
        "confidence": confidence,
        "reader_confidence_0_100": reader_confidence,
    }


def _render_legacy_interpretation(result: dict, english: bool = False) -> str:
    sections = ([
        ("Findings", result.get("finding", "")),
        ("Clinical Interpretation", result.get("interpretation", "")),
        ("Recommendations and Limitations", result.get("recommendation", "")),
    ] if english else [
        ("소견", result.get("finding", "")),
        ("임상적 해석", result.get("interpretation", "")),
        ("권장조치 및 한계", result.get("recommendation", "")),
    ])
    text = "\n\n".join(
        f"## {title}\n{body}".rstrip() for title, body in sections if body
    ).strip()
    score = result.get("reader_confidence_0_100")
    if isinstance(score, int):
        # ANSWER는 확률에서 유도한다. Reader에게 따로 물으면 두 값이 어긋날 수 있는데,
        # 채점은 확률을 쓰므로 불일치가 남으면 리포트 본문과 점수가 모순돼 보인다.
        verdict = "POSITIVE" if score >= 50 else "NEGATIVE"
        text = (f"{text}\n\nANSWER: {verdict}"
                f"\nTARGET_PRESENT_PROBABILITY: {score}")
    return text


def _strip_differential_sections(text: str) -> str:
    """Remove generated differential blocks from an explicit target-only report."""
    pattern = re.compile(
        r"\n#{1,6}\s*(?:Differential considerations|Differential diagnosis|감별진단 고려)"
        r"[^\n]*\n.*?(?=\n##\s|\nANSWER:|\Z)",
        re.I | re.S,
    )
    return pattern.sub("", text).strip()



def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _ensure_all_image_tokens(text: str, roles: list[str]) -> str:
    if not roles:
        return text

    ordered_roles = sort_image_roles(_unique_preserve_order(roles))
    missing = [role for role in ordered_roles if f"[IMG:{role}]" not in text]
    if not missing:
        return text

    appendix = "\n\n## Attached Images\n" + "\n".join(f"[IMG:{role}]" for role in missing)
    return text.rstrip() + appendix


def _ensure_classification_probabilities(text: str, step_results: list[dict]) -> str:
    """Preserve structured case-level probabilities when prose omits them."""
    has_numeric_probability = bool(
        re.search(r"\bprobabilit(?:y|ies)\b[^\n]{0,100}\d+(?:\.\d+)?", text, re.I)
        or (re.search(r"\|[^\n]*\bprobability\b[^\n]*\|", text, re.I)
            and re.search(r"\|\s*\d+(?:\.\d+)?%?\s*\|", text))
    )
    if has_numeric_probability:
        return text

    rows = []
    for step in step_results:
        for prediction in step.get("predictions", []) or []:
            probability = prediction.get("probability", prediction.get("prob"))
            if isinstance(probability, (int, float)):
                rows.append(
                    f"| {step.get('model', 'model')} | {prediction.get('label', 'Finding')} | "
                    f"{probability:.3f} |"
                )
    if not rows:
        return text
    appendix = (
        "\n\n## Structured Classification Evidence\n"
        "| Model | Finding | Probability |\n"
        "| :--- | :--- | ---: |\n" + "\n".join(rows)
    )
    return text.rstrip() + appendix


def _needs_interpretation_retry(text: str) -> bool:
    stripped = re.sub(r"\[IMG:[^\]]+\]", "", text)
    stripped = re.sub(r"#+\s*", "", stripped)
    stripped = stripped.strip()
    if len(stripped) < 120:
        return True

    section_hits = sum(
        1
        for section in (
            "Summary",
            "Key Imaging Findings",
            "Clinical Interpretation",
            "Recommendations",
            "Limitations",
        )
        if section.lower() in text.lower()
    )
    if section_hits < 3:
        return True

    sentence_count = len(re.findall(r"[.!?]\s+|[다요]\n|[다요]\s", stripped))
    return sentence_count < 4


def _needs_structured_result_retry(result: dict) -> bool:
    fields = [
        re.sub(r"\[IMG:[^\]]+\]", "", str(result.get(key, ""))).strip()
        for key in ("finding", "interpretation", "recommendation")
    ]
    return not all(fields) or sum(len(field) for field in fields) < 120


def _build_interpretation_fallback(task: dict, step_results: list[dict], image_roles: list[str]) -> str:
    task_label = " / ".join(v for v in [task.get("department", ""), task.get("project", "")] if v) or "this examination"
    model_names = ", ".join(r.get("model", "") for r in step_results if r.get("model")) or "the model"
    result_types = ", ".join(sorted({r.get("result_type", "") for r in step_results if r.get("result_type")})) or "outputs"
    token_preview = "\n".join(f"[IMG:{role}]" for role in sort_image_roles(image_roles[:4]))
    if token_preview:
        token_preview = f"\n{token_preview}"

    prediction_rows = []
    measurement_rows = []
    for step in step_results:
        model = step.get("model", "model")
        for prediction in step.get("predictions", []) or []:
            probability = prediction.get("probability", prediction.get("prob"))
            if not isinstance(probability, (int, float)):
                continue
            threshold = prediction.get("threshold")
            predicted = prediction.get("pred_name")
            if not predicted:
                predicted = "Positive" if prediction.get("pred") == 1 else "Negative"
            threshold_text = f"{threshold:.3f}" if isinstance(threshold, (int, float)) else "—"
            prediction_rows.append(
                f"| {model} | {prediction.get('label', 'Finding')} | {probability:.3f} | "
                f"{threshold_text} | {predicted} |"
            )
        measurements = (step.get("model_output") or {}).get("measurements", []) or []
        for measurement in measurements:
            label = measurement.get("label", "measurement")
            quality = measurement.get("volume_quality")
            values = []
            if quality != "rejected_implausible_lung_volume":
                for key, value in measurement.items():
                    if (isinstance(value, (int, float)) and not isinstance(value, bool)
                            and key.endswith(("_ml", "_mm", "_count", "_pct_of_lung"))):
                        values.append(f"{key}={value:g}")
            if values:
                measurement_rows.append(f"- **{model} / {label}:** " + ", ".join(values))
            elif quality:
                reason = measurement.get("geometry_rejection_reason", "physical geometry was not reliable")
                measurement_rows.append(f"- **{model} / {label}:** quantification withheld ({quality}); {reason}")

    evidence = ""
    if prediction_rows:
        evidence += (
            "\n\n### Classification probabilities\n"
            "| Model | Finding | Probability | Threshold | Prediction |\n"
            "| :--- | :--- | ---: | ---: | :--- |\n" + "\n".join(prediction_rows)
        )
    if measurement_rows:
        evidence += "\n\n### Quantitative measurements\n" + "\n".join(measurement_rows)

    return (
        f"## Summary\n"
        f"For {task_label}, an interpretation was generated based on {result_types} from {model_names}. "
        f"The following conservative report preserves the available structured model evidence."
        f"{evidence}{token_preview}\n\n"
        f"## Key Imaging Findings\n"
        f"The attached visual outputs show the location and extent of the regions segmented or detected by the model. "
        f"The exact morphology, boundaries, and distribution should be verified against the original images, and a definitive conclusion should not be made from a single image or a limited number of slices alone.\n\n"
        f"## Clinical Interpretation\n"
        f"These results are best interpreted as supportive information for identifying the presence and relative distribution of the target structures or findings. "
        f"Their clinical meaning is strengthened when integrated with symptoms, the full imaging volume, and additional radiology interpretation.\n\n"
        f"## Recommendations or Limitations\n"
        f"Review the complete original imaging study and contiguous slices, and supplement the result with quantitative metrics or specialist interpretation when needed. "
        f"A diagnosis should not be established solely from the automated output."
    )

def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if "```" in raw:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start != -1 and end > start:
            raw = raw[start:end]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"type": "knowledge", "message": raw}
