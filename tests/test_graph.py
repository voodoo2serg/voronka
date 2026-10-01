import copy
import json
from pathlib import Path
import pytest
from app.graph import validate_graph

def sample():
    return json.loads(Path("examples/mini_course.json").read_text())

def test_valid_graph():
    validate_graph(sample())

def test_missing_edge_is_rejected():
    graph = sample()
    graph["nodes"]["welcome"]["next"] = "missing"
    with pytest.raises(ValueError):
        validate_graph(graph)

def test_cycles_are_rejected():
    graph = sample()
    graph["nodes"]["task"]["next"] = "welcome"
    with pytest.raises(ValueError):
        validate_graph(graph)

def test_grant_needs_gate():
    graph = sample()
    graph["nodes"]["grant"]["requires"] = []
    with pytest.raises(ValueError):
        validate_graph(graph)
