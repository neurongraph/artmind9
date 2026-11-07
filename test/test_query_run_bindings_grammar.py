"""Step bindings: `{node.output}` placeholders and their load rules (P3 spec §4.1–§4.2). Invented routes."""

import pytest

from artmind.query_plan import program as prog
from artmind.query_plan import routes

VT = "artmind query vector-text --shape brief --domain {domains} --topK 5 {search_term} --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain {domains} --documentName {doc} --compact"


def _route(program, guards=None):
    entry = {"id": "fixture", "description": "d", "triggers": [], "needs": [], "kind": "one_step",
             "program": program, "read": [], "evidence": ["S1"]}
    if guards is not None:
        entry["guards"] = guards
    return routes.parse_routes({"version": 2, "routes": [entry]})[0]


def test_bound_placeholders_are_found_and_kept_apart():
    assert routes.placeholders(CHUNKS) == {"anchor.top_chunk"}
    assert routes.bound_placeholders(CHUNKS) == {("anchor", "top_chunk")}
    assert routes.bound_placeholders(VT) == set()


def test_render_leaves_bound_placeholders_and_still_counts_as_filled():
    out = routes.render(CHUNKS, {})
    assert out == CHUNKS


def test_a_binding_to_an_earlier_node_loads():
    route = _route([{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS}])
    node = prog.run_nodes(route.program)[1]
    assert prog.node_binds(node) == (("anchor", "top_chunk"),)


def test_a_map_over_a_bound_list_loads():
    route = _route([{"id": "anchor", "run": VT},
                    {"id": "dated", "map": {"over": "anchor.window_docs", "as": "doc", "max": 6,
                                            "do": {"id": "day", "run": P10}}}])
    dated = route.program[1]
    assert dated.map.over == "anchor.window_docs"


@pytest.mark.parametrize("program, message", [
    ([{"id": "around", "run": CHUNKS}], "unknown node"),
    ([{"id": "around", "run": CHUNKS}, {"id": "anchor", "run": VT}], "not run yet"),
    ([{"id": "p", "par": [{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS}]}], "same par"),
    ([{"id": "anchor", "run": VT}, {"id": "around", "run": CHUNKS.replace("top_chunk", "nope")}], "unknown output"),
    ([{"id": "anchor", "run": VT}, {"id": "around", "run": P10.replace("{doc}", "{anchor.doc_names}")}], "list output"),
    ([{"id": "anchor", "run": VT},
      {"id": "dated", "map": {"over": "anchor.top_doc", "as": "doc", "do": {"id": "day", "run": P10}}}], "scalar output"),
    ([{"id": "anchor", "run": VT},
      {"id": "around", "run": "artmind query chunks --idList x{anchor.top_chunk} --compact"}], "whole token"),
])
def test_bad_bindings_fail_at_load(program, message):
    with pytest.raises(routes.RouteError, match=message):
        _route(program)


def test_a_list_output_may_feed_a_comma_split_option():
    route = _route([{"id": "anchor", "run": VT},
                    {"id": "around", "run": "artmind query chunks --idList {anchor.chunk_ids} --shape brief --compact"}])
    assert prog.node_binds(prog.run_nodes(route.program)[1]) == (("anchor", "chunk_ids"),)


def test_the_packaged_routes_still_load_and_use_no_binding():
    for route in routes.load_routes():
        for choice in route.programs:
            assert not any(prog.node_binds(n) for n in prog.run_nodes(choice.program)), route.id


def test_a_v1_steps_template_still_rejects_a_dotted_placeholder():
    entry = {"id": "fixture", "description": "d", "triggers": [], "needs": [],
             "steps": ["artmind query chunks --idList {anchor.top_chunk} --compact"], "read": [], "evidence": ["S1"]}
    with pytest.raises(routes.RouteError, match="unknown placeholder"):
        routes.parse_routes({"version": 2, "routes": [entry]})


def test_node_ids_with_digits_can_be_bound():
    route = _route([{"id": "s1", "run": VT}, {"id": "s2", "run": CHUNKS.replace("anchor", "s1")}])
    assert prog.node_binds(prog.run_nodes(route.program)[1]) == (("s1", "top_chunk"),)


def test_a_binding_inside_a_map_body_may_name_a_node_before_the_map():
    route = _route([{"id": "anchor", "run": VT},
                    {"id": "each", "map": {"over": "twin_ids", "as": "doc",
                                           "do": {"id": "day", "run": CHUNKS}}}])
    assert prog.node_binds(prog.run_nodes(route.program)[1]) == (("anchor", "top_chunk"),)


def test_a_binding_inside_a_map_body_may_not_name_a_par_sibling_of_the_map():
    with pytest.raises(routes.RouteError, match="same par"):
        _route([{"id": "p", "par": [{"id": "anchor", "run": VT},
                                    {"id": "each", "map": {"over": "twin_ids", "as": "doc",
                                                           "do": {"id": "day", "run": CHUNKS}}}]}])


def test_entity_class_is_not_a_list_option():
    with pytest.raises(routes.RouteError, match="may only feed"):
        _route([{"id": "a", "run": VT},
                {"id": "b", "run": "artmind query graph entity-listing --entityClass {a.entity_ids} --compact"}])
