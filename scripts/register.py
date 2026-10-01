"""Configure menus and webhooks without printing tokens."""
import os, sys
import httpx
from app.db import Session, Connection
from app.channels import credential

def main():
    mode = sys.argv[1]
    base = "https://" + os.environ["DOMAIN"]
    try:
        with httpx.Client(timeout=20) as client:
            if mode == "admin":
                token = credential("ADMIN_BOT_TOKEN")
                response = client.post(f"https://api.telegram.org/bot{token}/setChatMenuButton", json={
                    "menu_button": {"type": "web_app", "text": "Воронки", "web_app": {"url": base}}})
                if not response.json().get("ok"):
                    raise ValueError("menu rejected")
                print("Admin menu configured")
                return
            with Session() as db:
                c = db.get(Connection, mode)
                if not c:
                    raise ValueError("connection missing")
                token, secret = credential(c.token_env), credential(c.secret_env)
                url = base + "/hooks/" + c.id
                if c.platform == "telegram":
                    response = client.post(f"https://api.telegram.org/bot{token}/setWebhook",
                        json={"url": url, "secret_token": secret, "allowed_updates": ["message"]})
                    good = response.json().get("ok")
                elif c.platform == "max":
                    response = client.post("https://platform-api2.max.ru/subscriptions",
                        headers={"Authorization": token}, json={
                            "url": url, "secret": secret, "update_types": ["bot_started", "message_created"]})
                    good = response.status_code == 200 and response.json().get("success")
                else:
                    print("Configure VK Callback API in community settings. URL:", url)
                    return
                if not good:
                    raise ValueError("subscription rejected")
                print("Webhook configured")
    except Exception as error:
        print("Registration failed:", type(error).__name__)
        raise SystemExit(1)

if __name__ == "__main__":
    main()
