import hashlib
import hmac
import time
import uuid
import httpx


class MediaRequestAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, base_url: str, service: str, secret: str):
        self.origin = httpx.URL(base_url)
        self.service = service
        self.secret = secret

    def auth_flow(self, request):
        if (request.url.scheme, request.url.host, request.url.port) != (
            self.origin.scheme, self.origin.host, self.origin.port
        ):
            yield request
            return
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        digest = hashlib.sha256(request.content).hexdigest()
        target = request.url.raw_path.decode("ascii")
        message = f"v2\n{self.service}\n{timestamp}\n{nonce}\n{request.method}\n{target}\n{digest}"
        signature = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        request.headers.update({
            "X-Media-Service": self.service,
            "X-Media-Timestamp": timestamp,
            "X-Media-Nonce": nonce,
            "X-Media-Signature": signature,
        })
        yield request
