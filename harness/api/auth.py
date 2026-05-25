from collections import defaultdict
from functools import wraps
from time import time

import jwt
from flask import jsonify, request

from config import settings


def _token_from_request() -> str:
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return request.cookies.get(settings.cookie_name, "")


def require_auth(roles: set[str] | None = None):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            token = _token_from_request()
            try:
                payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            except Exception:
                return jsonify({"error": "unauthorized"}), 401
            if roles and payload.get("role") not in roles:
                return jsonify({"error": "forbidden"}), 403
            resolved_user = payload.get("user_id") or payload.get("sub") or payload.get("username") or payload.get("email")
            if not resolved_user:
                return jsonify({"error": "missing_user_identity"}), 401
            payload["user_id"] = str(resolved_user)
            request.identity = payload
            return fn(*args, **kwargs)

        return inner

    return deco


_hits = defaultdict(list)


def rate_limit(limit: int):
    def deco(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            key = request.remote_addr or "unknown"
            now = time()
            _hits[key] = [t for t in _hits[key] if now - t < 60]
            if len(_hits[key]) >= limit:
                return jsonify({"error": "rate_limited"}), 429
            _hits[key].append(now)
            return fn(*args, **kwargs)

        return inner

    return deco
