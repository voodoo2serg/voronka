"""Isolated warm-up transport. Not a peer of channels.py Bot API / Campaign Outbox."""
import os
from typing import Protocol


class OutreachTransport(Protocol):
    def send_invite(self, phone: str, text: str) -> dict:
        """Attempt an unofficial first contact. Must never create bot Identity."""


class DryRunTransport:
    """Default path: generate copy only; no network, no unofficial API."""

    def send_invite(self, phone: str, text: str) -> dict:
        return {
            "status": "dry_run",
            "sent": False,
            "phone": phone,
            "text_length": len(text or ""),
        }


class PyMaxTransport:
    """Optional stub behind OUTREACH_MAX_ENABLED. Live credentials are not required to merge."""

    def send_invite(self, phone: str, text: str) -> dict:
        if os.getenv("OUTREACH_MAX_ENABLED", "0") != "1":
            raise RuntimeError("pymax_disabled")
        # Live PyMax wiring is intentionally deferred; refuse rather than pretend success.
        raise RuntimeError("pymax_not_configured")


def get_transport(mode: str, transport: str) -> OutreachTransport:
    if mode == "dry_run" or transport == "manual":
        return DryRunTransport()
    if transport == "pymax":
        return PyMaxTransport()
    return DryRunTransport()
