import os
import re
from datetime import date, datetime
from pathlib import Path

WIKI_PATH = Path(os.getenv("WIKI_PATH", "./wiki"))


def _ensure_dirs():
    for d in ["models", "departments", "concepts", "interpretations"]:
        (WIKI_PATH / d).mkdir(parents=True, exist_ok=True)


def _model_path(project: str, model_name: str) -> Path:
    return WIKI_PATH / "models" / project / f"{model_name}.md"


def _interp_dir(project: str, model_name: str) -> Path:
    return WIKI_PATH / "interpretations" / project / model_name


# ── 읽기 ──────────────────────────────────────────────────────────────────────

def read_model_page(project: str, model_name: str) -> str:
    p = _model_path(project, model_name)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def read_page(category: str, name: str) -> str:
    """models 외 카테고리(departments, concepts 등) 단순 읽기."""
    p = WIKI_PATH / category / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def read_all_models() -> list[dict]:
    """등록된 모든 모델을 {"project": ..., "model_name": ...} 목록으로 반환.
    정확히 models/{project}/{model_name}.md 깊이만 포함."""
    d = WIKI_PATH / "models"
    if not d.exists():
        return []
    return [
        {"project": f.parent.name, "model_name": f.stem}
        for f in d.rglob("*.md")
        if len(f.relative_to(d).parts) == 2
    ]


def resolve_model(name: str) -> tuple[str, str] | None:
    """model_name 또는 project명으로 (project, model_name) 반환.
    model_name 직접 매칭 → project명 역조회 순으로 시도.
    project명 매칭은 해당 project에 모델이 정확히 1개일 때만 반환한다.
    (모델이 여러 개면 비결정적이므로 None 반환)"""
    d = WIKI_PATH / "models"
    if not d.exists():
        return None

    model_match: tuple[str, str] | None = None
    project_matches: list[tuple[str, str]] = []

    for md_file in (f for f in d.rglob("*.md") if len(f.relative_to(d).parts) == 2):
        project = md_file.parent.name
        model_name = md_file.stem
        if name == model_name:
            model_match = (project, model_name)
        if name == project:
            project_matches.append((project, model_name))

    if model_match:
        return model_match
    if len(project_matches) == 1:
        return project_matches[0]
    return None


def search_wiki(query: str) -> str:
    """query 키워드가 포함된 wiki 페이지들을 반환 (최대 3개)"""
    results = []
    keywords = query.lower().split()

    for md_file in WIKI_PATH.rglob("*.md"):
        if md_file.name in ("index.md", "log.md"):
            continue
        content = md_file.read_text(encoding="utf-8")
        score = sum(1 for kw in keywords if kw in content.lower())
        if score > 0:
            results.append((score, md_file.stem, content))

    results.sort(key=lambda x: x[0], reverse=True)
    if not results:
        return ""

    combined = []
    for _, name, content in results[:3]:
        combined.append(f"### [{name}]\n{content[:800]}")
    return "\n\n".join(combined)


# ── 쓰기 ──────────────────────────────────────────────────────────────────────

