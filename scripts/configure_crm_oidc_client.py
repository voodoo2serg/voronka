#!/usr/bin/env python3
"""AUTH-203: ensure Keycloak confidential client voronka-crm (dry-run / apply).

Writes client secret only under /root with mode 600 when --apply creates/rotates it.
Never prints the secret. Does not modify the CRM database.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REALM = "solo-pr"
CLIENT_ID = "voronka-crm"
AUTH_PUBLIC_ORIGIN = "https://auth.solo-pr.ru"
CRM_ORIGIN = "https://crm.solo-pr.ru"
REDIRECT = f"{CRM_ORIGIN}/api/crm/oidc/callback"
BASE_CANDIDATES = ("http://127.0.0.1:18081", AUTH_PUBLIC_ORIGIN)


def fail(message: str) -> None:
    raise SystemExit(message)


def read_env(path: str) -> dict[str, str]:
    data: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return data
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def get_admin_credentials() -> tuple[str, str]:
    data: dict[str, str] = {}
    for path in ("/opt/lms_end/.env", "/etc/keycloak.env"):
        data.update(read_env(path))
    data.update({k: v for k, v in os.environ.items() if k.startswith("KEYCLOAK_")})
    user = data.get("KEYCLOAK_ADMIN") or data.get("KC_BOOTSTRAP_ADMIN_USERNAME") or ""
    password = data.get("KEYCLOAK_ADMIN_PASSWORD") or data.get("KC_BOOTSTRAP_ADMIN_PASSWORD") or ""
    if user and password:
        return user, password
    for name in subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True).splitlines():
        if "keycloak" not in name.lower():
            continue
        raw = subprocess.check_output(
            ["docker", "inspect", "--format", "{{json .Config.Env}}", name], text=True
        )
        current = dict(p.split("=", 1) for p in json.loads(raw) if "=" in p)
        user = user or current.get("KEYCLOAK_ADMIN") or current.get("KC_BOOTSTRAP_ADMIN_USERNAME") or ""
        password = (
            password
            or current.get("KEYCLOAK_ADMIN_PASSWORD")
            or current.get("KC_BOOTSTRAP_ADMIN_PASSWORD")
            or ""
        )
        if user and password:
            return user, password
    fail("Keycloak administrator credentials not accessible; no changes made")


def request(base: str, path: str, method: str, token: str = "", data=None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if data is not None:
        if path.endswith("/token"):
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode(data).encode()
        else:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            payload = res.read()
            return res.status, (json.loads(payload) if payload else None)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            response = json.loads(exc.read(8192).decode("utf-8"))
            description = str(response.get("errorMessage") or response.get("error") or "")
            if description:
                detail = " (" + description[:120] + ")"
        except (ValueError, UnicodeError, AttributeError):
            pass
        raise RuntimeError(
            f"Keycloak API rejected {method} {path.split('?')[0]}: HTTP {exc.code}{detail}"
        ) from None


def login() -> tuple[str, str]:
    admin_user, admin_pass = get_admin_credentials()
    for candidate in BASE_CANDIDATES:
        try:
            _, reply = request(
                candidate,
                "/realms/master/protocol/openid-connect/token",
                "POST",
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": admin_user,
                    "password": admin_pass,
                },
            )
            if reply and reply.get("access_token"):
                return candidate, reply["access_token"]
        except (RuntimeError, OSError):
            continue
    fail("Keycloak admin API unavailable; no client changed")


def backup_json(prefix: str, payload) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path("/root") / f"{prefix}-{stamp}.json"
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(payload, out, ensure_ascii=False, indent=2)
    return path


def client_body() -> dict:
    return {
        "clientId": CLIENT_ID,
        "name": "Voronka CRM BFF",
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": None,  # Keycloak generates on create; omitted on update unless rotate
        "standardFlowEnabled": True,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "authorizationServicesEnabled": False,
        "fullScopeAllowed": False,
        "redirectUris": [REDIRECT],
        "webOrigins": [CRM_ORIGIN],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "post.logout.redirect.uris": CRM_ORIGIN + "/*",
        },
    }


def write_secret_file(secret: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path("/root") / f"voronka-crm-oidc-secret-{stamp}.env"
    content = (
        f"KEYCLOAK_ISSUER={AUTH_PUBLIC_ORIGIN}/realms/{REALM}\n"
        f"CRM_PUBLIC_URL={CRM_ORIGIN}\n"
        f"CRM_OIDC_CLIENT_ID={CLIENT_ID}\n"
        f"CRM_OIDC_CLIENT_SECRET={secret}\n"
        f"CRM_OIDC_REDIRECT_URI={REDIRECT}\n"
        f"CRM_OIDC_CUTOVER=1\n"
    )
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(content)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rotate-secret", action="store_true", help="Regenerate client secret (with --apply)")
    args = parser.parse_args()
    if args.rotate_secret and not args.apply:
        fail("--rotate-secret requires --apply")

    base, token = login()
    q = urllib.parse.urlencode({"clientId": CLIENT_ID})
    _, clients = request(base, f"/admin/realms/{REALM}/clients?{q}", "GET", token)
    existing = (clients or [None])[0]
    tags: list[str] = []
    if existing:
        tags.append("CLIENT_EXISTS")
        redirects = set(existing.get("redirectUris") or [])
        if REDIRECT in redirects:
            tags.append("REDIRECT_OK")
        else:
            tags.append("REDIRECT_MISSING")
        if existing.get("publicClient") is True:
            tags.append("PUBLIC_CLIENT_UNEXPECTED")
        else:
            tags.append("CONFIDENTIAL_OK")
    else:
        tags.append("CLIENT_MISSING")

    print("inventory:", ", ".join(tags))
    if not args.apply:
        print("dry_run_ok: pass --apply to create/update client")
        return

    backup = backup_json("keycloak-voronka-crm-before", existing or {"clientId": CLIENT_ID, "missing": True})
    print(f"backup={backup}")

    if not existing:
        body = client_body()
        del body["secret"]  # let Keycloak generate
        request(base, f"/admin/realms/{REALM}/clients", "POST", token, body)
        _, clients = request(base, f"/admin/realms/{REALM}/clients?{q}", "GET", token)
        existing = clients[0]
        tags.append("CLIENT_CREATED")
    else:
        body = existing.copy()
        body.update({
            "enabled": True,
            "publicClient": False,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False,
            "redirectUris": sorted(set((body.get("redirectUris") or []) + [REDIRECT])),
            "webOrigins": sorted(set((body.get("webOrigins") or []) + [CRM_ORIGIN])),
        })
        attrs = dict(body.get("attributes") or {})
        attrs["pkce.code.challenge.method"] = "S256"
        body["attributes"] = attrs
        # Never send secret in PUT unless rotating via dedicated endpoint.
        body.pop("secret", None)
        request(base, f"/admin/realms/{REALM}/clients/{existing['id']}", "PUT", token, body)
        tags.append("CLIENT_UPDATED")

    _, secret_payload = request(
        base, f"/admin/realms/{REALM}/clients/{existing['id']}/client-secret", "GET", token
    )
    if args.rotate_secret or not (secret_payload or {}).get("value"):
        _, secret_payload = request(
            base, f"/admin/realms/{REALM}/clients/{existing['id']}/client-secret", "POST", token
        )
        tags.append("SECRET_ROTATED")
    secret = (secret_payload or {}).get("value") or ""
    if not secret:
        fail("client secret unavailable after apply")
    secret_path = write_secret_file(secret)
    print("apply_ok:", ", ".join(tags))
    print(f"secret_env_file={secret_path} (mode 600; merge into /opt/voronka/.env manually)")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        fail(str(exc))
