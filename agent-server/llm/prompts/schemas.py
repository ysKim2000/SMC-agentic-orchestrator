# llm/prompts/schemas.py

PLAN_EXECUTION_SCHEMA = {
    "type": "execution",
    "steps": [
        {
            "step": 1,
            "model": "registered_model_name",
            "department": "department_name",
            "project": "project_name",
        }
    ],
    "missing_inputs": [],
    "message": "사용자에게 보여줄 자연스러운 한국어 설명",
}


PLAN_KNOWLEDGE_SCHEMA = {
    "type": "knowledge",
    "message": "사용자에게 보여줄 자연스러운 한국어 답변",
}


PLAN_GENERAL_SCHEMA = {
    "type": "general",
    "message": "범용 분석을 수행합니다.",
}


PLAN_OUTPUT_SCHEMA_TEXT = """
For model execution:
{
  "type": "execution",
  "steps": [
    {
      "step": 1,
      "model": "registered_model_name",
      "department": "department_name",
      "project": "project_name"
    }
  ],
  "missing_inputs": [],
  "message": "사용자에게 보여줄 자연스러운 한국어 설명"
}

For clinical knowledge:
{
  "type": "knowledge",
  "message": "사용자에게 보여줄 자연스러운 한국어 답변"
}

For general analysis:
{
  "type": "general",
  "message": "범용 분석을 수행합니다."
}
""".strip()


ALLOWED_PLAN_TYPES = {"execution", "knowledge", "general"}


BOARD_READER_OUTPUT_SCHEMA_TEXT = """
Return only valid JSON, no markdown fences:
{
  "finding": "마크다운 텍스트. 관찰된 소견. [IMG:role] 토큰 삽입 가능.",
  "interpretation": "마크다운 텍스트. 소견의 임상적 의미와 감별진단.",
  "recommendation": "마크다운 텍스트. 권장 조치와 한계점.",
  "confidence_0_100": 0,  // 대상 소견이 실제로 존재할 0-100 확률(결론에 대한 확신이 아님)
  "claims": [{"label": "finding label", "text": "one clinical claim"}],
  "sentence_map": [{"claim_label": "finding label", "field": "finding|interpretation|recommendation", "sentence": "supporting sentence"}]
}
""".strip()
