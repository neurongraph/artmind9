import re
from pathlib import Path

import json_repair
import ollama
from loguru import logger

from utils.functions import load_env, log_llm_call


def embed_text(model: str, text: str) -> list[float]:
    response = ollama.embed(model=model, input=text)
    embedding = response.embeddings[0]
    log_llm_call("embed", model, text, f"[embedding vector, dim={len(embedding)}]")
    return embedding


def call_llm(model: str, prompt: str) -> str:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    response = ollama.Client(timeout=timeout).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    result = (response.message.content or "").strip()
    log_llm_call("chat", model, prompt, result)
    return result


def parse_json_response(text: str):
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json_repair.loads(text.strip())


def extract_with_retry(
    step_name: str,
    model: str,
    prompt: str,
    debug_dir: Path | None = None,
) -> tuple[list, bool]:
    raw_llm = ""
    for attempt in range(2):
        try:
            raw_llm = call_llm(model, prompt)
            return parse_json_response(raw_llm), True
        except Exception as e:
            if attempt == 0:
                logger.warning("  {} failed (attempt 1/2), retrying: {}", step_name, e)
            else:
                logger.error("  {} failed after 2 attempts: {}", step_name, e)
                if raw_llm and debug_dir:
                    safe = re.sub(r"[^A-Za-z0-9_]", "_", step_name)
                    (debug_dir / f"debug_{safe}.txt").write_text(raw_llm, encoding="utf-8")
    return [], False


def entities_list_text(entities: list[dict]) -> str:
    return "\n".join(f"{e['id']} ({e['entity_class']}): {e['name']}" for e in entities)


def build_entities_prompt(text: str, schema: dict) -> str:
    return schema.get("entities_prompt", "").replace("{text}", text)


def build_properties_prompt(text: str, entities: list[dict], schema: dict) -> str:
    ent_list = entities_list_text(entities)
    return (
        schema.get("properties_prompt", "")
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )


def build_relationships_prompt(text: str, entities: list[dict], schema: dict) -> str:
    ent_list = entities_list_text(entities)
    return (
        schema.get("relationships_prompt", "")
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )
