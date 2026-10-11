"""Keep the local `trace_reviews` index in step with the dossier files
(spec 2026-10-11 §8.1). The files win: `reindex` rebuilds the table."""

from pathlib import Path

from artmind.reviews.dossier import Dossier, iter_dossiers


def index_dossier(dossier: Dossier, path: Path | None) -> None:
    from artmind.traces import store

    meta = dossier.meta
    traces = [*(meta.get("trace_ids") or []), *(meta.get("verify_trace_ids") or [])]
    store.index_review(dossier.review_id, traces, stage=meta.get("stage"),
                       elements=list(meta.get("elements") or []), status=meta.get("status") or "open",
                       dossier_path=str(path) if path else None)


def reindex() -> dict:
    from artmind.traces import store

    store.clear_review_index()
    indexed = links = 0
    invalid = []
    for path, dossier, error in iter_dossiers():
        if error:
            invalid.append({"path": str(path), "error": error})
            continue
        index_dossier(dossier, path)
        indexed += 1
        links += len(dict.fromkeys([*(dossier.meta.get("trace_ids") or []),
                                    *(dossier.meta.get("verify_trace_ids") or [])]))
    return {"indexed": indexed, "links": links, "invalid": invalid}
