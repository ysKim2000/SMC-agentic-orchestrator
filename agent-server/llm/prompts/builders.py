# llm/prompts/builders.py

import os

from .schemas import BOARD_READER_OUTPUT_SCHEMA_TEXT, PLAN_OUTPUT_SCHEMA_TEXT
from .utils import format_metadata, format_value, sort_image_roles, to_pretty_json


def _target_only_policy(task: dict) -> str:
    """Optional policy for pre-specified single-finding evaluation tasks.

    This is driven only by the requested target, never by a reference label or
    test outcome. Normal interactive reports retain their broad-report behavior.
    """
    policy = task.get("report_policy") or {}
    if not policy.get("target_only"):
        return ""
    target = task.get("evaluation_scope") or "the finding named in the User Request"
    evidence = task.get("target_evidence_summary") or []
    evidence_text = to_pretty_json(evidence) if evidence else "No target-specific structured vote was supplied."
    return f"""
## Single-target reporting policy
The evaluation scope is exactly: **{target}**.
Target-specific structured evidence (derived without using any reference label):
```json
{evidence_text}
```
- Make clinical assertions only about this target and its directly linked localization or measurement.
- Do not promote other model labels, low-probability outputs, or differential diagnoses into findings.
- A below-threshold output is not evidence that a trace/possible target is present.
- Assert the target as present only when target-specific supplied evidence supports it.
- If target-specific models conflict, describe the conflict without turning either side into an additional clinical finding; use the stronger target-specific evidence and otherwise remain explicitly indeterminate.
- Keep the narrative conclusion, `confidence_0_100`, and the requested final ANSWER mutually consistent.
"""


def build_plan_prompt(
    query: str,
    uploaded_types: list[str],
    wiki_context: str,
    rag_context: str,
    available_models: list[dict] | None = None,
    mode: str = "auto",
    modality: str = "",
    always_on: list[str] | None = None,
) -> str:
    uploaded_str = ", ".join(uploaded_types) if uploaded_types else "없음"
    available_models = available_models or []
    available_models_str = to_pretty_json(available_models)
    always_on_section = ""
    if always_on:
        # 후보 목록에 넣기만 하면 LLM이 지나친다. 반드시 고르라고 따로 지시한다.
        always_on_section = (
            "\n## Always-Run Models\n"
            "These models MUST be included in the execution steps for this modality, "
            "regardless of what the request emphasizes. Their findings are common and "
            "easily missed, so they are screened on every study:\n"
            + "".join(f"- {n}\n" for n in always_on)
        )
    modality_section = f"\n## Detected Input Modality\n{modality}\n" if modality else ""

    return f"""## User Request
{query}

## Selected Mode
{mode}

## Uploaded Data Types
{uploaded_str}
{modality_section}
## Available Registered Models
```json
{available_models_str}
```
{always_on_section}
## Wiki Reference
{wiki_context if wiki_context else "관련 Wiki 정보 없음"}

## RAG Search Results
{rag_context if rag_context else "관련 논문/QA 없음"}

## Instructions
Based on the information above, determine which of the following request types applies:
1. **execution** — requires running one or more registered specialized AI models
2. **knowledge** — a clinical/medical knowledge or concept question
3. **general** — broad or comprehensive analysis ("전반적으로", "종합적으로") without a specific registered model

Mode-specific routing rules:
- If mode is "auto": classify freely using the criteria above. If uploaded data exists but no matching registered model is found, prefer "general" over "execution".
- If mode is "prediction": always return "execution". Select only from the models listed in "Available Registered Models". If no suitable model is found, set missing_inputs to explain why.
- If mode is "clinical": always return "knowledge".
- If mode is "general": always return "general".

Ordering: list the steps in order of clinical usefulness for the request. Models that
directly answer the question — disease classifiers and lesion segmenters for the findings
being asked about — come first. Generic anatomy tools (whole-organ masks, skeletal or
vessel segmentation) that do not by themselves report a finding come last, and only when
they add measurement context. A caller may execute only the first few steps, so a model
placed late may never run.

Critical constraint: You must only use model names, departments, and project names that appear exactly in the "Available Registered Models" list above. Do not invent or guess model names.

Return only valid JSON matching one of the schemas below. No markdown fences, no extra text.

{PLAN_OUTPUT_SCHEMA_TEXT}"""


