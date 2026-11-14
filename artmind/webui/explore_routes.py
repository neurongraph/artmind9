"""Admin-console routes for Explore -> Graph: question -> Cypher -> canvas / Neo4j Browser.

`POST /api/explore/cypher` turns a natural-language question into read-only,
domain-scoped Cypher (`artmind.text2cypher`, graph mode) and returns it in two
forms: the parameterised query, and a self-contained one with the parameters
inlined, because Neo4j Browser cannot take parameters from a link. When
`ARTMIND_KG_NEO4J_BROWSER_URL` is set it also returns a deep link that opens
Browser with the query pre-filled. This module executes nothing: the tab
draws the Cypher through `/api/graph/run`, and in Browser the operator
reviews it and presses Run.
It also returns `run_cypher` (everything but `$domains` inlined) for /api/graph/run.
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
    domains: list[str] | None = None

    @field_validator("cypher")
    @classmethod
    def _cypher(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("cypher must not be empty")
        return value


def _generate(question: str, domains: list[str]) -> dict:
    from artmind.text2cypher import generate_cypher

    return generate_cypher(question, domains, graph_mode=True)


def _domain_entity_counts() -> list[dict]:
    """Every available domain with its entity count (a domain family counts its
    children), most populated first so the Graph tab can default to one that
    has data. Alphabetical order put an empty `general` first."""
    from artmind.cli import _get_available_domains
    from artmind.graph_query import _run_read_query

    rows = _run_read_query(
        "MATCH (e:Entity) RETURN e._domain AS domain, count(e) AS n", {}
    )
    by_domain = {r["domain"]: r["n"] for r in rows if r.get("domain")}
    out = []
    for name in _get_available_domains():
        n = sum(c for d, c in by_domain.items() if d == name or d.startswith(name + "."))
        out.append({"name": name, "entities": n})
    return sorted(out, key=lambda d: (d["entities"] == 0, -d["entities"], d["name"]))


def _browser_url() -> str | None:
    from utils.functions import load_env

    return load_env().get("ARTMIND_KG_NEO4J_BROWSER_URL") or None


def _neo4j_uri() -> str | None:
    from utils.functions import load_env

    return load_env().get("ARTMIND_KG_NEO4J_URI") or None


def _inline_except_domains(cypher: str, parameters: dict) -> str:
    """Cypher for /api/graph/run: every parameter inlined except `$domains`,
    which the graph endpoint binds itself."""
    from artmind.text2cypher import _PARAM_RE, cypher_literal

    def sub(match):
        name = match.group(1)
        if name == "domains":
            return match.group(0)
        if name not in parameters:
            raise ValueError(f"Cypher references ${name} but no value was generated for it")
        return cypher_literal(parameters[name])

    return _PARAM_RE.sub(sub, cypher)


def _package(cypher: str, parameters: dict) -> dict:
    from artmind.text2cypher import browser_deep_link, inline_parameters

    standalone = inline_parameters(cypher, parameters)
    return {
        "cypher": cypher,
        "parameters": parameters,
        "standalone_cypher": standalone,
        "run_cypher": _inline_except_domains(cypher, parameters),
        "browser_url": browser_deep_link(_browser_url(), standalone, _neo4j_uri()),
    }


def _check_read_only(cypher: str) -> None:
    from artmind.webui import graph_routes

    graph_routes.prepare_cypher(cypher)


def register_explore_routes(app: FastAPI) -> FastAPI:
    @app.get("/api/explore/domains")
    async def api_explore_domains():
        try:
            return await anyio.to_thread.run_sync(_domain_entity_counts)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/explore/cypher")
    async def api_explore_cypher(payload: CypherRequest):
        from artmind.graph_query import normalize_domains

        try:
            domains = normalize_domains(payload.domains)
            result = await anyio.to_thread.run_sync(_generate, payload.question, domains)
            return {"question": payload.question, "domains": domains,
                    "warnings": result.get("warnings", []),
                    **_package(result["cypher"], result["parameters"])}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # LLM / Neo4j failure: report it, don't 500 with a stack
            raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/explore/cypher/link")
    async def api_explore_cypher_link(payload: LinkRequest):
        """Deep link for Cypher the operator edited by hand. Self-contained
        queries only (no `$params`), and held to the same read-only rule."""
        from artmind.graph_query import normalize_domains
        from artmind.text2cypher import browser_deep_link, inline_parameters

        try:
            _check_read_only(payload.cypher)
            params = {"domains": normalize_domains(payload.domains)} if payload.domains else {}
            standalone = inline_parameters(payload.cypher, params)
            return {"standalone_cypher": standalone,
                    "browser_url": browser_deep_link(_browser_url(), standalone, _neo4j_uri())}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
