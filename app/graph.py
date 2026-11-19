from urllib.parse import urlparse

MEDIA = {"photo", "video", "video_note", "voice", "document"}
SEND = {"text"} | MEDIA
# escalate / operator_pause = pause for human (≠ handoff bot switch).
# llm_reply = draft-first LLM node (PDialog P1 / Signal#20); auto_send is never a publish field.
TYPES = SEND | {
    "question", "survey", "condition", "wait", "grant", "stage", "task", "finish", "goal",
    "handoff", "llm_reply", "escalate", "operator_pause",
}
LLM_MODES = {"draft_only", "human_approval"}
LLM_PROVIDERS = {"raga", "openrouter", "ollama", "openai_compatible"}
RESERVED = "_"

def checked_text(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label}: нужен текст длиной 1–{maximum}")
    return value

def checked_key(value):
    checked_text(value, "Ключ ответа", 80)
    if value.startswith(RESERVED):
        raise ValueError("Ключи, начинающиеся с _, зарезервированы")
    return value

def checked_options(options):
    if not isinstance(options, list) or len(options) > 20:
        raise ValueError("Варианты ответа: список до 20 вариантов")
    for option in options:
        checked_text(option, "Вариант ответа", 100)
    if len(set(options)) != len(options):
        raise ValueError("Повтор варианта ответа")
    return options

def secure_url(value):
    if not isinstance(value, str):
        return False
    url = urlparse(value)
    return url.scheme == "https" and bool(url.hostname) and not url.username and not url.password