def write_model_page(model_name: str, department: str, project: str,
                     description: str, task_type: str, disease: str,
                     required_data: list[str], result_type: list[str] | str,
                     provides: list[str] | None = None,
                     requires: list[str] | None = None) -> None:
    _ensure_dirs()
    required_str = ", ".join(required_data)
    result_str = ", ".join(result_type) if isinstance(result_type, list) else result_type
    provides_str = ", ".join(provides or [])
    requires_str = ", ".join(requires or [])
    content = f"""# {model_name}

## 기본 정보
- **진료과:** {department}
- **프로젝트:** {project}
- **task_type:** {task_type}
- **required_data:** [{required_str}]
- **result_type:** [{result_str}]
- **provides:** [{provides_str}]
- **requires:** [{requires_str}]

## 설명
{description}

## 임상 해석 패턴

## 관련 개념
{disease}
"""
    p = _model_path(project, model_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def update_department_page(department: str, model_name: str, project: str) -> None:
    _ensure_dirs()
    p = WIKI_PATH / "departments" / f"{department}.md"
    if p.exists():
        content = p.read_text(encoding="utf-8")
        model_line = f"- [[{project}/{model_name}]]"
        if model_line not in content:
            content += f"\n{model_line}\n"
    else:
        content = f"# {department}\n\n## 모델 목록\n- [[{project}/{model_name}]]\n"
    p.write_text(content, encoding="utf-8")


def update_index(model_name: str, department: str, project: str) -> None:
    index_path = WIKI_PATH / "index.md"
    content = index_path.read_text(encoding="utf-8") if index_path.exists() else "# MARS AI Agent Wiki Index\n\n## Models\n\n## Departments\n\n## Concepts\n\n## Interpretations\n"

    model_line = f"- [[{project}/{model_name}]] - {department}"
    dept_line = f"- [[{department}]]"

    if model_line not in content:
        content = _insert_under_section(content, "## Models", model_line)
    if dept_line not in content:
        content = _insert_under_section(content, "## Departments", dept_line)

    index_path.write_text(content, encoding="utf-8")


def remove_model_from_index(project: str, model_name: str) -> None:
    index_path = WIKI_PATH / "index.md"
    if not index_path.exists():
        return
    content = index_path.read_text(encoding="utf-8")
    lines = [l for l in content.splitlines() if f"[[{project}/{model_name}]]" not in l]
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def delete_model_page(project: str, model_name: str) -> bool:
    p = _model_path(project, model_name)
    if p.exists():
        p.unlink()
        return True
    return False


def append_log(action: str, description: str) -> None:
    log_path = WIKI_PATH / "log.md"
    today = date.today().isoformat()
    entry = f"## [{today}] {action} | {description}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def append_interpretation(project: str, model_name: str, interpretation: str) -> None:
    """해석 전문은 interpretations/{project}/{model_name}/ 에 날짜별 파일로 저장.
    모델 페이지에는 실행 날짜만 한 줄 기록."""
    today = date.today().isoformat()

    interp_dir = _interp_dir(project, model_name)
    interp_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    filename = f"{ts}.md"
    (interp_dir / filename).write_text(interpretation, encoding="utf-8")

    model_page = _model_path(project, model_name)
    if not model_page.exists():
        return
    content = model_page.read_text(encoding="utf-8")
    log_line = f"- {today} 해석 완료 ({project}/{model_name}/{filename})"
    if log_line not in content:
        if "## 임상 해석 패턴" in content:
            content = content.replace("## 임상 해석 패턴", f"## 임상 해석 패턴\n{log_line}", 1)
        else:
            content = content.rstrip() + f"\n\n## 임상 해석 패턴\n{log_line}\n"
    model_page.write_text(content, encoding="utf-8")


def upsert_concept(query: str, answer: str) -> None:
    """clinical 답변을 concepts/ 에 캐싱. 같은 토픽이면 덮어씀."""
    _ensure_dirs()
    topic = _extract_topic(query)
    p = WIKI_PATH / "concepts" / f"{topic}.md"
    today = date.today().isoformat()
    content = f"# {topic}\n\n## 질문\n{query}\n\n## 답변\n{answer}\n\n_최종 업데이트: {today}_\n"
    p.write_text(content, encoding="utf-8")


def _extract_topic(query: str) -> str:
    noise = r"(이란|이란\?|란\?|란|은\?|는\?|이란무엇|이뭐야|이뭔가요|가뭐야|란무엇|뭐야|뭔가요|뭔지|알려줘|설명해줘|이란\s*무엇|무엇인가요|\?)"
    cleaned = re.sub(noise, "", query, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s가-힣]", " ", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned[:50] or "concept"


# ── 내부 유틸 ─────────────────────────────────────────────────────────────────

def _insert_under_section(content: str, section_header: str, line: str) -> str:
    lines = content.splitlines()
    result = []
    inserted = False
    for l in lines:
        result.append(l)
        if not inserted and l.strip() == section_header:
            result.append(line)
            inserted = True
    if not inserted:
        result.append(section_header)
        result.append(line)
    return "\n".join(result) + "\n"