def build_clinical_prompt(query: str, wiki_context: str, rag_context: str) -> str:
    return f"""## Clinical Question
{query}

## Wiki Reference
{wiki_context if wiki_context else "관련 Wiki 정보 없음"}

## Reference Literature / QA
{rag_context if rag_context else "관련 논문/QA 없음"}

## Instructions
Answer the clinical question based on the Wiki reference and literature/QA evidence above.
- Explain supporting evidence specifically when available; clearly state uncertainty when not.
- Do not mention AI model routing, execution logic, or system implementation details.
- Write in natural Korean."""


def build_interpret_prompt(
    query: str,
    task: dict,
    execution_context: dict,
    step_results: list[dict],
    wiki_context: str,
    image_roles: list[str] | None = None,
) -> str:
    task_str = f"{task.get('department', '')} / {task.get('project', '')}"
    mode = execution_context.get("mode", "")
    plan = execution_context.get("plan", {})
    plan_str = to_pretty_json(plan) if plan else "없음"

    results_str = ""
    for r in step_results:
        results_str += f"\n### Step {r.get('step', '?')} - {r.get('model', '알 수 없음')}\n"
        role = r.get("evidence_role") or ""
        if role:
            results_str += f"- evidence_role: {role}\n"
        results_str += f"- result_type: {r.get('result_type', '')}\n"
        results_str += f"- predictions: {format_value(r.get('predictions'))}\n"
        if r.get("model_output"):
            results_str += f"- model_output: {format_value(r['model_output'])}\n"
        for img in r.get("images", []):
            if isinstance(img, str):
                results_str += "- 이미지 첨부: role=output_image (VLM 참조)\n"
            else:
                results_str += f"- 이미지 첨부: role={img.get('role', '')} (VLM 참조)\n"

    # 원본 스캔 컨텍스트 (general 종합판독) — execution_context.attachments(_meta)
    orig_source = execution_context.get("attachments") or execution_context.get("attachments_meta") or []
    orig_section = ""
    if orig_source:
        orig_lines = []
        total_imgs = 0
        for idx, att in enumerate(orig_source, 1):
            fname = att.get("filename") or f"attachment_{idx}"
            atype = att.get("type") or "unknown"
            meta = att.get("metadata") or {}
            line = f"- {fname} ({atype})"
            if meta:
                line += f": {format_metadata(meta)}"
            orig_lines.append(line)
            total_imgs += len(att.get("images") or [])
        note = f"\n원본 스캔 이미지 {total_imgs}장이 모델 결과 이미지 뒤에 함께 제공됩니다." if total_imgs else ""
        orig_section = "\n## Original Scan Context\n" + "\n".join(orig_lines) + note + "\n"

    image_roles = image_roles or []
    image_instruction = ""
    if image_roles:
        ordered_roles = sort_image_roles(image_roles)
        roles_str = "\n".join(f"  - {r}" for r in ordered_roles)
        image_instruction = f"""
## Available Images
{roles_str}

## Image Insertion Rules
Insert `[IMG:role]` markers only where the image genuinely aids understanding of the finding being described:
- Only insert an image when the surrounding text directly references or is explained by that image. Do not insert images just because they exist.
- Each role should appear at most once. Only repeat a role if a clearly separate finding in a different section cannot be understood without it.
- Place each marker on its own line, immediately after the sentence it supports.
- Use the role name exactly as provided — do not rename or abbreviate.
- Do not use HTML tags (`<figure>`, `<img>`); use only `[IMG:role]` tokens.
"""

    output_language = "English only" if "english only" in query.lower() else "natural Korean"
    return f"""## User Request
{query}

## Execution Target
{task_str} (mode: {mode})

## Execution Plan
{plan_str}

## Model Inference Results
{results_str}

## Wiki Reference
{wiki_context if wiki_context else "관련 Wiki 정보 없음"}
{orig_section}{image_instruction}
## Instructions
Interpret the inference results and images above, and write a clinical report for medical professionals.
- Use the original scan context (modality, body part, age, sex, etc.) as clinical grounding when interpreting the model results.
- Explain the clinical significance of each step's result.
- When steps carry an evidence_role, base the primary conclusion on the model marked `primary`
  and use `supporting` models to corroborate, qualify, or contradict it. If they disagree, say so
  explicitly and explain which evidence is stronger and why — do not silently drop either result.
- Use probability values, model outputs (ROI, Grad-CAM, etc.) as the basis for findings.
- Do not overstate the AI result as a definitive diagnosis; include uncertainty and limitations.
- Structured data (classifications, per-sample results, interval aggregates, probability comparisons) should be presented as a markdown table first, followed by an interpretation paragraph.
- Only insert `[IMG:role]` tokens where the image directly supports the finding described in that sentence. Do not insert images out of completeness.
- Prefer inserting each role once; repeat only if a separate section genuinely cannot be understood without seeing the image again.
- Do not include HTML in the response.
- Include recommended follow-up examinations or clinical considerations.
Write in {output_language}.

## Required Output Format
Re-read the User Request at the top. If it specifies an exact line or token that the answer
must contain (for example `TARGET_PRESENT_PROBABILITY: <0-100>`), reproduce that line verbatim as the very
last line of your response, after every section above. This is mandatory — a response
missing it is unusable."""


