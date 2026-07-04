"""ingest_to_kg auto-chains normalize-time but NOT refine-graph/detect-conflicts."""
import inspect
import artmind.ingest as ing


def test_ingest_to_kg_calls_normalize_after_write():
    src = inspect.getsource(ing.ingest_to_kg)
    assert "normalize_ingested_document" in src
    assert src.index("write_to_graph") < src.index("normalize_ingested_document")


def test_ingest_to_kg_does_not_call_refine_or_detect():
    src = inspect.getsource(ing.ingest_to_kg)
    assert "refine_graph" not in src
    assert "detect_conflicts" not in src
