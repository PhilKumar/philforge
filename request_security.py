"""Request identity helpers for deployments behind a trusted loopback proxy."""

from __future__ import annotations

from ipaddress import ip_address


def _normalise_ip(value: object) -> str:
    try:
        return str(ip_address(str(value or "").strip()))
    except ValueError:
        return ""


def client_ip(peer_host: object, headers: object) -> str:
    """Trust proxy headers only when the direct connection is from loopback."""
    peer = _normalise_ip(peer_host)
    if peer in {"127.0.0.1", "::1"}:
        get_header = getattr(headers, "get", lambda _name, _default="": _default)
        forwarded = str(get_header("x-real-ip", "") or "").strip()
        if not forwarded:
            forwarded = str(get_header("x-forwarded-for", "") or "").split(",", 1)[0].strip()
        trusted = _normalise_ip(forwarded)
        if trusted:
            return trusted
    return peer or "unknown"


def request_client_ip(request: object) -> str:
    client = getattr(request, "client", None)
    return client_ip(getattr(client, "host", ""), getattr(request, "headers", {}))


def request_rate_subject(request: object) -> str:
    state = getattr(request, "state", None)
    user_id = getattr(state, "user_id", 0) if state is not None else 0
    return f"user:{int(user_id or 0)}:ip:{request_client_ip(request)}"