# ── Clinical Board builders (v4) ───────────────────────────────────────────────

def _positivity_summary(predictions) -> str:
    """REPORT_STYLE 전용 — 예측을 양성/음성으로 갈라 한 줄로 요약한다.

    raw JSON만 주면 LLM이 threshold 미만 예측까지 소견으로 승격시킨다. 어느 쪽이
    양성인지는 pred/threshold에 이미 있는 정보이므로, 읽기 쉬운 형태로 다시 제시할
    뿐 새로운 판단을 더하지 않는다.
    """
    if not isinstance(predictions, list):
        return ""
    positive, negative = [], []
    for item in predictions:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not label:
            continue
        probability = item.get("prob", item.get("probability"))
        threshold = item.get("threshold")
        flag = item.get("pred")
        if flag is None and isinstance(probability, (int, float)) and isinstance(threshold, (int, float)):
            flag = 1 if probability >= threshold else 0
        (positive if flag == 1 else negative).append(str(label))
    if not positive and not negative:
        return ""
    # REPORT_NEGATIVES=omit: 음성 목록을 주지 않는다. 목록을 주면 Reader가 "No X, Y, or Z"로
    # 옮겨 적는데, CT-RATE 공식 RadBERT 라벨러가 이런 부정문을 양성으로 읽어 CT 오탐의
    # 34%(3,250건)가 여기서 나왔다. 정답 판독문도 없는 소견을 일일이 나열하지 않는다.
    if os.getenv("REPORT_NEGATIVES", "list").strip().lower() == "omit":
        return (f"- POSITIVE (report these): {', '.join(positive) if positive else 'none'}\n"
                "- All other model labels are negative. Do not mention them, and do not "
                "write sentences that list findings as absent.\n")
    return (f"- POSITIVE (report these): {', '.join(positive) if positive else 'none'}\n"
            f"- NEGATIVE (below threshold \u2014 do not report as present): "
            f"{', '.join(negative) if negative else 'none'}\n")


def _format_step_results(step_results: list[dict]) -> str:
    """step_results를 프롬프트용 텍스트로 조립 (build_interpret_prompt에서 이식)."""
    results_str = ""
    for r in step_results:
        results_str += f"\n### Step {r.get('step', '?')} - {r.get('model', '알 수 없음')}\n"
        role = r.get("evidence_role") or ""
        if role:
            results_str += f"- evidence_role: {role}\n"
        results_str += f"- result_type: {r.get('result_type', '')}\n"
        results_str += f"- predictions: {format_value(r.get('predictions'))}\n"
        if _report_style_enabled():
            results_str += _positivity_summary(r.get("predictions"))
        if r.get("model_output"):
            results_str += f"- model_output: {format_value(r['model_output'])}\n"
        for img in r.get("images", []):
            if isinstance(img, str):
                results_str += "- 이미지 첨부: role=output_image (VLM 참조)\n"
            else:
                results_str += f"- 이미지 첨부: role={img.get('role', '')} (VLM 참조)\n"
    return results_str


def _format_original_scan_context(execution_context: dict) -> str:
    """원본 스캔 컨텍스트 섹션 (build_interpret_prompt에서 이식)."""
    orig_source = execution_context.get("attachments") or execution_context.get("attachments_meta") or []
    if not orig_source:
        return ""
    orig_lines = []
    total_imgs = 0
    for idx, att in enumerate(orig_source, 1):
        fname = att.get("filename") or f"attachment_{idx}"
        atype = att.get("type") or "unknown"
        meta = att.get("metadata") or {}
        line = f"- {fname} ({atype})"
        if meta:
            line += f": {format_metadata(meta)}"
        orig_lines.append(line)
        total_imgs += len(att.get("images") or [])
    note = f"\n원본 스캔 이미지 {total_imgs}장이 모델 결과 이미지 뒤에 함께 제공됩니다." if total_imgs else ""
    return "\n## Original Scan Context\n" + "\n".join(orig_lines) + note + "\n"


