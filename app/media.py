import json, os, shutil, subprocess
from pathlib import Path
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy import select
from app.auth import actor, permit, allowed
from app.db import Session, Asset, Connection, Funnel, Version
from app.runtime import event

router = APIRouter(prefix="/api/assets")
ROOT = Path(os.getenv("MEDIA_ROOT", "/data/media"))
LIMIT = 50_000_000
# Leave room for Postgres and neighbouring services on a shared disk.
MIN_FREE = 5 * 1024 ** 3

def local_file(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise HTTPException(404, "Файл не найден")
    return path

def inspect_file(path, kind):
    prefix = path.read_bytes()[:16] if path.stat().st_size < 32 else b""
    with path.open("rb") as stream:
        prefix = stream.read(16)
    if kind == "document":
        if not prefix.startswith(b"%PDF-"):
            raise ValueError("Документ должен быть PDF")
        return "application/pdf", {}
    if kind == "photo":
        if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", {}
        if prefix.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", {}
        raise ValueError("Фото: JPEG или PNG")
    if kind not in {"video", "video_note", "voice"}:
        raise ValueError("Неверный тип материала")
    if kind in {"video", "video_note"} and prefix[4:8] != b"ftyp":
        raise ValueError("Видео должно быть MP4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
    info = json.loads(probe.stdout)
    streams = info.get("streams", [])
    duration = float(info.get("format", {}).get("duration", 0))
    if kind in {"video", "video_note"}:
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not video or video.get("codec_name") != "h264" or duration <= 0:
            raise ValueError("Нужен MP4 с видео H.264")
        metadata = {"duration": duration, "width": video["width"], "height": video["height"]}
        if kind == "video_note" and (video["width"] != video["height"] or duration > 60):
            raise ValueError("Кружок: квадратное видео длительностью до 60 секунд")
        return "video/mp4", metadata
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not audio or audio.get("codec_name") not in {"opus", "mp3", "aac"} or duration <= 0:
        raise ValueError("Голос: OGG/Opus, MP3 или M4A/AAC")
    mime = "audio/ogg" if prefix.startswith(b"OggS") else "audio/mpeg" if audio["codec_name"] == "mp3" else "audio/mp4"
    return mime, {"duration": duration}

def asset_view(asset):
    return {"id": asset.id, "name": asset.name, "kind": asset.kind, "mime": asset.mime,
        "connection_ids": asset.connection_ids, "metadata": asset.metadata_json,
        "preview_available": bool(asset.local_path), "created_at": asset.created_at.isoformat()}

@router.get("")
def assets(admin=Depends(actor)):
    with Session() as db:
        return [asset_view(a) for a in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(500))
            if all(allowed(admin, cid) for cid in a.connection_ids)]

@router.post("")
async def upload(name: str = Form(...), kind: str = Form(...), connection_ids: str = Form(...),
                 file: UploadFile = File(...), admin=Depends(actor)):
    try:
        ids = json.loads(connection_ids)
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Выберите подключения")
    permit(admin, ids, edit=True)
    if not name.strip() or len(name) > 160:
        raise HTTPException(422, "Название: 1–160 символов")
    with Session() as db:
        for cid in ids:
            if not db.get(Connection, cid):
                raise HTTPException(404, "Подключение отсутствует")
    ROOT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < MIN_FREE:
        raise HTTPException(507, "Недостаточно места на диске: нужно не меньше 5 ГБ свободно")
    asset_id = str(uuid4())
    # Original filename never controls the destination path.
    extension = {"video":".mp4", "video_note":".mp4", "photo":".img", "voice":".audio", "document":".pdf"}.get(kind)
    if not extension:
        raise HTTPException(422, "Неверный тип")
    path = ROOT / (asset_id + extension)
    size = 0
    try:
        with path.open("xb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > (10_000_000 if kind == "photo" else LIMIT):
                    raise HTTPException(413, "Размер превышает лимит: фото 10 МБ, прочие файлы 50 МБ")
                target.write(chunk)
        if not size:
            raise HTTPException(422, "Пустой файл")
        try:
            mime, metadata = inspect_file(path, kind)
        except (ValueError, subprocess.SubprocessError, OSError) as error:
            raise HTTPException(422, str(error) if isinstance(error, ValueError) else "Файл не удалось проверить")
        # Give multipart uploads an extension corresponding to their real container.
        real_extension = {"image/png":".png", "image/jpeg":".jpg", "audio/ogg":".ogg",
            "audio/mpeg":".mp3", "audio/mp4":".m4a"}.get(mime, extension)
        if path.suffix != real_extension:
            updated = path.with_suffix(real_extension)
            path.rename(updated)
            path = updated
        with Session.begin() as db:
            asset = Asset(id=asset_id, name=name, kind=kind, mime=mime, local_path=path.name,
                connection_ids=ids, telegram_refs={}, metadata_json={**metadata, "size":size})
            db.add(asset); db.flush()
            for cid in ids:
                event(db, "asset.uploaded", cid, actor_id=admin.telegram_id, asset_id=asset.id)
        return asset_view(asset)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

@router.get("/{aid}/file")
def preview(aid: str, admin=Depends(actor)):
    with Session() as db:
        asset = db.get(Asset, aid)
        if not asset:
            raise HTTPException(404)
        permit(admin, asset.connection_ids)
        if not asset.local_path:
            raise HTTPException(409, "Материал хранится в Telegram и доступен для тестовой отправки")
        return FileResponse(local_file(asset.local_path), media_type=asset.mime,
            headers={"Cache-Control":"private, no-store","X-Content-Type-Options":"nosniff"})

@router.delete("/{aid}")
def delete_asset(aid: str, admin=Depends(actor)):
    with Session.begin() as db:
        asset = db.get(Asset, aid)
        if not asset:
            raise HTTPException(404)
        permit(admin, asset.connection_ids, edit=True)
        graphs = [f.draft for f in db.scalars(select(Funnel))]
        graphs += [v.graph for v in db.scalars(select(Version))]
        if any(any(n.get("asset_id") == aid for n in g.get("nodes", {}).values()) for g in graphs):
            raise HTTPException(409, "Материал используется в воронке или её опубликованной версии")
        from app.db import Outbox
        queued = db.scalar(select(Outbox.id).where(Outbox.payload["asset_id"].as_string() == aid,
            Outbox.status.in_(["pending","sending","held","unknown"])).limit(1))
        if queued:
            raise HTTPException(409, "Материал ожидает отправки")
        path = local_file(asset.local_path) if asset.local_path else None
        db.delete(asset)
    if path:
        path.unlink(missing_ok=True)
    return {"ok":True}
