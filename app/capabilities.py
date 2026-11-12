"""Honest channel media capability matrix (VOR-10). No fake success for MAX/VK circles."""
from __future__ import annotations

MEDIA_KINDS = ("text", "photo", "video", "video_note", "voice", "document")


def platform_capabilities(platform: str) -> dict:
    platform = (platform or "").lower()
    if platform == "telegram":
        return {
            "platform": "telegram",
            "native": {
                "text": True,
                "photo": True,
                "video": True,
                "video_note": True,
                "voice": True,
                "document": True,
            },
            "video_note": {"mode": "native", "available": True, "note": "Кружки через file_id или локальный MP4"},
            "link_fallback": False,
        }
    if platform == "max":
        return {
            "platform": "max",
            "native": {
                "text": True,
                "photo": False,
                "video": False,
                "video_note": False,
                "voice": False,
                "document": False,
            },
            "video_note": {
                "mode": "unavailable",
                "available": False,
                "note": "Нативные кружки MAX недоступны. Короткая HTTPS-ссылка — только если явно задана в payload.media",
            },
            "link_fallback": True,
        }
    if platform == "vk":
        return {
            "platform": "vk",
            "native": {
                "text": True,
                "photo": False,
                "video": False,
                "video_note": False,
                "voice": False,
                "document": False,
            },
            "video_note": {
                "mode": "unavailable",
                "available": False,
                "note": "Нативные кружки VK недоступны. Ссылка в тексте — явный fallback, не маскируется успехом native",
            },
            "link_fallback": True,
        }
    if platform == "email":
        return {
            "platform": "email",
            "native": {k: k == "text" for k in MEDIA_KINDS},
            "video_note": {"mode": "unavailable", "available": False, "note": "Email только текст"},
            "link_fallback": False,
        }
    return {
        "platform": platform or "unknown",
        "native": {k: False for k in MEDIA_KINDS},
        "video_note": {"mode": "unavailable", "available": False, "note": "Платформа неизвестна"},
        "link_fallback": False,
    }


def assert_sendable(platform: str, kind: str) -> tuple[bool, str]:
    caps = platform_capabilities(platform)
    if kind == "text":
        return True, ""
    if kind == "video_note" and not caps["video_note"]["available"]:
        return False, caps["video_note"]["note"]
    if not caps["native"].get(kind, False) and not (caps.get("link_fallback") and kind in {"photo", "video", "document"}):
        return False, f"{platform}: {kind} недоступен нативно"
    return True, ""
