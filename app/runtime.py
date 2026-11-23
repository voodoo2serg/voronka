import re, secrets, random
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func
from app.db import Contact, Identity, Run, Version, Entry, Funnel, Outbox, Event, Entitlement, Task, Asset, Admin, now

LIVE = ["active", "waiting", "delayed", "paused", "blocked"]
class AwaitDelivery(Exception):
    """Durably keep this input until the prompt's send result is known."""

def uid():
    return str(uuid4())


def _normalize_variants(raw):
    out = []
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        else:
            continue
        if text:
            out.append(text)
    return out


def _next_round_robin_index(n: int, state, *, version_id: str | None, node_key: str | None) -> int:
    """Shared cursor across Runs for the same Version node (Redis); fallback = run state."""
    if n <= 0:
        return 0
    if version_id and node_key:
        try:
            from app import auth
            key = f"variant-rr:{version_id}:{node_key}"
            count = auth.cache.incr(key)
            if count == 1:
                # Keep counter for the life of a published series experiment (~90d).
                auth.cache.expire(key, 90 * 86400)
            return (int(count) - 1) % n
        except Exception:
            pass
    idx0 = int(state.get("_variant_rr") or 0) % n
    state["_variant_rr"] = idx0 + 1
    return idx0


def pick_variant_text(node, state, *, version_id: str | None = None, node_key: str | None = None):
    """Resolve text node body: static text or variants[] pick (VR-406 / SIG-UX-04)."""
    variants = _normalize_variants(node.get("variants"))
    if not variants:
        return node.get("text") or "", None
    pick = node.get("pick") or "random"
    if pick == "round_robin":
        idx0 = _next_round_robin_index(
            len(variants), state, version_id=version_id, node_key=node_key
        )
    else:
        idx0 = random.randrange(len(variants))
    # 1-based index for staff markers «(вариант N)»
    return variants[idx0], idx0 + 1


def match_intent_router(node, text: str) -> str | None:
    """Return matched route key from patterns then routes keywords (Signal simulate parity)."""
    hay = (text or "").strip().lower()
    if not hay:
        return None
    for pat in node.get("patterns") or []:
        if not isinstance(pat, dict):
            continue
        needle = str(pat.get("match") or "").strip().lower()
        intent = str(pat.get("intent") or "").strip()
        if needle and intent and needle in hay:
            return intent
    routes = node.get("routes") or {}
    if isinstance(routes, dict):
        for keyword in routes:
            if str(keyword).lower() in hay:
                return str(keyword)
    return None


def resolve_intent_router_next(node, matched: str | None) -> str | None:
    routes = node.get("routes") or {}
    if matched is not None and isinstance(routes, dict) and matched in routes:
        target = routes.get(matched)
        if isinstance(target, str) and target.strip():
            return target
    miss = node.get("on_miss") or node.get("next")
    return miss if isinstance(miss, str) and miss.strip() else None

def event(db, kind, connection_id=None, contact_id=None, run_id=None, actor_id=None, **payload):
    db.add(Event(id=uid(), kind=kind, connection_id=connection_id, contact_id=contact_id,
                 run_id=run_id, actor_id=actor_id, payload=payload))

def queue(db, identity, payload, run=None):
    position = 0
    if run:
        position = (db.scalar(select(func.max(Outbox.position)).where(Outbox.run_id == run.id)) or 0) + 1
    out = Outbox(id=uid(), connection_id=identity.connection_id, identity_id=identity.id,
        run_id=run.id if run else None, chat_id=identity.chat_id, payload=payload,
        position=position, created_at=now(), due_at=now())
    db.add(out)
    return out

def prompt(db, run, identity, question, state):
    nonce = secrets.token_urlsafe(8)
    state["_prompt"] = nonce
    options = question.get("options", [])
    payload = {"type": "text", "text": question["text"]}
    if options:
        payload["choices"] = [{"text": option, "data": f"a:{nonce}:{index}"}
                              for index, option in enumerate(options)]
    queue(db, identity, payload, run)

