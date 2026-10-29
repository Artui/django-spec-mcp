from __future__ import annotations

from django.http import HttpRequest


class User:
    def __init__(self, uid: int) -> None:
        self.id = uid


def http_request(remote_addr: str | None = "203.0.113.7") -> HttpRequest:
    req = HttpRequest()
    if remote_addr is not None:
        req.META["REMOTE_ADDR"] = remote_addr
    return req
