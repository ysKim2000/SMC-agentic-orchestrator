"""
AI_Models/ 폴더를 스캔하여 meta.json이 있는 모델을 MongoDB + ChromaDB에 자동 등록.

사용법:
  python scan_and_register.py           # 전체 스캔
  python scan_and_register.py --dry-run # 변경 없이 탐지 결과만 출력
"""

import sys
import io
import json
import asyncio
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import httpx
from pymongo import MongoClient

from config.settings import MONGO_URI, AGENT_URL, AI_MODELS_DIR, DB_NAME

MONGO_URL = MONGO_URI
AI_MODELS = Path(AI_MODELS_DIR)

# build_model_info()가 소유하는 키. 재등록 시 이번 meta.json에 없는 키는 지워
# 이전 스캔 값이 남지 않게 한다. risk_policy 등 서버가 쓰는 필드는 보존한다.
MANAGED_KEYS = {
    "model_id", "project_name", "model_name", "model_description", "model_path",
    "required_data", "provides", "requires", "task_type", "result_type",
    "output_image_role", "inference_script", "inference_server", "endpoint",
    "parallel_safe", "docker", "requirements_path",
}


def logical_path(path: Path) -> str:
    """AI_MODELS 기준 상대 경로를 실행 서버가 해석하는 AI_Models/... 형태로 변환."""
    return str(Path("AI_Models") / path.relative_to(AI_MODELS)).replace("\\", "/")


# ── MongoDB 헬퍼 ─────────────────────────────────────────────────────────────

def get_department_doc(col, department_name: str):
    return col.find_one({"departments.department_name": department_name})


def find_project_id(dept: dict, project_name: str) -> str | None:
    for pid, pv in dept.get("projects", {}).items():
        if pv.get("project_name") == project_name:
            return pid
    return None


def find_model_id(dept: dict, project_id: str, model_name: str) -> str | None:
    models = dept.get("projects", {}).get(project_id, {}).get("ai_models", {})
    for mid, mv in models.items():
        if mv.get("model_name") == model_name:
            return mid
    return None


def next_id(mapping: dict) -> str:
    if not mapping:
        return "1"
    return str(max(int(k) for k in mapping.keys()) + 1)


def upsert_model_to_mongo(col, department_name, project_name, model_name, model_info: dict):
    """MongoDB에 모델 upsert. project에 모델 정보를 직접 저장."""
    doc = col.find_one({})
    if not doc:
        col.insert_one({"departments": []})
        doc = col.find_one({})
        print("  + maple_db 초기 문서 생성")

    depts = doc.get("departments", [])

    # department 찾기 (없으면 생성)
    dept = next((d for d in depts if d["department_name"] == department_name), None)
    if dept is None:
        dept = {"department_name": department_name, "projects": {}}
        depts.append(dept)
        print(f"  + department 생성: {department_name}")

    # project 찾기 (없으면 생성)
    pid = find_project_id(dept, project_name)
    if pid is None:
        pid = next_id(dept["projects"])
        dept["projects"][pid] = {}
        print(f"  + project 생성: {project_name} (id={pid})")
    else:
        print(f"  ~ project 업데이트: {project_name} (id={pid})")

    # project에 모델 정보 직접 저장. 이번 meta.json에 없어진 키는 먼저 제거한다.
    project = dept["projects"][pid]
    for key in MANAGED_KEYS - model_info.keys():
        project.pop(key, None)
    project.update(model_info)

    col.replace_one({"_id": doc["_id"]}, doc)
    return True


async def register_to_chromadb(model_name, department, project, meta: dict, doc_id: str):
    """Agent /agent/models/register 호출"""
    payload = {
        "id":           doc_id,
        "model_name":   model_name,
        "department":   department,
        "project":      project,
        "description":  meta.get("description", ""),
        "task_type":    meta.get("task_type", ""),
        "disease":      meta.get("disease", ""),
        "required_data": meta.get("required_data", []),
        "provides":     meta.get("provides", []),   # DAG 체인 구성용 (agent가 depends_on 도출)
        "requires":     meta.get("requires", []),
        "result_type":  meta.get("result_type", ""),
        "metrics":      meta.get("metrics", {}),   # 성능 지표 — agent 모델 선택의 판단 근거
        # 입력 데이터 식별 필드 — agent가 VLM이 판별한 입력과 맞춰 후보를 거른다
        "modality":     meta.get("modality", ""),
        "body_region":  meta.get("body_region", []),
        "view":         meta.get("view", ""),
    }
    # 임베딩 모델 콜드 스타트 시 한 건에 30초 이상 걸린다. 10초로는 조용히 누락된다.
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{AGENT_URL}/agent/models/register", json=payload)
        resp.raise_for_status()


def slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ── 스캔 ─────────────────────────────────────────────────────────────────────