def _image_instruction(image_roles: list[str] | None) -> str:
    """[IMG:role] 삽입 규칙 섹션 (build_interpret_prompt에서 이식)."""
    image_roles = image_roles or []
    if not image_roles:
        return ""
    ordered_roles = sort_image_roles(image_roles)
    roles_str = "\n".join(f"  - {r}" for r in ordered_roles)
    return f"""
## Available Images
{roles_str}

## Image Insertion Rules
Insert `[IMG:role]` markers only where the image genuinely aids understanding of the finding being described:
- Only insert an image when the surrounding text directly references or is explained by that image. Do not insert images just because they exist.
- Each role should appear at most once. Only repeat a role if a clearly separate finding in a different section cannot be understood without it.
- Place each marker on its own line, immediately after the sentence it supports.
- Use the role name exactly as provided — do not rename or abbreviate.
- Do not use HTML tags (`<figure>`, `<img>`); use only `[IMG:role]` tokens.
"""


def _report_style_enabled() -> bool:
    """REPORT_STYLE=on: Reader writes a radiology-report-style narrative.

    Used for report-level evaluation against reference reports, where probability
    figures and image markers are formatting artifacts rather than clinical content.
    Default is off, so interactive behavior is unchanged.
    """
    value = os.getenv("REPORT_STYLE", "off").strip().lower()
    return value == "on"


_READER_FIELDS_DEFAULT = """- `finding`: observed findings and structured model results. Put probability tables here when useful.
- `interpretation`: clinical meaning, uncertainty, and relevant differential considerations.
- `recommendation`: recommended follow-up, clinical correlation, and limitations.
- Use the original scan context (modality, body part, age, sex, etc.) as clinical grounding.
- Explain the clinical significance of each step's result, using probability values and model outputs (ROI, Grad-CAM, etc.) as the basis for findings.
- Do not overstate the AI result as a definitive diagnosis; include uncertainty and limitations.
- Insert `[IMG:role]` only inside the field whose surrounding text directly discusses that image, each role preferably once. Do not include HTML."""

_READER_FIELDS_REPORT_STYLE = """- `finding`: the observed findings, written as a radiology report FINDINGS section. Plain prose sentences only.
- `interpretation`: the clinical impression, written as a radiology report IMPRESSION section.
- `recommendation`: recommended follow-up, clinical correlation, and limitations.
- Use the original scan context (modality, body part, age, sex, etc.) as clinical grounding.
- Use the model outputs (probabilities, ROI, Grad-CAM, segmentation) as the basis for your findings, but report only the clinical findings themselves. Do not write numeric probabilities, scores, thresholds, or model names into the text.
- The FIRST image is the original scan; the images after it are model outputs (Grad-CAM, overlays). Read the original scan yourself before you use any model output.
- Two separate duties, and both are required:
  (a) For findings the models cover, report one as present ONLY when its model output is positive (above its threshold). A prediction below its threshold is a negative result: state it as a negative or leave it out. Never turn a low-scoring prediction into a hedged positive ("possible", "cannot be excluded", "suggestive of").
  (b) For everything the models do NOT cover, describe what you see in the original scan. This is not optional and is not limited by the model label set: support devices and hardware, post-surgical change, and the projection.
- Support devices must be named individually, each in its own clause, with where it ends. Scan the image for every one of: endotracheal tube, tracheostomy tube, nasogastric or orogastric tube, central venous catheter, PICC, Port-A-Cath, dialysis catheter, chest tube, pacemaker or ICD with its leads, sternotomy wires, surgical clips, ECG leads. Write them the way a radiologist dictates:
    "An endotracheal tube terminates 4 cm above the carina."
    "A nasogastric tube courses below the diaphragm with tip in the stomach."
    "A right internal jugular central venous catheter tip projects over the SVC."
    "Median sternotomy wires are intact."
  Do NOT write vague summaries such as "support devices are present", "lines and leads are noted", or "multiple monitoring devices overlie the chest" — name the device.
  If you see no device at all, simply omit the topic — do NOT write a sentence that names
  devices in order to deny them.
- Do not overstate the AI result as a definitive diagnosis; include uncertainty and limitations.
- Do not use markdown headers, bullet lists, or tables in these fields. Do not insert `[IMG:role]` markers or HTML.
- State clearly relevant negatives as a report would; keep the text concise and comparable in length to a routine report."""


def _knowledge_section(knowledge_context: str) -> str:
    """RAG_QUERY=structured일 때 Reader에 주는 일반 의학 지식. 배경지식일 뿐 소견의 근거가 아니다."""
    if not knowledge_context:
        return ""
    return ("\n## Retrieved Medical Knowledge (background only)\n"
            "General medical knowledge retrieved for this request. Use it to understand how findings "
            "appear and what they mean. It says nothing about this patient: never report a finding "
            "because it appears here.\n\n" + knowledge_context + "\n")


