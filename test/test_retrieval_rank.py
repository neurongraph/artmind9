import math
import random

import pytest

from artmind import retrieval_rank as R


def p(pid, chunk, doc, cosine=None, vec=None):
    return {"kind": "passage", "id": pid, "chunk_id": chunk, "doc_id": doc,
            **({"cosine": cosine} if cosine is not None else {}),
            **({"vector": vec} if vec is not None else {})}


def test_rrf_then_maxp_groups_passages_into_chunks():
    vec = [p("c1_p01", "c1", "d1", 0.9, [1, 0]), p("c1_p02", "c1", "d1", 0.7, [1, 0]), p("c2_p01", "c2", "d2", 0.6, [0, 1])]
    text = [p("c1_p02", "c1", "d1"), p("c2_p01", "c2", "d2")]
    units = R.passage_fuse(vec, text)
    assert [u.key for u in units] == ["chunk:c1", "chunk:c2"]
    c1, c2 = units
    assert c1.passages_matched == 2 and c1.matched_by == ["vector", "fulltext"]
    assert c1.relevance == 0.9                      # best cosine among its passages
    assert c1.best["id"] == "c1_p02"                # best fused passage: vector rank 2 + text rank 1
    assert c1.score == pytest.approx(2 / 62 + 1 / 61)
    assert c2.score == pytest.approx(2 / 63 + 1 / 62)
    assert c2.matched_by == ["vector", "fulltext"] and c2.relevance == 0.6


def test_vector_rank_outweighs_fulltext_rank():
    units = R.passage_fuse([p("v_p01", "v", "d1", 0.8)], [p("f_p01", "f", "d2")])
    assert [u.key for u in units] == ["chunk:v", "chunk:f"]
    assert units[0].score == pytest.approx(2 * units[1].score)


def test_fulltext_only_unit_has_no_relevance():
    units = R.passage_fuse([], [p("c3_p01", "c3", "d3")])
    assert units[0].relevance is None and units[0].matched_by == ["fulltext"] and units[0].vector is None


def test_chats_are_their_own_units():
    chat = {"kind": "chat", "id": "u1", "cosine": 0.8, "vector": [1, 0], "chat": {"id": "u1"}}
    units = R.passage_fuse([chat], [])
    assert units[0].key == "chat:u1" and units[0].kind == "chat"


def _unit(key, score, doc, vec):
    return R.Unit(key=key, kind="chunk", score=score, relevance=None, matched_by=["vector"],
                  best={}, doc_id=doc, vector=vec, passages_matched=1)


def test_mmr_zero_diversity_is_relevance_order():
    units = [_unit("a", 0.3, "d1", [1, 0]), _unit("b", 0.2, "d1", [1, 0]), _unit("c", 0.1, "d2", [0, 1])]
    assert [u.key for u in R.mmr_select(units, 2, 0.0)] == ["a", "b"]


def test_mmr_demotes_a_second_chunk_of_the_same_document():
    units = [_unit("a", 0.30, "d1", [1, 0]), _unit("b", 0.29, "d1", [0.9, 0.1]), _unit("c", 0.25, "d2", [0, 1])]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "c"]


def test_mmr_same_document_floor_applies_without_vectors():
    units = [_unit("a", 0.30, "d1", None), _unit("b", 0.29, "d1", None), _unit("c", 0.25, "d2", None)]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "c"]


def test_mmr_ties_break_on_score_then_key():
    units = [_unit("b", 0.2, "d1", None), _unit("a", 0.2, "d2", None)]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "b"]


def _naive_mmr(units, top_k, diversity):
    """MMR as defined: recompute every candidate's max similarity to all picks."""
    def cos(a, b):
        if not a or not b:
            return 0.0
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
        return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0

    def sim(a, b):
        s = cos(a.vector, b.vector)
        return max(s, R.SAME_DOC_SIMILARITY) if a.doc_id and a.doc_id == b.doc_id else s

    pool = sorted(units, key=lambda u: (-u.score, u.key))[: 3 * top_k]
    top, picked, rest = pool[0].score, [], list(pool)
    while rest and len(picked) < top_k:
        def value(u):
            red = max((sim(u, q) for q in picked), default=0.0)
            return (1 - diversity) * (u.score / top) - diversity * red
        best = min(rest, key=lambda u: (-value(u), -u.score, u.key))
        picked.append(best)
        rest.remove(best)
    return picked


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("diversity", [0.15, 0.5, 0.9])
def test_mmr_matches_the_naive_definition(seed, diversity):
    rng = random.Random(seed)
    units = [_unit(f"u{i:02d}", rng.random(), f"d{rng.randrange(6)}",
                   None if rng.random() < 0.2 else [rng.gauss(0, 1) for _ in range(8)])
             for i in range(40)]
    want = [u.key for u in _naive_mmr(units, 10, diversity)]
    assert [u.key for u in R.mmr_select(units, 10, diversity)] == want
