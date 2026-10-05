from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

if TYPE_CHECKING:
    from collections.abc import Mapping

_INIT_DATA_MAX_AGE = 86400

DEFAULT_TOKEN_TTL = 3600

_TOKEN_NAMESPACE = b"ccgram-miniapp/v1"


class InvalidTokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class TokenPayload:
    window_id: str
    user_id: int
    exp: int

    def is_expired(self, *, now: float | None = None) -> bool:
        ts = time.time() if now is None else now
        return ts >= self.exp


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(token: str) -> bytes:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad)


def _signing_key(bot_token: str) -> bytes:
    if not bot_token:
        raise InvalidTokenError("bot_token is empty")
    return hmac.new(
        _TOKEN_NAMESPACE, bot_token.encode("utf-8"), hashlib.sha256
    ).digest()


def sign_token(
    *,
    bot_token: str,
    window_id: str,
    user_id: int,
    ttl: int = DEFAULT_TOKEN_TTL,
    now: float | None = None,
) -> str:
    issued = int(time.time() if now is None else now)
    payload = {"w": window_id, "u": int(user_id), "exp": issued + int(ttl)}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(_signing_key(bot_token), body, hashlib.sha256).digest()
    return f"{_b64url_encode(body)}.{_b64url_encode(sig)}"


def verify_token(
    token: str,
    *,
    bot_token: str,
    now: float | None = None,
) -> TokenPayload:
    if not token or token.count(".") != 1:
        raise InvalidTokenError("malformed token")
    body_b64, sig_b64 = token.split(".", 1)
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise InvalidTokenError("base64 decode failed") from exc

    expected = hmac.new(_signing_key(bot_token), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise InvalidTokenError("signature mismatch")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidTokenError("payload not JSON") from exc

    try:
        result = TokenPayload(
            window_id=str(payload["w"]),
            user_id=int(payload["u"]),
            exp=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError("payload missing fields") from exc

    if result.is_expired(now=now):
        raise InvalidTokenError("token expired")
    return result


def _data_check_string(params: Mapping[str, str]) -> str:
    items = sorted((k, v) for k, v in params.items() if k != "hash")
    return "\n".join(f"{k}={v}" for k, v in items)


def validate_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age: int = _INIT_DATA_MAX_AGE,
    now: float | None = None,
) -> dict[str, str]:
    if not init_data:
        raise InvalidTokenError("empty initData")
    params = dict(parse_qsl(init_data, keep_blank_values=True))
    given_hash = params.get("hash")
    if not given_hash:
        raise InvalidTokenError("initData missing hash")

    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(
        secret, _data_check_string(params).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, given_hash):
        raise InvalidTokenError("initData signature mismatch")

    auth_date = params.get("auth_date", "")
    try:
        auth_ts = int(auth_date)
    except ValueError as exc:
        raise InvalidTokenError("auth_date not numeric") from exc

    current = time.time() if now is None else now
    if current - auth_ts > max_age:
        raise InvalidTokenError("initData stale")

    return params


def init_data_user_id(params: Mapping[str, str]) -> int:
    raw = params.get("user")
    if not raw:
        raise InvalidTokenError("initData missing user")
    try:
        user = json.loads(raw)
        return int(user["id"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidTokenError("initData user malformed") from exc


def authorize_api_request(
    *,
    bot_token: str,
    token: str,
    init_data: str | None,
    now: float | None = None,
) -> TokenPayload:
    payload = verify_token(token, bot_token=bot_token, now=now)
    if not init_data:
        raise InvalidTokenError("missing initData")
    params = validate_init_data(init_data, bot_token=bot_token, now=now)
    if init_data_user_id(params) != payload.user_id:
        raise InvalidTokenError("initData user mismatch")
    return payload