def _reader_field_instructions(report_style: bool) -> str:
    if not report_style:
        return _READER_FIELDS_DEFAULT
    text = _READER_FIELDS_REPORT_STYLE
    # 장치 지시의 부정문 금지 근거: "No endotracheal tube, ... is identified"처럼 쓰면
    # 실제 있는 봉합선·리드는 라벨러가 세지 않고 부정문 때문에 음성으로 확정돼
    # GT 양성 3건이 틀렸다(MIMIC n=50).
    if os.getenv("REPORT_NEGATIVES", "list").strip().lower() == "omit":
        text = text.replace(
            "A prediction below its threshold is a negative result: state it as a negative or leave it out.",
            "A prediction below its threshold is a negative result: leave it out entirely.")
        text = text.replace(
            "- State clearly relevant negatives as a report would; keep the text concise",
            "- Describe only what is present. Do not write sentences that list findings as absent "
            "(\"no X, Y, or Z\"); a study with nothing abnormal may be summarized in one sentence. "
            "Keep the text concise")
    # READER_IMAGE_FINDINGS=on: 모델이 다루지 않는 소견도 영상에서 분명히 보이면 쓴다.
    # 기존 (b)는 장치·수술 흔적·촬영 방향만 허용해, 모델 근거가 없는 소견은 보여도 쓰지 않았다
    # (다중 라벨 모델을 빼자 묻지 않은 소견 recall 0.44 → 0.20).
    if os.getenv("READER_IMAGE_FINDINGS", "off").strip().lower() == "on":
        text = text.replace(
            "  (b) For everything the models do NOT cover, describe what you see in the original scan. "
            "This is not optional and is not limited by the model label set: support devices and "
            "hardware, post-surgical change, and the projection.",
            "  (b) For everything the models do NOT cover, read the original scan yourself and describe what "
            "is clearly visible. This is not optional and is not limited by the model label set: abnormal "
            "findings outside the models' labels, support devices and hardware, post-surgical change, and the "
            "projection. Report such a finding only when it is clearly visible; do not speculate.")
    # IMPRESSION_STRICT=on: Impression은 소견 요약만. "불확실성·한계를 쓰라"는 지시와 질의의
    # "추측성 서술 금지"가 충돌해 원인·감별 추측("heart failure에서 볼 수 있다")이 들어갔다.
    if os.getenv("IMPRESSION_STRICT", "off").strip().lower() == "on":
        text = text.replace(
            "- `interpretation`: the clinical impression, written as a radiology report IMPRESSION section.",
            "- `interpretation`: the clinical impression, written as a radiology report IMPRESSION section: a "
            "brief summary of the key findings above. Do not speculate about causes, associations, or "
            "differential diagnoses.")
        text = text.replace(
            "- Do not overstate the AI result as a definitive diagnosis; include uncertainty and limitations.",
            "- Do not overstate the AI result as a definitive diagnosis. Put uncertainty and limitations in "
            "`recommendation`, not in `finding` or `interpretation`.")
    return text


def build_reader_prompt(
    query: str,
    task: dict,
    execution_context: dict,
    step_results: list[dict],
    wiki_context: str,
    image_roles: list[str] | None = None,
    knowledge_context: str = "",
) -> str:
    """Reader 프롬프트 — 기존 interpret를 UI용 3필드와 검증용 claims로 구조화."""
    task_str = f"{task.get('department', '')} / {task.get('project', '')}"
    mode = execution_context.get("mode", "")
    plan = execution_context.get("plan", {})
    plan_str = to_pretty_json(plan) if plan else "없음"

    results_str = _format_step_results(step_results)
    orig_section = _format_original_scan_context(execution_context)
    report_style = _report_style_enabled()
    image_instruction = "" if report_style else _image_instruction(image_roles)

    output_language = "English" if "english only" in query.lower() else "Korean"
    target_policy = _target_only_policy(task)
    return f"""## User Request
{query}

## Execution Target
{task_str} (mode: {mode})

## Execution Plan
{plan_str}

## Model Inference Results
{results_str}

## Wiki Reference
{wiki_context if wiki_context else "관련 Wiki 정보 없음"}
{_knowledge_section(knowledge_context)}{orig_section}{image_instruction}
{target_policy}
## Instructions
Interpret the inference results and images above into three {output_language}, human-facing fields and a structured claim breakdown.

For the human-facing fields:
{_reader_field_instructions(report_style)}

For the structured fields:
- `claims`: a list of the discrete clinical claims your opinion makes. Each claim = {{"label": "<short finding/condition name>", "text": "<the claim in one sentence>"}}. Use the SAME label wording that appears in the model predictions when a claim corresponds to a predicted class, so the claim can be matched back to its score.
- `sentence_map`: map each claim to its supporting sentence and field.
- `confidence_0_100`: an integer 0-100 giving the probability that the target finding asked about in the User Request is actually present. This is NOT your confidence in your own conclusion: a confidently negative reading must be near 0, not near 100. Base it on the model probabilities and your own reading, use the full range, and avoid habitual round numbers. Always provide it, even when the evidence is uncertain.

{BOARD_READER_OUTPUT_SCHEMA_TEXT}"""