def validate_graph(graph):
    if not isinstance(graph, dict):
        raise ValueError("Сценарий должен быть объектом")
    nodes = graph.get("nodes")
    if not isinstance(nodes, dict) or not 1 <= len(nodes) <= 200:
        raise ValueError("Нужно от 1 до 200 узлов")
    if not isinstance(graph.get("start"), str) or graph["start"] not in nodes:
        raise ValueError("Нет начального узла")
    refs, response_keys = {}, set()
    for key, node in nodes.items():
        checked_text(key, "ID узла", 80)
        if not isinstance(node, dict):
            raise ValueError("Неверный узел")
        kind = node.get("type")
        if not isinstance(kind, str) or kind not in TYPES:
            raise ValueError(f"Неподдерживаемый тип: {kind}")
        outgoing = [] if kind == "finish" else [node.get("next")]
        if kind == "condition":
            outgoing = [node.get("yes"), node.get("no")]
            checked_key(node.get("key"))
            if "equals" not in node:
                node = {**node, "equals": True}
        if kind == "question":
            options = checked_options(node.get("options", []))
            if len(options) > 5:
                raise ValueError("Голосование: не больше 5 кнопок")
            routes = node.get("routes") or {}
            if not isinstance(routes, dict):
                raise ValueError("Сценарии кнопок заданы неверно")
            if set(routes) - set(options):
                raise ValueError("Сценарий указан для неизвестной кнопки")
            if routes:
                outgoing = []
                for option in options:
                    outgoing.append(routes.get(option, node.get("next")))
        if kind == "wait":
            seconds = node.get("seconds")
            if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 2592000:
                raise ValueError("Ожидание: 1–2592000 секунд")
            if "cancel_on_reply" in node and not isinstance(node.get("cancel_on_reply"), bool):
                raise ValueError(f"wait {key}: cancel_on_reply должен быть bool")
            if node.get("cancel_on_reply") and node.get("on_reply") is not None:
                outgoing = [node.get("next"), node.get("on_reply")]
        if kind == "llm_reply":
            # P0 publish descriptor: id?, provider?, model?, temperature?, max_tokens?,
            # rules_version (required), mode draft_only|human_approval, fallback?, prompt_ref?
            # auto_send is intentionally not accepted as a publish field.
            if "auto_send" in node:
                raise ValueError(f"llm_reply {key}: auto_send не publish-поле (всегда false)")
            pin = node.get("rules_version")
            if not isinstance(pin, str) or not pin.strip() or len(pin) > 120:
                raise ValueError(f"llm_reply {key}: нужен rules_version (pin 1–120)")
            mode = node.get("mode", "draft_only")
            if mode not in LLM_MODES:
                raise ValueError(f"llm_reply {key}: mode draft_only|human_approval")
            if "provider" in node and node["provider"] is not None:
                if node["provider"] not in LLM_PROVIDERS:
                    raise ValueError(f"llm_reply {key}: неизвестный provider")
            if "model" in node and node["model"] is not None:
                if not isinstance(node["model"], str) or len(node["model"]) > 120:
                    raise ValueError(f"llm_reply {key}: model строка до 120")
            if "temperature" in node and node["temperature"] is not None:
                temp = node["temperature"]
                if isinstance(temp, bool) or not isinstance(temp, (int, float)) or not 0 <= float(temp) <= 2:
                    raise ValueError(f"llm_reply {key}: temperature 0–2")
            if "max_tokens" in node and node["max_tokens"] is not None:
                cap = node["max_tokens"]
                if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 8192:
                    raise ValueError(f"llm_reply {key}: max_tokens 1–8192")
            if "fallback" in node and node["fallback"] is not None:
                if not isinstance(node["fallback"], str) or len(node["fallback"]) > 4000:
                    raise ValueError(f"llm_reply {key}: fallback текст до 4000")
            if "prompt_ref" in node and node["prompt_ref"] is not None:
                if not isinstance(node["prompt_ref"], str) or len(node["prompt_ref"]) > 160:
                    raise ValueError(f"llm_reply {key}: prompt_ref до 160")
            if "id" in node and node["id"] is not None:
                if not isinstance(node["id"], str) or not node["id"].strip() or len(node["id"]) > 80:
                    raise ValueError(f"llm_reply {key}: id 1–80")
        if kind in {"escalate", "operator_pause"}:
            # Operator pause — do not confuse with handoff (bot switch).
            title = node.get("text") or node.get("title") or "Эскалация оператору"
            checked_text(title, f"escalate {key}", 160)
        if any(not isinstance(x, str) or x not in nodes for x in outgoing):
            raise ValueError(f"Неверный переход из {key}")
        refs[key] = outgoing
        if kind in SEND | {"question"}:
            maximum = 1024 if kind in MEDIA else 4000
            # video_note caption is delivered in a separate text message.
            if kind == "video_note":
                maximum = 4000
            checked_text(node.get("text", ""), f"Текст узла {key}", maximum)
        if kind in MEDIA:
            if not node.get("asset_id") and not secure_url(node.get("media")):
                raise ValueError("Выберите материал из медиабанка или HTTPS URL")
            if node.get("asset_id") and (not isinstance(node["asset_id"], str) or len(node["asset_id"]) > 36):
                raise ValueError("Неверный asset_id")
            if kind == "video_note" and not node.get("asset_id"):
                raise ValueError("Кружок выбирается из медиабанка")
        if node.get("buttons"):
            if not isinstance(node["buttons"], list) or len(node["buttons"]) > 20:
                raise ValueError("Не больше 20 ссылочных кнопок")
            for button in node["buttons"]:
                checked_text(button.get("text"), "Кнопка", 100)
                if not secure_url(button.get("url")):
                    raise ValueError("Кнопка требует HTTPS URL")
        if kind in {"question", "survey"}:
            response_key = checked_key(node.get("key"))
            if response_key in response_keys:
                raise ValueError("У вопросов и опросов должны быть разные ключи")
            response_keys.add(response_key)
        if kind == "question":
            if node.get("answer_type", "text") not in {"text", "email", "phone"}:
                raise ValueError("Тип ответа: text/email/phone")
        if kind == "survey":
            qs = node.get("questions")
            if not isinstance(qs, list) or not 1 <= len(qs) <= 30 or any(not isinstance(q, dict) for q in qs):
                raise ValueError("Опрос: 1–30 вопросов")
            keys = set()
            for q in qs:
                k = checked_key(q.get("key"))
                checked_text(q.get("text"), "Вопрос")
                checked_options(q.get("options", []))
                if k in keys:
                    raise ValueError("Повтор ключа вопроса")
                keys.add(k)
        if kind == "grant":
            checked_text(node.get("product"), "Ключ продукта", 80)
            requirements = node.get("requires")
            if not isinstance(requirements, list) or not requirements:
                raise ValueError("Выдача продукта требует requires")
            for requirement in requirements:
                checked_key(requirement)
        if kind in {"stage", "task", "goal"}:
            checked_text(node.get("text"), "Текст", 160)
        if kind == "handoff":
            cid = node.get("connection_id")
            if not isinstance(cid, str) or not cid.strip() or len(cid) > 36:
                raise ValueError(f"handoff {key}: нужен connection_id")
    # Validate every path, not only the path chosen by a preview.
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

def validate_bindings(db, graph, connection_ids):
    from app.db import Asset, Connection
    allowed = set(connection_ids)
    for cid in connection_ids:
        connection = db.get(Connection, cid)
        if not connection or not connection.active:
            raise ValueError("Подключение отсутствует или отключено")
        if connection.platform not in {"telegram", "max", "vk"}:
            raise ValueError("Это подключение используется только для CRM-рассылок")
        for node in graph["nodes"].values():
            if node.get("asset_id"):
                asset = db.get(Asset, node["asset_id"])
                if not asset or cid not in asset.connection_ids or asset.kind != node["type"]:
                    raise ValueError("Материал недоступен подключению или не соответствует типу шага")
                if connection.platform == "telegram":
                    if not asset.local_path and cid not in asset.telegram_refs:
                        raise ValueError("Telegram file_id нельзя использовать в другом боте")
                elif not secure_url(node.get("media")):
                    raise ValueError("Для MAX/VK задайте резервный HTTPS URL материала")
    for node in graph["nodes"].values():
        if node.get("type") == "handoff":
            target = node.get("connection_id")
            if target not in allowed:
                raise ValueError("handoff connection_id должен быть в connection_ids сценария")
            target_conn = db.get(Connection, target)
            if not target_conn or not target_conn.active:
                raise ValueError("handoff: целевое подключение отсутствует или отключено")