def pause_for_operator(db, run, identity, contact, *, reason: str, task_title: str | None = None):
    """Pause automation for human takeover (same path as POST /api/messages). Not bot handoff."""
    if run.status != "paused":
        run.state = {**dict(run.state), "_before_pause": run.status, "_pause_reason": reason}
        run.status = "paused"
    for out in db.scalars(select(Outbox).where(
        Outbox.run_id == run.id, Outbox.status == "pending"
    ).with_for_update()):
        out.status = "held"
    if task_title:
        db.add(Task(
            id=uid(),
            contact_id=contact.id,
            connection_id=identity.connection_id,
            title=task_title[:500],
        ))
    event(db, "run.operator_pause", identity.connection_id, contact.id, run.id, reason=reason)

def _published_rules_text(version, rules_version: str) -> str | None:
    """Optional pin map on Version.graph until Signal publish lands: graph._rules[rules_version]."""
    blob = (version.graph or {}).get("_rules")
    if isinstance(blob, dict):
        text = blob.get(rules_version)
        if isinstance(text, str) and text.strip():
            return text
    return None

def _execute_llm_reply(db, run, identity, contact, node, key, state, connection):
    from app import ai_providers
    from app.crm_models import LlmReplyAudit

    version = db.get(Version, run.version_id)
    node_id = node.get("id") or key
    mode = node.get("mode") or "draft_only"
    rules_version = node["rules_version"]
    project_id = connection.project_id if connection else None
    published = _published_rules_text(version, rules_version)
    # Prefer Version.graph meta.rules_text from Signal publish when pin matches.
    if not published:
        meta = (version.graph or {}).get("meta") or {}
        if meta.get("rules_version") == rules_version and isinstance(meta.get("rules_text"), str):
            published = meta.get("rules_text")
    resolved = ai_providers.resolve_rules_pin(
        db,
        rules_version=rules_version,
        project_id=project_id,
        connection_id=identity.connection_id,
        published_rules_text=published,
    )
    provider = node.get("provider") or resolved.get("provider") or "raga"
    model = node.get("model") or resolved.get("model") or ""
    temperature = node.get("temperature")
    max_tokens = node.get("max_tokens")
    question = (
        state.get("_last_inbound")
        or state.get("last_message")
        or node.get("prompt_ref")
        or "Подготовь черновик ответа клиенту по сценарию."
    )
    context = {
        "run_id": run.id,
        "node_id": node_id,
        "contact_stage": contact.stage,
        "state_keys": sorted(k for k in state.keys() if not str(k).startswith("_")),
    }
    draft_text = ""
    latency_ms = None
    error_class = None
    response_status = "ok"
    try:
        draft_text, latency_ms = ai_providers.complete_draft_timed(
            provider=provider,
            model=model,
            system_rules=resolved.get("system_rules") or "",
            question=str(question)[:4000],
            context=context,
            project_id=project_id or "",
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        error_class = ai_providers.classify_error(exc)
        response_status = "timeout" if error_class == "timeout" else "error"
        fallback = node.get("fallback")
        if isinstance(fallback, str) and fallback.strip():
            draft_text = fallback.strip()
            response_status = "fallback"
        else:
            draft_text = (
                "Спасибо за сообщение. Менеджер скоро ответит лично — "
                "автоматический черновик сейчас недоступен."
            )
            response_status = "fallback"
            if mode == "auto_framed":
                # Course-lead soft path: branch without operator park when AI unavailable.
                audit = LlmReplyAudit(
                    id=uid(),
                    execution_id=run.id,
                    node_id=node_id,
                    run_id=run.id,
                    identity_id=identity.id,
                    contact_id=contact.id,
                    connection_id=identity.connection_id,
                    project_id=project_id,
                    provider=provider,
                    model=model or "",
                    rules_version=rules_version,
                    mode=mode,
                    response_status=response_status,
                    latency_ms=latency_ms,
                    error_class=error_class,
                    draft_text=draft_text[:20000],
                    created_at=now(),
                    updated_at=now(),
                )
                db.add(audit)
                db.flush()
                event(
                    db, "llm_reply.out_of_frame", identity.connection_id, contact.id, run.id,
                    node=node_id, error_class=error_class, audit_id=audit.id, mode=mode,
                )
                nxt = node.get("on_out_of_frame") or node.get("next")
                if nxt:
                    state["_force_next"] = nxt
                    run.state = state
                    return False  # advance honors _force_next
                pause_for_operator(
                    db, run, identity, contact,
                    reason="llm_error",
                    task_title=f"LLM ошибка ({error_class}): нужен ответ клиенту",
                )
                run.state = state
                return True
            audit = LlmReplyAudit(
                id=uid(),
                execution_id=run.id,
                node_id=node_id,
                run_id=run.id,
                identity_id=identity.id,
                contact_id=contact.id,
                connection_id=identity.connection_id,
                project_id=project_id,
                provider=provider,
                model=model or "",
                rules_version=rules_version,
                mode=mode,
                response_status=response_status,
                latency_ms=latency_ms,
                error_class=error_class,
                draft_text=draft_text[:20000],
                created_at=now(),
                updated_at=now(),
            )
            db.add(audit)
            db.flush()
            state["_llm_draft_id"] = audit.id
            state["_await"] = "llm_approval"
            pause_for_operator(
                db, run, identity, contact,
                reason="llm_error",
                task_title=f"LLM ошибка ({error_class}): нужен ответ клиенту",
            )
            event(
                db, "llm_reply.fallback_escalate", identity.connection_id, contact.id, run.id,
                node=node_id, error_class=error_class, audit_id=audit.id,
            )
            run.state = state
            return True  # stop advance

    # auto_framed (VR-406 course-lead): queue framed reply and continue — not inbox park.
    # Interceptor counters / Approach C limits remain SIG-UX-10 NEXT.
    if mode == "auto_framed":
        audit = LlmReplyAudit(
            id=uid(),
            execution_id=run.id,
            node_id=node_id,
            run_id=run.id,
            identity_id=identity.id,
            contact_id=contact.id,
            connection_id=identity.connection_id,
            project_id=project_id,
            provider=provider,
            model=model or "",
            rules_version=rules_version,
            mode=mode,
            response_status=(
                "ok" if response_status == "ok" and draft_text.strip()
                else (response_status if response_status != "ok" else "fallback")
            ),
            latency_ms=latency_ms,
            error_class=error_class,
            draft_text=(draft_text or "")[:20000],
            created_at=now(),
            updated_at=now(),
        )
        db.add(audit)
        db.flush()
        if isinstance(draft_text, str) and draft_text.strip():
            queue(db, identity, {"type": "text", "text": draft_text.strip()[:4000]}, run)
            event(
                db, "llm_reply.auto_framed", identity.connection_id, contact.id, run.id,
                node=node_id, audit_id=audit.id, mode=mode, rules_version=rules_version,
                response_status=audit.response_status, auto_send=False, framed=True,
            )
            run.state = state
            return False  # fall through to node["next"]
        nxt = node.get("on_out_of_frame") or node.get("next")
        event(
            db, "llm_reply.out_of_frame", identity.connection_id, contact.id, run.id,
            node=node_id, audit_id=audit.id, mode=mode,
        )
        if nxt:
            state["_force_next"] = nxt
            run.state = state
            return False
        run.state = state
        return True

    # Draft-first modes park as pending for inbox; preserve fallback/error labels when used.
    stored_status = response_status if response_status in {"fallback", "error", "timeout"} else "pending"
    audit = LlmReplyAudit(
        id=uid(),
        execution_id=run.id,
        node_id=node_id,
        run_id=run.id,
        identity_id=identity.id,
        contact_id=contact.id,
        connection_id=identity.connection_id,
        project_id=project_id,
        provider=provider,
        model=model or "",
        rules_version=rules_version,
        mode=mode,
        response_status=stored_status,
        latency_ms=latency_ms,
        error_class=error_class,
        draft_text=draft_text[:20000],
        created_at=now(),
        updated_at=now(),
    )
    db.add(audit)
    db.flush()
    state["_llm_draft_id"] = audit.id
    state["_await"] = "llm_approval"
    # Draft-first: never auto-send. Park run until inbox approve/edit/reject.
    run.status = "waiting"
    event(
        db, "llm_reply.draft", identity.connection_id, contact.id, run.id,
        node=node_id, audit_id=audit.id, mode=mode, rules_version=rules_version,
        response_status=audit.response_status, auto_send=False,
    )
    run.state = state
    return True

def advance(db, run, identity):
    graph = db.get(Version, run.version_id).graph
    state = dict(run.state)
    contact = db.get(Contact, identity.contact_id)
    from app.db import Connection
    connection = db.get(Connection, identity.connection_id)
    for _ in range(201):
        if run.status != "active":
            break
        key = run.current
        node = graph["nodes"][key]
        kind = node["type"]
        event(db, "node.entered", identity.connection_id, contact.id, run.id, node=key)
        if kind in {"text", "photo", "video", "video_note", "voice", "document"}:
            # Circle messages have no caption in Telegram: queue explanatory text first.
            if kind == "video_note" and node.get("text"):
                queue(db, identity, {"type": "text", "text": node["text"]}, run)
            if kind == "text":
                body, variant_index = pick_variant_text(
                    node, state, version_id=run.version_id, node_key=key
                )
                payload = {**node, "text": body}
                payload.pop("variants", None)
                if variant_index is not None:
                    state["last_variant_index"] = variant_index
                    payload["variant_index"] = variant_index
                queue(db, identity, payload, run)
            else:
                queue(db, identity, node, run)
        elif kind == "question":
            prompt(db, run, identity, node, state)
            run.status = "waiting"
            break
        elif kind == "survey":
            state["_survey_index"], state["_survey_answers"] = 0, {}
            prompt(db, run, identity, node["questions"][0], state)
            run.status = "waiting"
            break
        elif kind == "condition":
            run.current = node["yes"] if state.get(node["key"]) == node.get("equals", True) else node["no"]
            continue
        elif kind == "intent_router":
            # Only fresh inbound — consume so a later router does not reuse it.
            inbound = state.get("_last_inbound") or ""
            if not str(inbound).strip():
                state["_await"] = "intent_router"
                run.status = "waiting"
                run.state = state
                break
            state.pop("_last_inbound", None)
            matched = match_intent_router(node, str(inbound))
            nxt = resolve_intent_router_next(node, matched)
            event(
                db, "intent_router.matched", identity.connection_id, contact.id, run.id,
                node=key, matched=matched, next=nxt, input=str(inbound)[:200],
            )
            if not nxt:
                run.status = "completed"
                break
            run.current = nxt
            continue
        elif kind == "wait":
            # Delay starts after preceding messages are sent, not when they enter outbox.
            run.due_at = None
            run.status = "delayed"
            break
        elif kind == "handoff":
            # Switch run delivery to another bot Identity for the same Contact (channel → DM).
            target_cid = node["connection_id"]
            target = db.scalar(
                select(Identity)
                .where(Identity.contact_id == contact.id, Identity.connection_id == target_cid)
                .with_for_update()
            )
            if not target:
                # Private chat_id for Telegram/MAX equals user id until the user opens DM.
                target = Identity(
                    id=uid(),
                    contact_id=contact.id,
                    connection_id=target_cid,
                    external_user_id=identity.external_user_id,
                    chat_id=identity.external_user_id,
                    name=identity.name or "",
                )
                db.add(target)
                db.flush()
                event(db, "identity.handoff_created", target_cid, contact.id, run.id, from_connection=identity.connection_id)
            run.identity_id = target.id
            identity = target
            event(db, "run.handoff", target_cid, contact.id, run.id, from_connection=node.get("from") or "")
            run.current = node["next"]
            continue
        elif kind in {"escalate", "operator_pause"}:
            title = node.get("text") or node.get("title") or "Эскалация оператору"
            pause_for_operator(
                db, run, identity, contact,
                reason="escalate",
                task_title=title,
            )
            # Remember next so resume can continue after human reply if desired.
            # Merge onto run.state: pause_for_operator already wrote _before_pause / _pause_reason.
            state = {**dict(run.state), **state, "_escalate_next": node["next"]}
            run.state = state
            break
        elif kind == "llm_reply":
            if _execute_llm_reply(db, run, identity, contact, node, key, state, connection):
                break
            forced = state.pop("_force_next", None)
            if forced:
                run.current = forced
                continue
        elif kind == "grant":
            if not all(state.get(x) is True for x in node["requires"]):
                run.status = "blocked"
                event(db, "grant.blocked", identity.connection_id, contact.id, run.id, node=key)
                break
            existing = db.scalar(select(Entitlement).where(
                Entitlement.contact_id == contact.id, Entitlement.product_key == node["product"]))
            if not existing:
                db.add(Entitlement(id=uid(), contact_id=contact.id, product_key=node["product"]))
            event(db, "product.granted", identity.connection_id, contact.id, run.id, product=node["product"])
        elif kind == "stage":
            contact.stage = node["text"]
        elif kind == "task":
            db.add(Task(id=uid(), contact_id=contact.id, connection_id=identity.connection_id, title=node["text"]))
        elif kind == "goal":
            event(db, "goal.reached", identity.connection_id, contact.id, run.id, goal=node["text"])
        elif kind == "finish":
            run.status = "completed"
            event(db, "funnel.completed", identity.connection_id, contact.id, run.id)
            break
        run.current = node["next"]
    else:
        raise ValueError("graph step budget exceeded")
    run.state = state

def cancel_run(db, run):
    run.status, run.due_at = "stopped", None
    for out in db.scalars(select(Outbox).where(Outbox.run_id == run.id,
        Outbox.status.in_(["pending", "held"])).with_for_update()):
        out.status = "cancelled"

def cancel_delayed_on_reply(db, run, identity, contact, text: str) -> bool:
    """If current wait has cancel_on_reply, cancel timer/pending delayed outbox and branch."""
    if not run or run.status != "delayed":
        return False
    version = db.get(Version, run.version_id)
    node = version.graph["nodes"].get(run.current) or {}
    if node.get("type") != "wait" or not node.get("cancel_on_reply"):
        return False
    # Cancel undelivered automation tied to this delayed wait.
    wait_node = run.current
    for out in db.scalars(select(Outbox).where(
        Outbox.run_id == run.id, Outbox.status.in_(["pending", "held"])
    ).with_for_update()):
        out.status = "cancelled"
    run.due_at = None
    nxt = node.get("on_reply") or node.get("next")
    state = dict(run.state)
    state["_last_inbound"] = text
    state.pop("_await", None)
    run.state, run.current, run.status = state, nxt, "active"
    event(
        db, "wait.cancel_on_reply", identity.connection_id, contact.id, run.id,
        node=wait_node, text=text[:500], next=nxt,
    )
    advance(db, run, identity)
    return True

def media_import(db, connection, msg):
    admin = db.get(Admin, int(msg["user_id"]))
    if not admin or not admin.active or admin.role not in {"owner", "editor"}:
        return False
    if admin.role != "owner" and connection.id not in admin.connection_ids:
        return False
    media = msg["media"]
    asset = Asset(id=uid(), name=(msg.get("text") or media["kind"])[:160],
        kind=media["kind"], mime=media.get("mime", ""), connection_ids=[connection.id],
        telegram_refs={connection.id: media["file_id"]}, metadata_json=media.get("meta", {}))
    db.add(asset)
    db.flush()
    identity = db.scalar(select(Identity).where(Identity.connection_id == connection.id,
        Identity.external_user_id == msg["user_id"]))
    if not identity:
        contact = Contact(id=uid(), stage="Администратор")
        db.add(contact); db.flush()
        identity = Identity(id=uid(), contact_id=contact.id, connection_id=connection.id,
            external_user_id=msg["user_id"], chat_id=msg["chat_id"], name=msg.get("name", ""))
        db.add(identity); db.flush()
    queue(db, identity, {"type": "text", "text": "Материал сохранён в медиабанке: " + asset.name})
    event(db, "asset.imported", connection.id, actor_id=admin.telegram_id, asset_id=asset.id)
    return True

def consume(db, connection, msg):
    if msg.get("media"):
        media_import(db, connection, msg)
        return
    identity = db.scalar(select(Identity).where(Identity.connection_id == connection.id,
        Identity.external_user_id == msg["user_id"]).with_for_update())
    if not identity:
        contact = Contact(id=uid(), stage="Новый")
        db.add(contact); db.flush()
        identity = Identity(id=uid(), contact_id=contact.id, connection_id=connection.id,
            external_user_id=msg["user_id"], chat_id=msg["chat_id"], name=msg.get("name", ""))
        db.add(identity); db.flush()
        event(db, "contact.created", connection.id, contact.id)
    elif msg.get("name"):
        identity.name = msg["name"][:200]
    if connection.project_id:
        from app.crm_service import ensure_profile
        ensure_profile(db, identity, connection.project_id)
    run = db.scalar(select(Run).where(Run.identity_id == identity.id,
        Run.status.in_(LIVE)).with_for_update())
    text, callback = msg.get("text", ""), msg.get("callback", "")
    if text.strip().lower() in {"/stop", "стоп"}:
        if connection.project_id:
            from app.crm_service import ensure_profile
            ensure_profile(db, identity, connection.project_id).stopped = True
        # Also cancel undelivered messages of a completed run.
        for previous in db.scalars(select(Run).where(Run.identity_id == identity.id)):
            if previous.status in LIVE or previous.status == "completed":
                cancel_run(db, previous)
        event(db, "contact.stopped", connection.id, identity.contact_id)
        return
    if callback.startswith("e:"):
        msg = {**msg, "start": True, "payload": callback[2:]}
    if msg.get("start"):
        if connection.project_id:
            from app.outreach import mark_started_for_identity
            mark_started_for_identity(db, identity, connection)
        if run:
            return
        entry = None
        if msg.get("channel_join"):
            # Prefer Entry tagged for channel membership (wizard sets attribution.trigger).
            for candidate in db.scalars(select(Entry).where(Entry.connection_id == connection.id, Entry.active == True)):
                if (candidate.attribution or {}).get("trigger") == "channel_join":
                    entry = candidate
                    break
            if not entry:
                token = connection.config.get("channel_join_entry") or connection.config.get("default_entry")
                entry = db.get(Entry, token) if token else None
        else:
            token = msg.get("payload") or connection.config.get("default_entry")
            entry = db.get(Entry, token) if token else None
        if not entry or not entry.active or entry.connection_id != connection.id:
            if msg.get("channel_join"):
                event(db, "channel_join.no_entry", connection.id, identity.contact_id)
                return
            entries = db.scalars(select(Entry).where(Entry.connection_id == connection.id, Entry.active == True)).all()
            choices = [{"text": db.get(Funnel, e.funnel_id).name[:100], "data": "e:" + e.token}
                       for e in entries[:20] if db.get(Funnel, e.funnel_id).published_version_id]
            welcome = connection.config.get("welcome_text", "Выберите программу")
            queue(db, identity, {"type": "text", "text": welcome, "choices": choices})
            return
        funnel = db.get(Funnel, entry.funnel_id)
        if not funnel.published_version_id or connection.id not in funnel.connection_ids:
            return
        version = db.get(Version, funnel.published_version_id)
        run = Run(id=uid(), identity_id=identity.id, version_id=version.id,
            entry_token=entry.token, current=version.graph["start"], status="active", state={})
        db.add(run); db.flush()
        event(
            db,
            "funnel.started",
            connection.id,
            identity.contact_id,
            run.id,
            attribution=entry.attribution,
            channel_join=bool(msg.get("channel_join")),
        )
        advance(db, run, identity)
        return
    contact = db.get(Contact, identity.contact_id)
    if run and run.status == "delayed" and text.strip() and not callback:
        if cancel_delayed_on_reply(db, run, identity, contact, text):
            return
    if run and run.status == "waiting" and (run.state or {}).get("_await") == "llm_approval":
        # Customer wrote while draft awaits operator — log only; do not treat as question answer.
        state = dict(run.state)
        state["_last_inbound"] = text
        run.state = state
        event(db, "message.received", connection.id, identity.contact_id, run.id, text=text)
        return
    if run and run.status == "waiting" and (run.state or {}).get("_await") == "intent_router":
        if not text.strip():
            return
        node = db.get(Version, run.version_id).graph["nodes"][run.current]
        if node.get("type") != "intent_router":
            event(db, "message.received", connection.id, identity.contact_id, run.id, text=text)
            return
        state = dict(run.state)
        state["_last_inbound"] = text
        state.pop("_await", None)
        event(db, "message.received", connection.id, identity.contact_id, run.id, text=text)
        # Re-enter advance on the same router node so inbound is consumed once there.
        run.state, run.status = state, "active"
        advance(db, run, identity)
        return
    if not run or run.status != "waiting":
        event(db, "message.received", connection.id, identity.contact_id, run.id if run else None, text=text)
        return
    node = db.get(Version, run.version_id).graph["nodes"][run.current]
    state = dict(run.state)
    question = node if node["type"] == "question" else node["questions"][state["_survey_index"]]
    if callback:
        parts = callback.split(":")
        if len(parts) != 3 or parts[0] != "a" or parts[1] != state.get("_prompt"):
            event(db, "callback.stale", connection.id, identity.contact_id, run.id)
            return
        try:
            index = int(parts[2])
            if index < 0:
                return
            text = question.get("options", [])[index]
        except (ValueError, IndexError):
            return
    outstanding = db.scalar(select(Outbox).where(Outbox.run_id == run.id,
        Outbox.status.notin_(["sent", "cancelled"])))
    if outstanding:
        raise AwaitDelivery()
    if not text.strip():
        return
    event(db, "message.received", connection.id, identity.contact_id, run.id, text=text)
    options = question.get("options", [])
    valid = not options or text in options
    answer_type = question.get("answer_type", "text")
    if answer_type == "email":
        valid = bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text))
    if answer_type == "phone":
        valid = bool(re.fullmatch(r"\+?[\d ()-]{7,25}", text))
    if not valid:
        prompt(db, run, identity, question, state)
        run.state = state
        return
    if node["type"] == "question":
        state[node["key"]] = text
        event(db, "question.answered", connection.id, identity.contact_id, run.id, key=node["key"], answer=text)
    else:
        answers = dict(state["_survey_answers"])
        answers[question["key"]] = text
        state["_survey_answers"] = answers
        index = state["_survey_index"] + 1
        if index < len(node["questions"]):
            state["_survey_index"] = index
            prompt(db, run, identity, node["questions"][index], state)
            run.state = state
            return
        state[node["key"]], state[node["key"] + "_answers"] = True, answers
        event(db, "survey.completed", connection.id, identity.contact_id, run.id, answers=answers)
    state.pop("_prompt", None)
    state["_last_inbound"] = text
    nxt = node["next"]
    if node["type"] == "question":
        nxt = (node.get("routes") or {}).get(text) or nxt
    run.state, run.current, run.status = state, nxt, "active"
    advance(db, run, identity)
