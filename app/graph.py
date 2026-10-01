TYPES = {"text", "photo", "video", "voice", "document", "question", "survey",
         "condition", "wait", "grant", "stage", "task", "finish"}
SEND = {"text", "photo", "video", "voice", "document"}

def validate_graph(graph):
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict) or not 1 <= len(nodes) <= 200:
        raise ValueError("Нужно от 1 до 200 узлов")
    if graph.get("start") not in nodes:
        raise ValueError("Нет начального узла")
    refs = {}
    for key, node in nodes.items():
        if not isinstance(key, str) or len(key) > 80 or not isinstance(node, dict):
            raise ValueError("Неверный узел")
        kind = node.get("type")
        if kind not in TYPES:
            raise ValueError(f"Неподдерживаемый тип: {kind}")
        outgoing = [node.get("next")] if kind != "finish" else []
        if kind == "condition":
            outgoing = [node.get("yes"), node.get("no")]
            if not isinstance(node.get("key"), str):
                raise ValueError("У условного узла нет key")
        if any(x not in nodes for x in outgoing):
            raise ValueError(f"Неверный переход из {key}")
        refs[key] = outgoing
        if kind in SEND | {"question"}:
            if not isinstance(node.get("text"), str) or not node["text"]:
                raise ValueError(f"Нет текста: {key}")
        if kind in SEND - {"text"} and not str(node.get("media", "")).startswith("https://"):
            raise ValueError("Медиа в этой версии: публичный HTTPS URL")
        if kind == "wait" and not 1 <= node.get("seconds", 0) <= 2592000:
            raise ValueError("Ожидание: 1–2592000 секунд")
        if kind == "question" and (not isinstance(node.get("key"), str) or not node["key"]):
            raise ValueError("Нет ключа ответа")
        if kind == "survey":
            qs = node.get("questions", [])
            if not 1 <= len(qs) <= 30 or not node.get("key"):
                raise ValueError("Опрос: 1–30 вопросов и key")
            if any(not q.get("key") or not q.get("text") for q in qs):
                raise ValueError("Вопросы требуют key и text")
            if len({q["key"] for q in qs}) != len(qs):
                raise ValueError("Повтор ключа вопроса")
        if kind == "grant" and (not node.get("product") or not node.get("requires")):
            raise ValueError("Выдача продукта требует product и requires")
        if kind in {"stage", "task"} and not node.get("text"):
            raise ValueError("Нужен текст")
    # Only acyclic graphs in baseline: eliminates runaway message loops.
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            raise ValueError("Циклы пока не поддерживаются")
        if key in visited:
            return
        visiting.add(key)
        for nxt in refs[key]:
            visit(nxt)
        visiting.remove(key)
        visited.add(key)
    visit(graph["start"])
    if len(visited) != len(nodes):
        raise ValueError("Есть недостижимые узлы")
    return graph
