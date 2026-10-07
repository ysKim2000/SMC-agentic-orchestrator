from rag.embedder import _get_collection


def search_models(query: str, n_results: int = 3) -> list[dict]:
    col = _get_collection("maple_models")
    results = col.query(query_texts=[query], n_results=n_results)
    return _format_results(results, source="maple_models")


def search_knowledge(query: str, n_results: int = 5) -> list[dict]:
    col = _get_collection("maple_knowledge")
    results = col.query(query_texts=[query], n_results=n_results)
    return _format_results(results, source="maple_knowledge")


def _format_results(results: dict, source: str) -> list[dict]:
    formatted = []
    if not results["documents"] or not results["documents"][0]:
        return formatted

    docs = results["documents"][0]
    metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
    distances = results["distances"][0] if results.get("distances") else [1.0] * len(docs)

    for doc, meta, dist in zip(docs, metas, distances):
        formatted.append({
            "source": source,
            "text": doc,
            "metadata": meta,
            "score": round(1 - dist, 4),  # cosine similarity
        })
    return formatted


def build_rag_context(model_results: list[dict], knowledge_results: list[dict]) -> str:
    lines = []
    for r in model_results:
        lines.append(f"[모델 레지스트리] (score={r['score']})\n{r['text']}")
    for r in knowledge_results:
        lines.append(f"[논문/QA] (score={r['score']})\n{r['text'][:400]}")
    return "\n\n".join(lines)
