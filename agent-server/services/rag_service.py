import asyncio
from rag.retriever import search_models, search_knowledge, build_rag_context


async def retrieve(query: str, n_models: int = 3) -> tuple[list[dict], list[dict], str]:
    """모델 레지스트리 + 지식 베이스 검색을 병렬로 실행 후 컨텍스트 반환

    n_models: 모델 후보 수. 기본 3은 대화형 응답용이다. 계획 수립처럼 여러
    모델을 골라야 하는 경로는 더 크게 준다 — 3개만 넘기면 plan이 7개를
    고를 수 없고, 실측에서 상위 3개가 전부 심장 계열로 채워졌다.
    """
    def _search_models():
        try:
            return search_models(query, n_results=n_models)
        except Exception:
            return []

    def _search_knowledge():
        try:
            return search_knowledge(query, n_results=5)
        except Exception:
            return []

    loop = asyncio.get_event_loop()
    model_results, knowledge_results = await asyncio.gather(
        loop.run_in_executor(None, _search_models),
        loop.run_in_executor(None, _search_knowledge),
    )

    context = build_rag_context(model_results, knowledge_results)
    return model_results, knowledge_results, context
