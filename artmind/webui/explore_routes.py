"""Admin-console route for Explore -> Graph: question -> Cypher -> Neo4j Browser.

`POST /api/explore/cypher` turns a natural-language question into read-only,
domain-scoped Cypher (`artmind.text2cypher`, graph mode) and returns it in two
forms: the parameterised query, and a self-contained one with the parameters
inlined, because Neo4j Browser cannot take parameters from a link. When
`ARTMIND_KG_NEO4J_BROWSER_URL` is set it also returns a deep link that opens
Browser with the query pre-filled. Nothing is executed here: the operator
reviews the Cypher and presses Run in Browser.
"""

from __future__ import annotations

import anyio.to_thread
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

MAX_QUESTION_CHARS = 2000


class CypherRequest(BaseModel):
    question: str
    domains: list[str]

    @field_validator("question")
    @classmethod
    def _question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        if len(value) > MAX_QUESTION_CHARS:
            raise ValueError(f"question is longer than {MAX_QUESTION_CHARS} characters")
        return value


class LinkRequest(BaseModel):
    cypher: str

    @field_validator("cypher")
    @classmethod
    def _cypher(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cypher must not be empty")
        return value


def _generate(question: str, domains: list[str]) -> dict:
    from artmind.text2cypher import generate_cypher

    return generate_cypher(question, domains, graph_mode=True)


def _browser_url() -> str | None:
    from utils.functions import load_env

    return load_env().get("ARTMIND_KG_NEO4J_BROWSER_URL") or None


def _package(cypher: str, parameters: dict) -> dict:
    from artmind.text2cypher import browser_deep_link, inline_parameters

    standalone = inline_parameters(cypher, parameters)
    return {
        "cypher": cypher,
        "parameters": parameters,
        "standalone_cypher": standalone,
        "browser_url": browser_deep_link(_browser_url(), standalone),
    }


def _check_read_only(cypher: str) -> None:
    from artmind.text2cypher import _prepare_graph_cypher, validate_read_only

    validate_read_only(cypher)
    _prepare_graph_cypher(cypher)  # also refuses CALL


def register_explore_routes(app: FastAPI) -> FastAPI:
    @app.post("/api/explore/cypher")
    async def api_explore_cypher(payload: CypherRequest):
        from artmind.graph_query import normalize_domains

        try:
            domains = normalize_domains(payload.domains)
            result = await anyio.to_thread.run_sync(_generate, payload.question, domains)
            return {"question": payload.question, "domains": domains,
                    **_package(result["cypher"], result["parameters"])}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # LLM / Neo4j failure: report it, don't 500 with a stack
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/explore/cypher/link")
    async def api_explore_cypher_link(payload: LinkRequest):
        """Deep link for Cypher the operator edited by hand. Self-contained
        queries only (no `$params`), and held to the same read-only rule."""
        from artmind.text2cypher import browser_deep_link, inline_parameters

        try:
            _check_read_only(payload.cypher)
            standalone = inline_parameters(payload.cypher, {})
            return {"standalone_cypher": standalone,
                    "browser_url": browser_deep_link(_browser_url(), standalone)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
