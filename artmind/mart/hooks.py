"""Best-effort `mart build` after projection rebuild and vault sync (spec §8): a failure is
reported, never raised, so a rebuild or sync never fails because of the mart."""
from __future__ import annotations

import paths


def _declared(domains) -> list[str]:
    directory = paths.DOMAIN_MART_DIR
    have = {p.stem for p in directory.glob("*.yaml")} if directory.is_dir() else set()
    return [d for d in domains if d in have]


def _build(domain) -> dict:
    from artmind.mart.build import build
    from artmind.mart.declaration import load_declaration

    return build(load_declaration(domain), [domain], dry_run=False)


def build_after(domains) -> dict[str, str]:
    out = {}
    for d in _declared(sorted(set(domains))):
        try:
            _build(d)
            out[d] = "built"
        except Exception as e:  # never break the caller
            out[d] = f"failed: {e}"
    return out
