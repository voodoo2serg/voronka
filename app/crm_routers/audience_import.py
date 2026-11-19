"""IMPORT-101 admin/ops API: CSV audience → Person + ContactPoint + Consent."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app import audience_import as ai
from app.crm_deps import Session, crm_actor, must
from app.crm_models import ImportBatch
from app.crm_service import audit, project_access
from app.db import Project

router = APIRouter()

ConsentMode = Literal["quarantine", "grant_marketing"]


def _parse_mapping(mapping: str) -> dict:
    try:
        fields = json.loads(mapping or "{}")
        if not isinstance(fields, dict):
            raise ValueError()
        # Strip internal keys if client echoes them.
        return {k: v for k, v in fields.items() if not str(k).startswith("_")}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Сопоставление должно быть JSON-объектом") from exc


@router.post("/projects/{pid}/audience-imports/dry-run")
async def audience_import_dry_run(
    pid: str,
    file: UploadFile = File(...),
    mapping: str = Form("{}"),
    consent_mode: ConsentMode = Form("quarantine"),
    consent_evidence: str = Form(""),
    admin=Depends(crm_actor),
):
    """Dry-run: parse CSV and report counts without persisting Person/Consent."""
    raw = await file.read(5_000_001)
    fields = _parse_mapping(mapping)
    headers, fields, rows = ai.parse_audience_csv(raw, fields)
    fingerprint = ai.audience_fingerprint(raw, fields, consent_mode, consent_evidence)
    with Session.begin() as db:
        project_access(db, admin, pid)
        batch = ai.get_or_create_audience_batch(
            db,
            project_id=pid,
            fingerprint=fingerprint,
            mapping=fields,
            rows=rows,
            actor_id=admin.telegram_id,
            consent_mode=consent_mode,
            evidence=consent_evidence,
        )
        result = ai.plan_or_apply_audience_rows(
            db,
            project_id=pid,
            rows=rows,
            consent_mode=consent_mode,
            evidence=consent_evidence,
            batch_id=batch.id,
            actor_id=admin.telegram_id,
            dry_run=True,
            source_label=file.filename or "audience.csv",
        )
        audit(
            db,
            admin,
            "audience_import.dry_run",
            batch_id=batch.id,
            project_id=pid,
            fingerprint=fingerprint,
            counts=result["counts"],
            consent_mode=consent_mode,
        )
        return {
            "id": batch.id,
            "state": batch.state,
            "headers": headers,
            "mapping": fields,
            "fingerprint": fingerprint,
            "consent_mode": consent_mode,
            **result,
        }


@router.post("/projects/{pid}/audience-imports")
async def audience_import_commit(
    pid: str,
    file: UploadFile = File(...),
    mapping: str = Form("{}"),
    consent_mode: ConsentMode = Form("quarantine"),
    consent_evidence: str = Form(""),
    dry_run: bool = Form(False),
    admin=Depends(crm_actor),
):
    """Commit (or dry-run) audience CSV into Person + ContactPoint + Consent."""
    if dry_run:
        return await audience_import_dry_run(
            pid,
            file=file,
            mapping=mapping,
            consent_mode=consent_mode,
            consent_evidence=consent_evidence,
            admin=admin,
        )
    raw = await file.read(5_000_001)
    fields = _parse_mapping(mapping)
    headers, fields, rows = ai.parse_audience_csv(raw, fields)
    fingerprint = ai.audience_fingerprint(raw, fields, consent_mode, consent_evidence)
    with Session.begin() as db:
        project_access(db, admin, pid)
        db.scalar(select(Project).where(Project.id == pid).with_for_update())
        batch = ai.get_or_create_audience_batch(
            db,
            project_id=pid,
            fingerprint=fingerprint,
            mapping=fields,
            rows=rows,
            actor_id=admin.telegram_id,
            consent_mode=consent_mode,
            evidence=consent_evidence,
        )
        if batch.state == "done" and batch.report:
            return {
                "id": batch.id,
                "state": batch.state,
                "headers": headers,
                "mapping": {k: v for k, v in (batch.mapping or {}).items() if not str(k).startswith("_")},
                "fingerprint": fingerprint,
                "replay": True,
                **batch.report,
            }
        result = ai.plan_or_apply_audience_rows(
            db,
            project_id=pid,
            rows=batch.rows or rows,
            consent_mode=consent_mode,
            evidence=consent_evidence,
            batch_id=batch.id,
            actor_id=admin.telegram_id,
            dry_run=False,
            source_label=file.filename or "audience.csv",
        )
        batch.state = "done"
        batch.report = result
        batch.rows = []  # drop CSV payload; fingerprint keeps replay
        audit(
            db,
            admin,
            "audience_import.completed",
            batch_id=batch.id,
            project_id=pid,
            fingerprint=fingerprint,
            counts=result["counts"],
            consent_mode=consent_mode,
            # evidence string only — never raw emails/telegrams in audit event beyond counts
            evidence=consent_evidence[:200] if consent_evidence else "",
        )
        return {
            "id": batch.id,
            "state": batch.state,
            "headers": headers,
            "mapping": fields,
            "fingerprint": fingerprint,
            "replay": False,
            **result,
        }


@router.post("/audience-imports/{bid}/commit")
def audience_import_commit_batch(
    bid: str,
    consent_mode: ConsentMode | None = None,
    consent_evidence: str | None = None,
    admin=Depends(crm_actor),
):
    """Commit a previously dry-run / previewed audience ImportBatch."""
    with Session.begin() as db:
        batch = must(db.scalar(select(ImportBatch).where(ImportBatch.id == bid).with_for_update()))
        project_access(db, admin, batch.project_id)
        kind = (batch.mapping or {}).get("_kind")
        if kind != "audience":
            raise HTTPException(422, "Партия не является audience-import")
        if batch.state == "done":
            return {"id": batch.id, "state": batch.state, "replay": True, **(batch.report or {})}
        db.scalar(select(Project).where(Project.id == batch.project_id).with_for_update())
        mode = consent_mode or (batch.mapping or {}).get("_consent_mode") or "quarantine"
        evidence = (
            consent_evidence
            if consent_evidence is not None
            else (batch.mapping or {}).get("_evidence") or ""
        )
        if mode == "grant_marketing" and not str(evidence).strip():
            raise HTTPException(422, "Для grant_marketing нужна evidence")
        result = ai.plan_or_apply_audience_rows(
            db,
            project_id=batch.project_id,
            rows=batch.rows or [],
            consent_mode=mode,
            evidence=str(evidence),
            batch_id=batch.id,
            actor_id=admin.telegram_id,
            dry_run=False,
            source_label="audience_batch",
        )
        batch.state = "done"
        batch.report = result
        batch.rows = []
        audit(
            db,
            admin,
            "audience_import.completed",
            batch_id=batch.id,
            project_id=batch.project_id,
            counts=result["counts"],
            consent_mode=mode,
            evidence=str(evidence)[:200],
        )
        return {"id": batch.id, "state": batch.state, "replay": False, **result}
