import os
import json
import base64
import httpx
from typing import AsyncIterator


LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:8011/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemma-4-31B-it")
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemma-4-31B-it")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2048"))


def normalize_images(images: list[str] | None) -> list[str]:
    normalized = []
    for image in images or []:
        if not image or not isinstance(image, str):
            continue
        raw = image.strip()
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[1] if "," in raw else ""
        raw = "".join(raw.split())
        if not raw:
            continue
        try:
            base64.b64decode(raw, validate=True)
        except Exception:
            continue
        normalized.append(raw)
    return normalized


def _build_messages(prompt: str, system: str = "") -> list[dict]:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages


async def generate(prompt: str, system: str = "") -> str:
    payload = {
        "model": LLM_MODEL,
        "messages": _build_messages(prompt, system),
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{LLM_BASE_URL}/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def stream_generate(prompt: str, system: str = "") -> AsyncIterator[str]:
    payload = {
        "model": LLM_MODEL,
        "messages": _build_messages(prompt, system),
        "max_tokens": MAX_TOKENS,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        async with client.stream("POST", f"{LLM_BASE_URL}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                data = json.loads(data_str)
                content = data["choices"][0].get("delta", {}).get("content", "")
                if content:
                    yield content


async def generate_with_images(prompt: str, images: list[str], system: str = "") -> str:
    normalized = normalize_images(images)
    if not normalized:
        return await generate(prompt, system=system)

    content = [{"type": "text", "text": prompt}]
    for img in normalized:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{img}"},
        })

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    payload = {
        "model": VLM_MODEL,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{LLM_BASE_URL}/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def is_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LLM_BASE_URL}/models")
            return resp.status_code == 200
    except Exception:
        return False
