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

def test_vote_button_opens_its_own_step():
    graph = {"start": "ask", "nodes": {
        "ask": {"type": "question", "text": "Насколько зашло?", "key": "score",
            "options": ["1", "5"], "routes": {"1": "soft", "5": "full"}},
        "soft": {"type": "text", "text": "Короткий разбор", "next": "end"},
        "full": {"type": "text", "text": "Полное видео", "buttons": [
            {"text": "Подпишись", "url": "https://t.me/example"}], "next": "end"},
        "end": {"type": "finish"}}}
    validate_graph(graph)

def test_vote_rejects_more_than_five_buttons():
    graph = {"start": "ask", "nodes": {
        "ask": {"type": "question", "text": "Оценка", "key": "score",
            "options": ["1", "2", "3", "4", "5", "6"], "next": "end"},
        "end": {"type": "finish"}}}
    with pytest.raises(ValueError):
        validate_graph(graph)

def test_grant_needs_gate():
    graph = sample()
    graph["nodes"]["grant"]["requires"] = []
    with pytest.raises(ValueError):
        validate_graph(graph)
