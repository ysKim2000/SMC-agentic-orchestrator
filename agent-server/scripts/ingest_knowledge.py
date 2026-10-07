"""
PubMedQA + MedMCQA 데이터를 ChromaDB maple_knowledge 컬렉션에 ingest.

사용법:
    python scripts/ingest_knowledge.py [--pubmedqa N] [--medmcqa N]

옵션:
    --pubmedqa N   PubMedQA에서 ingest할 샘플 수 (기본: 5000)
    --medmcqa N    MedMCQA에서 ingest할 샘플 수 (기본: 5000)
    --batch B      ChromaDB upsert 배치 크기 (기본: 256)
    --dry-run      ChromaDB에 쓰지 않고 샘플 3개만 출력

주의: ChromaDB가 localhost:8010에서 실행 중이어야 합니다.
"""

import argparse
import os
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from tqdm import tqdm
import chromadb
from chromadb.utils import embedding_functions

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8010"))

_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)


def get_collection():
    client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    return client.get_or_create_collection(
        name="maple_knowledge",
        embedding_function=_ef,
        metadata={"hnsw:space": "cosine"},
    )


def batch_upsert(col, ids: list, docs: list, metas: list, batch_size: int):
    for i in range(0, len(ids), batch_size):
        col.upsert(
            ids=ids[i:i+batch_size],
            documents=docs[i:i+batch_size],
            metadatas=metas[i:i+batch_size],
        )


def ingest_pubmedqa(col, max_samples: int | None, batch_size: int, dry_run: bool):
    print(f"\n[PubMedQA] loading (max {'전체' if max_samples is None else max_samples})...")
    from datasets import load_dataset
    # pqa_labeled: 1000개 레이블된 QA
    # pqa_unlabeled: 61k 비레이블 (답변 없는 문헌)
    ds = load_dataset("qiaojin/PubMedQA", "pqa_labeled", split="train")

    ids, docs, metas = [], [], []
    for i, row in enumerate(ds):
        if max_samples is not None and i >= max_samples:
            break

        question = row.get("question") or ""
        context_list = (row.get("context") or {}).get("contexts") or []
        context = " ".join(context_list)[:1000]
        long_answer = row.get("long_answer") or ""
        final_decision = row.get("final_decision") or ""  # yes / no / maybe

        text = f"[PubMedQA] Q: {question}\nContext: {context}\nA: {long_answer}"
        doc_id = f"pubmedqa_{i}"
        meta = {
            "source": "pubmedqa",
            "question": question[:200],
            "final_decision": final_decision,
        }

        if dry_run and i < 3:
            print(f"\n--- sample {i} ---")
            print(f"id: {doc_id}")
            print(f"text[:200]: {text[:200]}")
            print(f"meta: {meta}")

        ids.append(doc_id)
        docs.append(text)
        metas.append(meta)

    if not dry_run:
        print(f"  upserting {len(ids)} docs to ChromaDB...")
        batch_upsert(col, ids, docs, metas, batch_size)
        print(f"  done.")
    else:
        print(f"\n[dry-run] would upsert {len(ids)} PubMedQA docs (전체)")


def ingest_medmcqa(col, max_samples: int | None, batch_size: int, dry_run: bool):
    print(f"\n[MedMCQA] loading (max {'전체' if max_samples is None else max_samples})...")
    from datasets import load_dataset
    ds = load_dataset("openlifescienceai/medmcqa", split="train")

    ids, docs, metas = [], [], []
    for i, row in enumerate(ds):
        if max_samples is not None and i >= max_samples:
            break

        question = row.get("question", "")
        opa = row.get("opa", "")
        opb = row.get("opb", "")
        opc = row.get("opc", "")
        opd = row.get("opd", "")
        cop = row.get("cop", 0)  # correct option index (0~3)
        exp = row.get("exp", "") or ""
        subject = row.get("subject_name") or ""
        topic = row.get("topic_name") or ""

        options = [opa, opb, opc, opd]
        correct_answer = options[cop] if cop < len(options) else ""

        text = (
            f"[MedMCQA] Subject: {subject} | Topic: {topic}\n"
            f"Q: {question}\n"
            f"Options: A) {opa}  B) {opb}  C) {opc}  D) {opd}\n"
            f"Answer: {correct_answer}\n"
            f"Explanation: {exp[:400]}"
        )
        doc_id = f"medmcqa_{i}"
        meta = {
            "source": "medmcqa",
            "subject": subject,
            "topic": topic,
            "question": question[:200],
        }

        if dry_run and i < 3:
            print(f"\n--- sample {i} ---")
            print(f"id: {doc_id}")
            print(f"text[:200]: {text[:200]}")
            print(f"meta: {meta}")

        ids.append(doc_id)
        docs.append(text)
        metas.append(meta)

    if not dry_run:
        print(f"  upserting {len(ids)} docs to ChromaDB...")
        batch_upsert(col, ids, docs, metas, batch_size)
        print(f"  done.")
    else:
        print(f"\n[dry-run] would upsert {len(ids)} MedMCQA docs (전체)")


def main():
    parser = argparse.ArgumentParser(description="Ingest PubMedQA + MedMCQA into ChromaDB")
    parser.add_argument("--pubmedqa", type=int, default=None, metavar="N")
    parser.add_argument("--medmcqa", type=int, default=None, metavar="N")
    parser.add_argument("--batch", type=int, default=256, metavar="B")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print("[dry-run mode] ChromaDB에 쓰지 않습니다.")
        col = None
    else:
        print(f"ChromaDB 연결: {CHROMA_HOST}:{CHROMA_PORT}")
        col = get_collection()
        print(f"컬렉션 'maple_knowledge' 준비 완료. 현재 doc 수: {col.count()}")

    ingest_pubmedqa(col, args.pubmedqa, args.batch, args.dry_run)
    ingest_medmcqa(col, args.medmcqa, args.batch, args.dry_run)

    if not args.dry_run:
        print(f"\n완료. maple_knowledge 총 doc 수: {col.count()}")


if __name__ == "__main__":
    main()
