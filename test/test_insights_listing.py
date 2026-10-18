from artmind.insights.listing import finding_summary, list_findings
from artmind.insights.model import Finding, Run
from artmind.insights.sameas_merges import build_merges


def F(key, score, diff="same", detector="dormancy", kind="corpus", **kw):
    return Finding(key=key, detector=detector, kind=kind, score=score, title=key, diff=diff, **kw)


def run(findings):
    return Run(domain="demo", created_at="t", as_of="2026-10-01", fingerprint={}, anchors=[], profile={},
               plan=[], circles=[], diff={}, findings=findings)


def test_list_filters_and_ranks():
    r = run([F("a", 0.2), F("b", 0.9), F("g", 0.99, diff="gone"), F("h", 0.5, kind="health", detector="kind_mismatch")])
    assert [f.key for f in list_findings(r)] == ["b", "h", "a"]
    assert [f.key for f in list_findings(r, kind="health")] == ["h"]
    assert [f.key for f in list_findings(r, detector="dormancy", limit=1)] == ["b"]
    assert [f.key for f in list_findings(r, only_new=True)] == ["g"]


def test_summary_carries_narration_and_subject_ids():
    f = F("a", 0.5, narration="A has gone quiet.", subjects=[{"_id": "p1", "name": "A", "class": "PERSON"}])
    s = finding_summary(f)
    assert s["narration"] == "A has gone quiet." and s["subjects"] == [{"_id": "p1", "name": "A", "class": "PERSON"}]
    assert set(s) == {"key", "detector", "kind", "score", "title", "narration", "diff", "action", "subjects"}


def cand(a, b, score, aid=None, bid=None):
    return Finding(key=f"same_as_candidates:PERSON:{a}:{b}", detector="same_as_candidates", kind="health",
                   score=score, title="t", action="curate",
                   stats={"canonical": {"_id": aid or a, "name": a, "class": "PERSON"},
                          "alias": {"_id": bid or b, "name": b, "class": "PERSON"}, "rule": "kinship"})


def test_build_merges_guards_chains_and_ambiguous_names():
    merges, candidates, skipped = build_merges([cand("Dad", "Daddy", 0.9), cand("Daddy", "Da", 0.8),
                                                cand("Sam", "S", 0.7, bid="s1"), cand("Lee", "S", 0.6, bid="s2")])
    assert merges == {"Daddy": "Dad"} and skipped == {"chain": 1, "ambiguous_name": 2}
    assert candidates[0]["alias"] == "Daddy"
