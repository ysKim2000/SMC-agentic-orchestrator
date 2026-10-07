from __future__ import annotations

import os
import chromadb
from chromadb.utils import embedding_functions

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8010"))

_client: chromadb.HttpClient | None = None
_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2",
    device="cuda",
)


def get_client() -> chromadb.HttpClient:
    global _client
    if _client is None:
        _client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return _client


def _get_collection(name: str):
    client = get_client()
    try:
        return client.get_collection(name=name, embedding_function=_ef)
    except Exception as e:
        msg = str(e).lower()
        if "does not exist" not in msg and "not found" not in msg:
            raise
        return client.create_collection(
            name=name,
            embedding_function=_ef,
            metadata={"hnsw:space": "cosine"},
        )


def upsert_model(model_id: str, text: str, metadata: dict) -> None:
    col = _get_collection("maple_models")
    col.upsert(ids=[model_id], documents=[text], metadatas=[metadata])


def get_all_models() -> list[dict]:
    """maple_models 컬렉션의 모든 모델 메타데이터 목록.
    키워드 매칭용으로 doc_text를 '_document' 키에 함께 담아 반환."""
    col = _get_collection("maple_models")
    result = col.get(include=["metadatas", "documents"])
    metas = result.get("metadatas") or []
    docs = result.get("documents") or []
    out = []
    for i, meta in enumerate(metas):
        m = dict(meta)
        m["_document"] = docs[i] if i < len(docs) else ""
        out.append(m)
    return out


def get_model_name(model_id: str) -> str | None:
    """model_id로 ChromaDB에서 model_name 역조회"""
    col = _get_collection("maple_models")
    result = col.get(ids=[model_id], include=["metadatas"])
    if result["ids"]:
        return result["metadatas"][0].get("model_name")
    return None


def delete_model(model_id: str) -> None:
    col = _get_collection("maple_models")
    col.delete(ids=[model_id])