def build_challenger_prompt(
    query: str,
    task: dict,
    step_results: list[dict],
    wiki_context: str,
    alt_model_output: dict | None = None,
    ddx_context: str = "",
) -> str:
    """Challenger 프롬프트 — 두 변형.
    - alt_model_output 있으면: 대체 모델 output 기반 (블라인드)
    - 없으면: 블라인드 동일모델 + RAG DDx 체크리스트

    두 변형 모두 Reader의 구조화 판독/claims는 제공하지 않는다(블라인드 원칙)."""
    task_str = f"{task.get('department', '')} / {task.get('project', '')}"
    results_str = _format_step_results(step_results)
    target_policy = _target_only_policy(task)

    if alt_model_output is not None:
        source_section = f"""## Alternative Model Output
An alternative model targeting the same clinical question was run. Base your differential primarily on this independent output:
```json
{to_pretty_json(alt_model_output)}
```

## Primary Model Inference Results (for reference only)
{results_str}"""
        source_note = "You have an independent alternative model's output. Compare it against the primary model results and surface any divergent or additional findings."
    else:
        source_section = f"""## Model Inference Results
{results_str}

## Differential Diagnosis Checklist (retrieved)
{ddx_context if ddx_context else "관련 감별진단 참고 정보 없음"}"""
        source_note = "No alternative model is available. Use the differential diagnosis checklist above to systematically consider alternatives the primary reading might miss."

    return f"""## User Request
{query}

## Execution Target
{task_str}

{source_section}

## Wiki Reference
{wiki_context if wiki_context else "관련 Wiki 정보 없음"}

{target_policy}

## Instructions
You are producing an INDEPENDENT differential. You have NOT been shown the primary reading — do not assume what it concluded. {source_note}
- For broad reports, list plausible alternative or additional findings grounded in the supplied evidence.
- Under the single-target reporting policy, challenge only the named target state and return no unrelated findings or general differential diagnoses.
- A low probability by itself must not be promoted into a possible positive finding.

Return only valid JSON (no markdown fences) in exactly this shape:
{{
  "differential": [{{"label": "<finding/condition name>", "rationale": "<one-line reason>"}}]
}}"""


def build_evidence_prompt(
    query: str,
    reader_claims: list[dict],
    challenger_differential: list[dict],
    rag_context: str,
) -> str:
    """Evidence 프롬프트 — Reader.claims ∪ Challenger.differential을 주장 단위로 RAG 대조."""
    reader_str = to_pretty_json(reader_claims) if reader_claims else "[]"
    challenger_str = to_pretty_json(challenger_differential) if challenger_differential else "[]"

    return f"""## User Request
{query}

## Reader Claims
```json
{reader_str}
```

## Challenger Differential
```json
{challenger_str}
```

## Retrieved Clinical Evidence
{rag_context if rag_context else "관련 근거 없음"}

## Instructions
For each claim (from both the Reader claims and the Challenger differential), assess it against the retrieved clinical evidence.
- Classify support as "supported", "neutral", or "unsupported".
- A claim is "unsupported" only when the retrieved evidence provides no backing (not merely because evidence is silent — use "neutral" for silence).
- Do not invent evidence beyond the retrieved context.

Return only valid JSON (no markdown fences) in exactly this shape:
{{
  "evidence_map": [{{"claim": "<claim label or text>", "support": "supported|neutral|unsupported", "note": "<short justification>"}}],
  "unsupported_claims": ["<verbatim claim label or text>", ...]
}}"""


