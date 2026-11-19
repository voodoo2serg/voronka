"""IMPORT-101: audience CSV → Person + ContactPoint + Consent + send_allowed gate."""
from __future__ import annotations

from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app.crm_models import Consent, ContactPoint, ImportBatch, Person, Profile
from app import person_graph as graph


def _csv(*lines: str, encoding: str = "utf-8") -> bytes:
    return ("\n".join(lines) + "\n").encode(encoding)


def test_audience_dry_run_counts_without_persist(crm):
    raw = _csv("email;telegram_id;name", "a@example.ru;1001;Анна", "b@example.ru;1002;Борис")
    r = crm.client.post(
        "/api/crm/projects/p/audience-imports/dry-run",
        data={"consent_mode": "quarantine"},
        files={"file": ("aud.csv", raw, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["counts"]["total"] == 2
    assert body["counts"]["created_persons"] == 2
    assert body["counts"]["quarantined"] >= 2
    assert body["counts"]["consent_granted"] == 0
    with crm.db() as db:
        assert list(db.scalars(select(Person))) == []
        assert list(db.scalars(select(ContactPoint))) == []
        batch = db.get(ImportBatch, body["id"])
        assert batch is not None and batch.state == "preview"
        assert (batch.mapping or {}).get("_kind") == "audience"


def test_quarantine_blocks_marketing_send_allowed(crm):
    raw = _csv("email;name", "quarantine@example.ru;Q")
    dry = crm.client.post(
        "/api/crm/projects/p/audience-imports/dry-run",
        data={"consent_mode": "quarantine"},
        files={"file": ("q.csv", raw, "text/csv")},
    ).json()
    commit = crm.client.post(f"/api/crm/audience-imports/{dry['id']}/commit")
    assert commit.status_code == 200, commit.text
    report = commit.json()
    assert report["counts"]["created_persons"] == 1
    assert report["counts"]["consent_granted"] == 0
    assert report["counts"]["quarantined"] >= 1
    sample = report["samples"][0]
    assert sample["marketing_send_allowed"] is False
    assert sample["marketing_reason"] == "consent_missing"
    with crm.db() as db:
        person = db.get(Person, sample["person_id"])
        assert person is not None
        cps = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == person.id)))
        assert any(cp.channel == "email" for cp in cps)
        email_cp = next(cp for cp in cps if cp.channel == "email")
        consent = db.scalar(
            select(Consent).where(Consent.contact_point_id == email_cp.id, Consent.purpose == "marketing")
        )
        assert consent is not None and consent.status == "unknown"
        allowed, reason = graph.send_allowed(
            db,
            person_id=person.id,
            channel="email",
            purpose="marketing",
            missing_policy="deny",
        )
        assert allowed is False
        assert reason == "consent_missing"
        # Replay commit is idempotent
    again = crm.client.post(f"/api/crm/audience-imports/{dry['id']}/commit")
    assert again.status_code == 200 and again.json().get("replay") is True
    with crm.db() as db:
        assert len(list(db.scalars(select(Person)))) == 1


def test_grant_marketing_requires_evidence_and_allows_send(crm):
    raw = _csv("email;telegram_id", "ok@example.ru;555")
    blocked = crm.client.post(
        "/api/crm/projects/p/audience-imports",
        data={"consent_mode": "grant_marketing", "consent_evidence": ""},
        files={"file": ("g.csv", raw, "text/csv")},
    )
    assert blocked.status_code == 422

    r = crm.client.post(
        "/api/crm/projects/p/audience-imports",
        data={
            "consent_mode": "grant_marketing",
            "consent_evidence": "ops confirmed list 2026-11-19; source=partner_export",
        },
        files={"file": ("g.csv", raw, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["consent_granted"] >= 1
    assert body["samples"][0]["marketing_send_allowed"] is True
    with crm.db() as db:
        person_id = body["samples"][0]["person_id"]
        allowed, reason = graph.send_allowed(
            db,
            person_id=person_id,
            channel="email",
            purpose="marketing",
            missing_policy="deny",
        )
        assert allowed is True, reason
        tg_allowed, tg_reason = graph.send_allowed(
            db,
            person_id=person_id,
            channel="telegram",
            purpose="marketing",
            missing_policy="deny",
        )
        assert tg_allowed is True, tg_reason
        cps = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == person_id)))
        assert all(cp.verified for cp in cps)


def test_audience_import_idempotent_on_natural_keys(crm):
    raw = _csv(
        "email;telegram_username;name",
        "same@example.ru;@SameUser;Первый",
        "same@example.ru;sameuser;Повтор",
    )
    r = crm.client.post(
        "/api/crm/projects/p/audience-imports",
        data={"consent_mode": "quarantine", "consent_evidence": "n/a"},
        files={"file": ("id.csv", raw, "text/csv")},
    )
    assert r.status_code == 200, r.text
    # Second row matches first by email → one person
    assert r.json()["counts"]["created_persons"] == 1
    assert r.json()["counts"]["updated_persons"] == 1
    with crm.db() as db:
        persons = list(db.scalars(select(Person)))
        assert len(persons) == 1
        profiles = list(db.scalars(select(Profile).where(Profile.project_id == "p")))
        assert len(profiles) == 1
        assert profiles[0].email == "same@example.ru"

    # Re-upload same file → fingerprint replay, no new persons
    r2 = crm.client.post(
        "/api/crm/projects/p/audience-imports",
        data={"consent_mode": "quarantine", "consent_evidence": "n/a"},
        files={"file": ("id.csv", raw, "text/csv")},
    )
    assert r2.status_code == 200 and r2.json().get("replay") is True
    with crm.db() as db:
        assert len(list(db.scalars(select(Person)))) == 1


def test_telegram_only_audience_row(crm):
    raw = _csv("telegram_id;telegram_username", "9001;@tgonly")
    r = crm.client.post(
        "/api/crm/projects/p/audience-imports",
        data={
            "consent_mode": "grant_marketing",
            "consent_evidence": "telegram list confirmed",
        },
        files={"file": ("tg.csv", raw, "text/csv")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["created_persons"] == 1
    with crm.db() as db:
        cps = list(db.scalars(select(ContactPoint)))
        values = {cp.normalized_value for cp in cps}
        assert "9001" in values and "tgonly" in values
        for cp in cps:
            allowed, reason = graph.send_allowed(
                db,
                contact_point_id=cp.id,
                channel="telegram",
                purpose="marketing",
                missing_policy="deny",
            )
            assert allowed is True, reason
