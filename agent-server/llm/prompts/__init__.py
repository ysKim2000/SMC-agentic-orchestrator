# llm/prompts/__init__.py

from .system import (
    SYSTEM_PLAN,
    SYSTEM_CLINICAL,
    SYSTEM_INTERPRET,
    SYSTEM_INTERPRET_EN,
    SYSTEM_INTENT,
    SYSTEM_GENERAL,
    SYSTEM_READER,
    SYSTEM_READER_EN,
    SYSTEM_CHALLENGER,
    SYSTEM_EVIDENCE,
    SYSTEM_GUARDIAN,
)

from .builders import (
    build_plan_prompt,
    build_clinical_prompt,
    build_interpret_prompt,
    build_intent_prompt,
    build_model_select_prompt,
    build_general_prompt,
    build_general_prompt_from_attachments,
    build_reader_prompt,
    build_challenger_prompt,
    build_evidence_prompt,
    build_guardian_prompt,
)

from .schemas import (
    PLAN_EXECUTION_SCHEMA,
    PLAN_KNOWLEDGE_SCHEMA,
    PLAN_GENERAL_SCHEMA,
    PLAN_OUTPUT_SCHEMA_TEXT,
    ALLOWED_PLAN_TYPES,
)

__all__ = [
    "SYSTEM_PLAN",
    "SYSTEM_CLINICAL",
    "SYSTEM_INTERPRET",
    "SYSTEM_INTERPRET_EN",
    "SYSTEM_INTENT",
    "SYSTEM_GENERAL",
    "SYSTEM_READER",
    "SYSTEM_READER_EN",
    "SYSTEM_CHALLENGER",
    "SYSTEM_EVIDENCE",
    "SYSTEM_GUARDIAN",
    "build_plan_prompt",
    "build_clinical_prompt",
    "build_interpret_prompt",
    "build_intent_prompt",
    "build_model_select_prompt",
    "build_general_prompt",
    "build_general_prompt_from_attachments",
    "build_reader_prompt",
    "build_challenger_prompt",
    "build_evidence_prompt",
    "build_guardian_prompt",
    "PLAN_EXECUTION_SCHEMA",
    "PLAN_KNOWLEDGE_SCHEMA",
    "PLAN_GENERAL_SCHEMA",
    "PLAN_OUTPUT_SCHEMA_TEXT",
    "ALLOWED_PLAN_TYPES",
]
