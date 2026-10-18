import json

from _insights_helpers import ent, make_ctx
from artmind.insights import narrate
from artmind.insights.model import Finding


def f(key, score, diff="same", name="Asha Bose", stats=None, detector="dormancy"):
    return Finding(key=key, detector=detector, kind="corpus", score=score, title=f"Quiet: {name}", diff=diff,
                   subjects=[{"_id": key, "name": name, "class": "PERSON"}],
                   stats=stats or {"silence_days": 578, "cadence_days": 18})


class StubLLM:
    def __init__(self, reply=None, raises=None):
        self.prompts, self.reply, self.raises = [], reply, raises

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        if self.reply is not None:
            return self.reply(prompt) if callable(self.reply) else self.reply
        keys = [item["key"] for item in json.loads(prompt.split("Findings:\n", 1)[1])]
        return json.dumps({k: "Asha Bose has not appeared for 578 days." for k in keys})


def ctx_for():
    return make_ctx([ent("p1", "PERSON", ["2024-01-01"], name="Asha Bose"),
                     ent("p2", "PERSON", ["2024-01-01"], name="Ben Okoro")])


def test_selection_budget_top_plus_new():
    fs = [f(f"k{i}", score=1 - i / 100, diff="new" if i % 2 else "same") for i in range(40)]
    fs.append(f("gone", 0.99, diff="gone"))
    chosen = narrate.select_for_narration(fs, top=5)
    keys = [x.key for x in chosen]
    assert keys[:5] == ["k0", "k1", "k2", "k3", "k4"] and len(keys) == 10 and "gone" not in keys
    assert all(int(k[1:]) % 2 for k in keys[5:])


def test_narrates_batches_checks_and_caches():
    ctx, llm = ctx_for(), StubLLM()
    fs = [f(f"k{i}", 0.9 - i / 100) for i in range(25)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx, llm=llm, top=25)
    assert len(llm.prompts) == 2                     # 20 + 5
    assert stats == {"narrated": 25, "cached": 0, "rejected": 0, "calls": 2, "errors": 0}
    assert all(x.narration and x.narration_key for x in fs)

    again = [f(f"k{i}", 0.9 - i / 100) for i in range(25)]
    llm2 = StubLLM()
    stats2 = narrate.narrate_findings(again, previous=fs, ctx=ctx, llm=llm2, top=25)
    assert llm2.prompts == [] and stats2["cached"] == 25 and again[0].narration == fs[0].narration


def test_changed_stats_invalidate_the_cache():
    ctx = ctx_for()
    first = [f("k0", 0.9)]
    narrate.narrate_findings(first, previous=None, ctx=ctx, llm=StubLLM(), top=5)
    changed = [f("k0", 0.9, stats={"silence_days": 600, "cadence_days": 18})]
    llm = StubLLM(reply=lambda p: json.dumps({"k0": "Asha Bose has not appeared for 600 days."}))
    narrate.narrate_findings(changed, previous=first, ctx=ctx, llm=llm, top=5)
    assert len(llm.prompts) == 1 and "600" in changed[0].narration


def test_rejected_and_failed_narrations_leave_none():
    ctx = ctx_for()
    bad = StubLLM(reply=json.dumps({"k0": "Asha Bose stopped seeing Ben Okoro.", "k1": ""}))
    fs = [f("k0", 0.9), f("k1", 0.8)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx, llm=bad, top=5)
    assert [x.narration for x in fs] == [None, None] and stats["rejected"] == 2
    boom = [f("k0", 0.9)]
    stats = narrate.narrate_findings(boom, previous=None, ctx=ctx, llm=StubLLM(raises=RuntimeError("down")), top=5)
    assert boom[0].narration is None and stats["errors"] == 1


def test_unparseable_reply_counts_as_error():
    fs = [f("k0", 0.9)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx_for(), llm=StubLLM(reply="not json at all"), top=5)
    assert fs[0].narration is None and stats["errors"] == 1


def circle(cid, members, name=None, source="template"):
    return {"id": cid, "class": "PERSON", "lead": members[0], "name": name or f"Circle around {members[0]}",
            "name_source": source, "members": members}


def test_circle_naming_and_drift_reuse():
    ents = [ent(i, "PERSON", ["2024-01-01"], name=i.upper()) for i in "abcdefghij"]
    ents.append(ent("org", "ORGANIZATION", ["2024-01-01"], name="Acme"))
    ctx = make_ctx(ents, entity_props={("PERSON", "relationship_to_diarist"): {"a": "colleague"}})
    llm = StubLLM(reply=lambda p: json.dumps({"PERSON:a": "Work team", "PERSON:f": "Team 7"}))
    circles = [circle("PERSON:a", list("abcde")), circle("PERSON:f", list("fghij"))]
    stats = narrate.name_circles(circles, previous=None, ctx=ctx, llm=llm)
    assert circles[0]["name"] == "Work team" and circles[0]["name_source"] == "llm"
    assert circles[1]["name"] == "Circle around f" and circles[1]["name_source"] == "template"   # digits rejected
    assert '"colleague"' in llm.prompts[0] and "Acme" in llm.prompts[0]
    assert stats == {"named": 1, "kept": 0, "rejected": 1, "calls": 1, "errors": 0}

    drifted = [circle("PERSON:b", list("abcde") + ["z"]), circle("PERSON:f", list("fghij"))]
    llm2 = StubLLM(reply=lambda p: json.dumps({"PERSON:f": "Old friends"}))
    stats2 = narrate.name_circles(drifted, previous=circles, ctx=ctx, llm=llm2)
    assert drifted[0]["name"] == "Work team" and drifted[0]["name_source"] == "llm" and stats2["kept"] == 1
    assert drifted[1]["name"] == "Old friends" and "PERSON:b" not in llm2.prompts[0]


def test_circle_failure_keeps_template():
    ctx = make_ctx([ent("a", "PERSON", ["2024-01-01"])])
    cs = [circle("PERSON:a", ["a"])]
    stats = narrate.name_circles(cs, previous=None, ctx=ctx, llm=StubLLM(raises=RuntimeError("x")))
    assert cs[0]["name_source"] == "template" and stats["errors"] == 1


def test_circle_findings_get_the_circle_name():
    cs = [circle("PERSON:a", list("abc"), name="Work team", source="llm")]
    fs = [Finding(key="communities:PERSON:circle:a", detector="communities", kind="corpus", score=0.5,
                  title="Circle around A (3)", stats={"members": list("abc")}),
          Finding(key="communities:PERSON:share:a", detector="communities", kind="corpus", score=0.5,
                  title="Circle around A: share of mentions", stats={"members": list("abc")})]
    narrate.label_circle_findings(fs, cs)
    assert all(x.stats["circle_name"] == "Work team" for x in fs)