def scan_models() -> list[dict]:
    """AI_MODELS/{dept}/{project}/meta.json 구조를 스캔 (2단계)."""
    found = []
    for meta_path in sorted(AI_MODELS.rglob("meta.json")):
        rel_parts = meta_path.relative_to(AI_MODELS).parts
        if len(rel_parts) != 3:
            continue
        dept, project, _ = rel_parts
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        found.append({"dept": dept, "project": project,
                      "meta": meta, "meta_path": meta_path})
    return found


def build_model_info(dept: str, project: str, meta: dict) -> dict:
    """DB에 저장할 model_info dict 구성. project_name = model_name."""
    model_dir      = AI_MODELS / dept / project
    checkpoint_dir = model_dir / "checkpoint"

    model_path = {}
    if checkpoint_dir.exists():
        for f in checkpoint_dir.iterdir():
            if f.is_file() and f.suffix not in (".py", ".txt", ".md"):
                model_path[f.name] = logical_path(f)

    script_file  = model_dir / "inference.py"
    requirements = model_dir / "requirements.txt"
    inference_script  = logical_path(script_file)  if script_file.exists()  else None
    requirements_path = logical_path(requirements) if requirements.exists() else None

    docker_meta  = meta.get("docker", {})
    docker_image = f"{slugify(dept)}-{slugify(project)}:latest"

    info = {
        "model_id":          meta.get("model_id", f"{dept}/{project}"),
        "project_name":      project,
        "model_name":        meta.get("model_name", project),
        "model_description": meta.get("description", ""),
        "model_path":        model_path,
        "required_data":     meta.get("required_data", []),
        "provides":          meta.get("provides", []),   # DAG: 이 모델이 산출하는 태그 (예: ["roi"])
        "requires":          meta.get("requires", []),   # DAG: 이 모델이 선행으로 요구하는 태그
        "task_type":         meta.get("task_type", ""),
        "disease":           meta.get("disease", ""),
        "modality":          meta.get("modality", ""),
        "body_region":       meta.get("body_region", []),
        "view":              meta.get("view", ""),
        "result_type":       meta.get("result_type", "text"),
        "output_image_role": meta.get("output_image_role"),  # interpret [IMG:role] 태깅용
        "inference_script":  inference_script,
        "inference_server":  meta.get("inference_server", "local"),
        "endpoint":          meta.get("endpoint", "/infer"),
        "parallel_safe":     meta.get("parallel_safe", True),
        "docker": {
            "image":       docker_meta.get("image", docker_image),
            "service_url": docker_meta.get("service_url", ""),
        },
    }
    if requirements_path:
        info["requirements_path"] = requirements_path
    return info


# ── 메인 ─────────────────────────────────────────────────────────────────────

async def main(dry_run: bool):
    models = scan_models()
    if not models:
        print("meta.json을 찾지 못했습니다.")
        return

    print(f"발견된 모델: {len(models)}개\n")

    if dry_run:
        for m in models:
            print(f"  {m['dept']} / {m['project']}")
            print(f"    model_name    : {m['meta'].get('model_name', m['project'])}")
            print(f"    required_data : {m['meta'].get('required_data')}")
            print(f"    provides      : {m['meta'].get('provides', [])}")
            print(f"    requires      : {m['meta'].get('requires', [])}")
            print(f"    result_type   : {m['meta'].get('result_type')}")
            print(f"    service_url   : {m['meta'].get('docker', {}).get('service_url')}")
        return

    client = MongoClient(MONGO_URL)
    col = client[DB_NAME]["departments"]
    no_weights: list[str] = []
    chroma_failed: list[str] = []

    for m in models:
        dept, project, meta = m["dept"], m["project"], m["meta"]
        model_name = meta.get("model_name", project)
        print(f"▶ {dept} / {project} (model: {model_name})")

        # 1. MongoDB
        model_info = build_model_info(dept, project, meta)
        if not model_info["model_path"]:
            no_weights.append(f"{dept}/{project}")
            print(f"  ⚠️  가중치 없음 — checkpoint/ 에 파일이 없어 model_path가 빕니다")
        upsert_model_to_mongo(col, dept, project, model_name, model_info)
        print(f"  ✅ MongoDB 등록 완료")

        # 2. ChromaDB (Agent 서버 필요)
        doc_id = f"{slugify(dept)}-{slugify(project)}"
        try:
            await register_to_chromadb(model_name, dept, project, meta, doc_id)
            print(f"  ✅ ChromaDB 등록 완료")
        except Exception as e:
            chroma_failed.append(f"{dept}/{project}")
            print(f"  ⚠️  ChromaDB 등록 실패: {e}")

        print()

    print(f"완료. 모델 {len(models)}개 등록.")
    if no_weights:
        print(f"\n가중치 없음 {len(no_weights)}개 (model_path 빈 상태로 등록됨 — 추론 불가):")
        for name in no_weights:
            print(f"  - {name}")
    if chroma_failed:
        print(f"\nChromaDB 등록 실패 {len(chroma_failed)}개:")
        for name in chroma_failed:
            print(f"  - {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="등록 없이 탐지 결과만 출력")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