def build_guardian_prompt(
    query: str,
    task: dict,
    step_results: list[dict],
    reader_out: dict,
    challenger_out: dict,
    evidence_out: dict,
    guardian_rag_context: str,
) -> str:
    """Guardian 프롬프트 — 자체 RAG 재조회 + 최종 veto."""
    task_str = f"{task.get('department', '')} / {task.get('project', '')}"
    safe_step_results = []
    for step in step_results:
        safe_step = {k: v for k, v in step.items() if k != "images"}
        safe_step["image_roles"] = [
            image.get("role", "") if isinstance(image, dict) else "output_image"
            for image in step.get("images", [])
        ]
        safe_step_results.append(safe_step)
    case_str = to_pretty_json({
        "step_results": safe_step_results,
        "reader": reader_out,
        "challenger": challenger_out,
        "evidence": evidence_out,
    })
    target_policy = _target_only_policy(task)

    return f"""## User Request
{query}

## Execution Target
{task_str}

## Complete Board Case
```json
{case_str}
```

## Guardian Re-retrieved Context
{guardian_rag_context if guardian_rag_context else "관련 근거 없음"}

{target_policy}

## Instructions
As the final safety net, independently re-review this case.
- Consider whether any don't-miss / critical finding may be present or inadequately addressed, grounded in the re-retrieved clinical context.
- Consider whether the unsupported claims from the Evidence agent create patient-safety risk.
- Set veto to true only when the interpretation should NOT be auto-confirmed without human review.
- Assign risk_tier as low, moderate, high, or critical. High/critical require a concrete rationale grounded in the supplied case or retrieved evidence.
- Be conservative: when patient safety is in doubt, flag it.
- Under the single-target reporting policy, do not introduce unrelated findings or convert a below-threshold target output into a positive clinical assertion.

Return only valid JSON (no markdown fences) in exactly this shape:
{{
  "veto": false,
  "risk_tier": "low|moderate|high|critical",
  "flags": ["<safety concern>", ...],
  "rationale": "<evidence-grounded short reason>",
  "evidence_refs": ["<supporting reference from retrieved context>", ...]
}}"""


def build_intent_prompt(query: str, uploaded_types: list[str] | None = None) -> str:
    uploaded_str = ", ".join(uploaded_types) if uploaded_types else "없음"
    return f"""## User Request
{query}

## Uploaded File Types
{uploaded_str}

## Instructions
Analyze the request and extract the clinical intent for specialized-model retrieval.
Return only this JSON (values in English):
{{
  "body_part": "target body part or anatomy, or empty string",
  "disease_group": "disease/condition group, or empty string",
  "modality": "imaging modality if identifiable (MR, CT, X-ray, ...), or empty string",
  "search_query": "a concise English phrase describing the analysis task, optimized for semantic model search"
}}"""


def build_model_select_prompt(query: str, intent: dict, candidates: list[dict]) -> str:
    """후보 모델 중 쿼리에 적절한 것을 LLM이 고르게 하는 프롬프트.
    candidates: [{"metadata": {...}, "text": "<doc_text>"}]"""
    lines = []
    for i, c in enumerate(candidates, 1):
        m = c.get("metadata", {})
        # doc_text 전문을 보여준다 — 질환(disease) 목록이 잘려 핵심 키워드가 누락되지 않도록.
        info = (c.get("text") or "").strip().replace("\n", " ")[:1200]
        perf = []
        if m.get("auroc_per_label"):
            perf.append(f"AUROC {m['auroc_per_label']}")
        elif m.get("auroc_macro") is not None:
            perf.append(f"AUROC {m['auroc_macro']}")
        if m.get("dice_mean") is not None:
            perf.append(f"Dice {m['dice_mean']}")
        if m.get("n_eval"):
            perf.append(f"n={m['n_eval']}")
        if m.get("metrics_source"):
            perf.append(f"source={m['metrics_source']}")
        lines.append(
            f"{i}. model_name: {m.get('model_name', '')}\n"
            f"   department: {m.get('department', '')} | project: {m.get('project', '')}\n"
            f"   task_type: {m.get('task_type', '')} | required_data: {m.get('required_data', '')}\n"
            f"   result_type: {m.get('result_type', '')}\n"
            f"   performance: {' | '.join(perf) if perf else '(not reported)'}\n"
            f"   info: {info}"
        )
    candidates_str = "\n".join(lines) if lines else "(no candidates)"

    return f"""## User Request
{query}

## Analyzed Intent
{to_pretty_json(intent)}

## Candidate Models
{candidates_str}

## Instructions
Select the models appropriate to fulfill the user's request, using the request, the analyzed intent, and each candidate's info/task.
- A model is relevant if it can detect, classify, or otherwise identify the target condition — including when the condition appears among the findings listed in its info (질환/disease). Do NOT exclude a model just because its task_type wording (e.g. "classification") differs from the request wording (e.g. "detection").
- If several relevant models exist for the same condition (e.g. a detection model AND a classification model that covers it), include ALL of them.
- If none are appropriate, return an empty list.
- Use model_name values exactly as listed. Do not invent models.

Then designate one selected model as the PRIMARY evidence source and the rest as SUPPORTING.
Weigh these together — do not decide on the performance number alone:
- reported performance (AUROC/Dice) AND how it was obtained (source, n, per-finding vs macro).
  Numbers from different task types are not directly comparable; a segmentation AUROC derived
  from a mask is not the same quantity as a classifier AUROC.
- how directly the model targets the condition asked about (a dedicated model over a broad
  multitask one when the request names a specific finding).
- what the output actually provides as clinical evidence (localization/quantification from a
  segmentation mask is stronger evidence than a probability alone).
- task difficulty and reliability: when two models report comparable metrics, prefer the one
  whose metric rests on a larger evaluation set or a verifiable source.
Performance reporting is uneven across this registry, so do not let it distort the choice:
- `(not reported)` means the metric was never recorded, NOT that the model performs poorly.
  Never rank a model lower, exclude it, or decline to make it primary merely because its
  performance is unreported.
- Judge such a model on task fit, how specifically it targets the condition, and the clinical
  evidence its output provides — exactly as you would a model with a reported metric.
- A reported number is not automatically decisive either: a broad multitask model with a high
  macro AUROC can still be the weaker choice for a specific finding.
State the reason in one short English sentence.

Return only this JSON, no markdown fences:
{{"selected_models": ["model_name", ...], "primary_model": "model_name", "primary_reason": "..."}}"""


