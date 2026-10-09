"""The worked examples shipped inside artmind-create-view must satisfy the contract.

They are templates an agent copies from, so a contract change that breaks one would
teach every future view the broken shape.
"""

from pathlib import Path

import pytest

from artmind.kg_views.store import load_view_dir

EXAMPLES = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-create-view" / "references" / "examples"


@pytest.mark.parametrize("folder", sorted(p for p in EXAMPLES.iterdir() if p.is_dir()), ids=lambda p: p.name)
def test_example_view_is_valid(folder):
    loaded = load_view_dir(folder)
    assert loaded.spec.name == folder.name


def test_there_are_two_examples_covering_two_formats():
    formats = {load_view_dir(p).spec.presentation.format for p in EXAMPLES.iterdir() if p.is_dir()}
    assert {"table", "tree"} <= formats