def build_general_prompt(query: str, csv_data: list[dict] | None = None) -> str:
    csv_data = csv_data or []
    csv_section = ""
    if csv_data:
        csv_section = f"\n## Numeric Data (CSV)\n```json\n{to_pretty_json(csv_data[:50])}\n```"

    return f"""## User Request
{query}{csv_section}

## Instructions
Analyze all attached data (images, numeric data, etc.) comprehensively and answer the user's request.
Integrate image findings, abnormal numeric values, and clinical relevance into a concise first-pass screening summary.
Write in natural Korean."""


def build_general_prompt_from_attachments(
    query: str,
    attachments: list[dict],
) -> tuple[str, list[str]]:
    """정규화된 attachments[]로 general 프롬프트를 조립.
    반환: (prompt, VLM에 순서대로 넣을 이미지 목록)

    이미지가 파일당 여러 장(예: 3면 슬라이스) 올 수 있으므로,
    각 이미지를 "Attachment i image j" 라벨로 명시하고 VLM 입력 순서와 일치시킨다.
    """
    sections: list[str] = []
    vlm_images: list[str] = []
    image_manifest: list[str] = []

    for i, att in enumerate(attachments, 1):
        atype = att.get("type") or "unknown"
        fname = att.get("filename") or f"attachment_{i}"
        lines = [f"### Attachment {i}: {fname} ({atype})"]

        meta = att.get("metadata") or {}
        if meta:
            lines.append(f"- Metadata: {format_metadata(meta)}")

        text = (att.get("text") or "").strip()
        if text:
            lines.append(f"- Extracted text: {text}")

        tabular = att.get("tabular")
        if tabular:
            lines.append(f"- Tabular data:\n```json\n{to_pretty_json(tabular[:50])}\n```")

        imgs = att.get("images") or []
        if imgs:
            labels = []
            for j, img in enumerate(imgs, 1):
                vlm_images.append(img)
                label = f"Attachment {i} image {j}"
                labels.append(label)
                image_manifest.append(label)
            lines.append(f"- Attached images: {', '.join(labels)}")

        sections.append("\n".join(lines))

    attachments_str = "\n\n".join(sections) if sections else "없음"

    manifest_str = ""
    if image_manifest:
        ordered = "\n".join(f"{k}. {label}" for k, label in enumerate(image_manifest, 1))
        manifest_str = (
            "\n\n## Image Order\n"
            "The images are provided to you in exactly this order:\n" + ordered
        )

    prompt = f"""## User Request
{query}

## Attachments
{attachments_str}{manifest_str}

## Instructions
Analyze all attached data (images, extracted text, metadata, tabular values) comprehensively and answer the user's request.
- Use each attachment's metadata (modality, body part, age, sex, etc.) as clinical context when interpreting its images.
- Refer to images by their attachment and sequence when relevant.
Integrate image findings, abnormal numeric values, and clinical relevance into a concise first-pass screening summary.
Write in natural Korean."""
    return prompt, vlm_images
