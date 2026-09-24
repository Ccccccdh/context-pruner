"""Language-neutral JSON/HTTP sidecar for Context-Pruner.

The sidecar deliberately exposes lifecycle hooks rather than pretending to be
an OpenAI-compatible model proxy.  Hosts keep their authoritative history and
send only the messages/events they want Context-Pruner to manage.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from . import __version__
from .middleware import ContextPrunerMiddleware
from .plugin import ContextPluginConfig
from .types import ContextBudget


SIDECAR_SCHEMA_VERSION = 1
_SESSION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CONFIG_KEYS = {
    "enabled",
    "method",
    "budget",
    "recovery_limit",
    "recovery_ttl_events",
    "capture_checkpoints",
}


class SidecarRequestError(ValueError):
    """A stable client-visible request failure."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)


@dataclass(frozen=True)
class SidecarServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    auth_token: str = ""
    max_request_bytes: int = 1_048_576
    max_sessions: int = 128
    default_plugin: ContextPluginConfig = field(default_factory=ContextPluginConfig)

    def __post_init__(self) -> None:
        if not 0 <= int(self.port) <= 65_535:
            raise ValueError("port must be between 0 and 65535")
        if int(self.max_request_bytes) <= 0:
            raise ValueError("max_request_bytes must be positive")
        if int(self.max_sessions) <= 0:
            raise ValueError("max_sessions must be positive")
        if not _is_loopback_host(self.host) and not self.auth_token:
            raise ValueError(
                "a bearer token is required when the sidecar binds beyond loopback"
            )


@dataclass
class _Session:
    config: ContextPluginConfig
    middleware: ContextPrunerMiddleware
    lock: threading.RLock = field(default_factory=threading.RLock)


class ContextSidecarService:
    """Thread-safe in-memory session manager behind the HTTP transport."""

    def __init__(self, config: SidecarServerConfig | None = None):
        self.config = config or SidecarServerConfig()
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "sessions": self.session_count,
        }

    def capabilities(self) -> dict[str, Any]:
        # Lazy import avoids a package cycle while the adapter registry imports.
        from .adapters.registry import get_adapter_descriptor

        return {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "adapter": get_adapter_descriptor("http_sidecar").to_dict(),
            "routes": {
                "lifecycle": "POST /v1/lifecycle",
                "before_model": "POST /v1/sessions/{session_id}/before-model",
                "after_model": "POST /v1/sessions/{session_id}/after-model",
                "after_tool": "POST /v1/sessions/{session_id}/after-tool",
                "error": "POST /v1/sessions/{session_id}/error",
                "finalize": "POST /v1/sessions/{session_id}/finalize",
                "state": "GET|PUT /v1/sessions/{session_id}/state",
                "delete": "DELETE /v1/sessions/{session_id}",
            },
            "authoritative_history_owner": "host",
            "streaming": False,
        }

    def lifecycle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Dispatch one authenticated JSON request from low-code workflow nodes."""
        session_id = request.get("session_id")
        if not isinstance(session_id, str):
            raise SidecarRequestError(400, "invalid_session_id", "session_id must be a string")
        _validate_session_id(session_id)
        operation = request.get("operation")
        if not isinstance(operation, str):
            raise SidecarRequestError(400, "invalid_operation", "operation must be a string")
        payload = request.get("payload", {})
        if not isinstance(payload, Mapping):
            raise SidecarRequestError(400, "invalid_payload", "payload must be a JSON object")
        handlers = {
            "before_model": self.before_model,
            "after_model": self.after_model,
            "after_tool": self.after_tool,
            "on_error": self.on_error,
            "finalize": self.finalize,
            "restore_state": self.restore,
        }
        if operation in handlers:
            return handlers[operation](session_id, payload)
        if operation == "get_state":
            return self.state(session_id)
        if operation == "delete_session":
            return self.delete(session_id)
        raise SidecarRequestError(400, "invalid_operation", "unsupported lifecycle operation")

    def before_model(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise SidecarRequestError(400, "invalid_messages", "messages must be a JSON list")
        reserved = _nonnegative_int(payload.get("reserved_tokens", 0), "reserved_tokens")
        task_state = str(payload.get("task_state") or "")
        session = self._get_or_create(session_id, payload, task_state=task_state)
        with session.lock:
            result = session.middleware.before_model(
                messages,
                task_state=task_state,
                reserved_tokens=reserved,
            )
            return self._hook_response(session_id, result.messages, result.metrics, result.lifecycle_state)

    def after_model(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if "output" not in payload:
            raise SidecarRequestError(400, "missing_output", "output is required")
        session = self._require_session(session_id)
        with session.lock:
            result = session.middleware.after_model(payload["output"])
            return self._hook_response(session_id, result.messages, result.metrics, result.lifecycle_state)

    def after_tool(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if "output" not in payload:
            raise SidecarRequestError(400, "missing_output", "output is required")
        session = self._require_session(session_id)
        with session.lock:
            result = session.middleware.after_tool(payload["output"])
            return self._hook_response(session_id, result.messages, result.metrics, result.lifecycle_state)

    def on_error(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if "error" not in payload:
            raise SidecarRequestError(400, "missing_error", "error is required")
        session = self._require_session(session_id)
        with session.lock:
            result = session.middleware.on_error(
                str(payload["error"]),
                query=str(payload.get("query") or ""),
                recover=_boolean(payload, "recover", True),
            )
            return self._hook_response(session_id, result.messages, result.metrics, result.lifecycle_state)

    def finalize(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        metadata = payload.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise SidecarRequestError(400, "invalid_metadata", "metadata must be a JSON object")
        session = self._require_session(session_id)
        with session.lock:
            metrics = session.middleware.finalize(metadata)
            return {
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "session_id": session_id,
                "metrics": metrics,
                "lifecycle_state": session.middleware.export_state(),
            }

    def state(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        with session.lock:
            return {
                "schema_version": SIDECAR_SCHEMA_VERSION,
                "session_id": session_id,
                "config": _config_dict(session.config),
                "metrics": session.middleware.metrics_dict(),
                "lifecycle_state": session.middleware.export_state(),
            }

    def restore(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        state = payload.get("lifecycle_state", payload.get("state"))
        if not isinstance(state, Mapping):
            raise SidecarRequestError(
                400,
                "invalid_state",
                "lifecycle_state must be a JSON object",
            )
        task_state = str(payload.get("task_state") or "")
        with self._lock:
            existing = self._sessions.get(session_id)
            fallback = existing.config if existing else self.config.default_plugin
            plugin_config = _plugin_config(payload, fallback)
            if existing is None and len(self._sessions) >= self.config.max_sessions:
                raise SidecarRequestError(429, "session_capacity", "maximum sidecar sessions reached")
            middleware = ContextPrunerMiddleware.from_state(
                state,
                config=plugin_config,
                task_state=task_state,
            )
            self._sessions[session_id] = _Session(plugin_config, middleware)
        return self.state(session_id)

    def delete(self, session_id: str) -> dict[str, Any]:
        _validate_session_id(session_id)
        with self._lock:
            deleted = self._sessions.pop(session_id, None) is not None
        return {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "session_id": session_id,
            "deleted": deleted,
        }

    def _get_or_create(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        task_state: str,
    ) -> _Session:
        _validate_session_id(session_id)
        with self._lock:
            existing = self._sessions.get(session_id)
            fallback = existing.config if existing else self.config.default_plugin
            requested = _plugin_config(payload, fallback)
            if existing is not None:
                if any(key in payload for key in _CONFIG_KEYS) and requested != existing.config:
                    raise SidecarRequestError(
                        409,
                        "session_config_mismatch",
                        "delete or restore the session before changing its plugin configuration",
                    )
                return existing
            if len(self._sessions) >= self.config.max_sessions:
                raise SidecarRequestError(429, "session_capacity", "maximum sidecar sessions reached")
            state = payload.get("lifecycle_state", payload.get("state"))
            if state is not None and not isinstance(state, Mapping):
                raise SidecarRequestError(400, "invalid_state", "state must be a JSON object")
            middleware = ContextPrunerMiddleware.from_state(
                state,
                config=requested,
                task_state=task_state,
            )
            session = _Session(requested, middleware)
            self._sessions[session_id] = session
            return session

    def _require_session(self, session_id: str) -> _Session:
        _validate_session_id(session_id)
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SidecarRequestError(404, "session_not_found", "sidecar session not found")
        return session

    @staticmethod
    def _hook_response(
        session_id: str,
        messages: list[Any],
        metrics: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": SIDECAR_SCHEMA_VERSION,
            "session_id": session_id,
            "messages": messages,
            "metrics": dict(metrics),
            "lifecycle_state": dict(state),
        }


def create_sidecar_server(
    config: SidecarServerConfig | None = None,
    *,
    service: ContextSidecarService | None = None,
) -> ThreadingHTTPServer:
    config = config or SidecarServerConfig()
    service = service or ContextSidecarService(config)
    handler = _handler_factory(service, config)
    server = ThreadingHTTPServer((config.host, config.port), handler)
    server.daemon_threads = True
    server.context_pruner_service = service  # type: ignore[attr-defined]
    return server


def serve_sidecar(config: SidecarServerConfig | None = None) -> None:
    config = config or SidecarServerConfig()
    server = create_sidecar_server(config)
    host, port = server.server_address[:2]
    print(f"Context-Pruner sidecar {__version__} listening on http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _handler_factory(service: ContextSidecarService, config: SidecarServerConfig):
    class ContextSidecarHandler(BaseHTTPRequestHandler):
        server_version = "ContextPrunerSidecar/1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
            try:
                path = urlsplit(self.path).path
                if path == "/health":
                    self._send(200, service.health())
                    return
                self._authorize()
                if path == "/v1/capabilities":
                    self._send(200, service.capabilities())
                    return
                session_id, action = _session_route(path)
                if action == "state":
                    self._send(200, service.state(session_id))
                    return
                raise SidecarRequestError(404, "not_found", "route not found")
            except SidecarRequestError as error:
                self._error(error)
            except Exception:
                self._error(
                    SidecarRequestError(500, "internal_error", "sidecar request failed")
                )

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._authorize()
                payload = self._json_body()
                path = urlsplit(self.path).path
                if path == "/v1/lifecycle":
                    self._send(200, service.lifecycle(payload))
                    return
                session_id, action = _session_route(path)
                handlers = {
                    "before-model": service.before_model,
                    "after-model": service.after_model,
                    "after-tool": service.after_tool,
                    "error": service.on_error,
                    "finalize": service.finalize,
                }
                callback = handlers.get(action)
                if callback is None:
                    raise SidecarRequestError(404, "not_found", "route not found")
                self._send(200, callback(session_id, payload))
            except SidecarRequestError as error:
                self._error(error)
            except (TypeError, ValueError) as error:
                self._error(SidecarRequestError(400, "invalid_request", str(error)))
            except Exception:
                self._error(
                    SidecarRequestError(500, "internal_error", "sidecar request failed")
                )

        def do_PUT(self) -> None:  # noqa: N802
            try:
                self._authorize()
                payload = self._json_body()
                session_id, action = _session_route(urlsplit(self.path).path)
                if action != "state":
                    raise SidecarRequestError(404, "not_found", "route not found")
                self._send(200, service.restore(session_id, payload))
            except SidecarRequestError as error:
                self._error(error)
            except (TypeError, ValueError) as error:
                self._error(SidecarRequestError(400, "invalid_request", str(error)))
            except Exception:
                self._error(
                    SidecarRequestError(500, "internal_error", "sidecar request failed")
                )

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                self._authorize()
                session_id, action = _session_route(urlsplit(self.path).path)
                if action:
                    raise SidecarRequestError(404, "not_found", "route not found")
                self._send(200, service.delete(session_id))
            except SidecarRequestError as error:
                self._error(error)
            except Exception:
                self._error(
                    SidecarRequestError(500, "internal_error", "sidecar request failed")
                )

        def _authorize(self) -> None:
            if not config.auth_token:
                return
            header = self.headers.get("Authorization", "")
            prefix = "Bearer "
            supplied = header[len(prefix) :] if header.startswith(prefix) else ""
            if not hmac.compare_digest(supplied, config.auth_token):
                raise SidecarRequestError(401, "unauthorized", "valid bearer token required")

        def _json_body(self) -> Mapping[str, Any]:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as error:
                raise SidecarRequestError(400, "invalid_length", "invalid Content-Length") from error
            if length < 0:
                raise SidecarRequestError(400, "invalid_length", "invalid Content-Length")
            if length > config.max_request_bytes:
                raise SidecarRequestError(413, "request_too_large", "JSON request exceeds configured limit")
            media_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if length and media_type != "application/json":
                raise SidecarRequestError(415, "unsupported_media_type", "Content-Type must be application/json")
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise SidecarRequestError(400, "invalid_json", "request body must be valid UTF-8 JSON") from error
            if not isinstance(payload, Mapping):
                raise SidecarRequestError(400, "invalid_json_object", "request body must be a JSON object")
            return payload

        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            encoded = (json.dumps(payload, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)

        def _error(self, error: SidecarRequestError) -> None:
            self._send(
                error.status,
                {"error": {"code": error.code, "message": error.message}},
            )

        def log_message(self, _format: str, *args: Any) -> None:
            # Avoid logging request paths or headers that may contain tenant IDs.
            return

    return ContextSidecarHandler


def _plugin_config(
    payload: Mapping[str, Any],
    fallback: ContextPluginConfig,
) -> ContextPluginConfig:
    budget_data = payload.get("budget")
    if budget_data is None:
        budget = fallback.budget
    elif isinstance(budget_data, Mapping):
        try:
            budget = ContextBudget(
                _nonnegative_int(
                    budget_data.get(
                        "soft",
                        budget_data.get(
                            "soft_limit_tokens", fallback.budget.soft_limit_tokens
                        ),
                    ),
                    "budget.soft",
                ),
                _nonnegative_int(
                    budget_data.get(
                        "hard",
                        budget_data.get(
                            "hard_limit_tokens", fallback.budget.hard_limit_tokens
                        ),
                    ),
                    "budget.hard",
                ),
                _nonnegative_int(
                    budget_data.get(
                        "target",
                        budget_data.get(
                            "target_tokens", fallback.budget.target_tokens
                        ),
                    ),
                    "budget.target",
                ),
            )
        except ValueError as error:
            raise SidecarRequestError(400, "invalid_budget", str(error)) from error
    else:
        raise SidecarRequestError(400, "invalid_budget", "budget must be a JSON object")
    try:
        return ContextPluginConfig(
            enabled=_boolean(payload, "enabled", fallback.enabled),
            method=str(payload.get("method", fallback.method)),
            budget=budget,
            recovery_limit=int(payload.get("recovery_limit", fallback.recovery_limit)),
            recovery_ttl_events=int(
                payload.get("recovery_ttl_events", fallback.recovery_ttl_events)
            ),
            capture_checkpoints=_boolean(
                payload, "capture_checkpoints", fallback.capture_checkpoints
            ),
        )
    except (TypeError, ValueError) as error:
        raise SidecarRequestError(400, "invalid_plugin_config", str(error)) from error


def _config_dict(config: ContextPluginConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "method": config.method,
        "budget": {
            "soft": config.budget.soft_limit_tokens,
            "hard": config.budget.hard_limit_tokens,
            "target": config.budget.target_tokens,
        },
        "recovery_limit": config.recovery_limit,
        "recovery_ttl_events": config.recovery_ttl_events,
        "capture_checkpoints": config.capture_checkpoints,
    }


def _nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise SidecarRequestError(400, "invalid_integer", f"{field_name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise SidecarRequestError(400, "invalid_integer", f"{field_name} must be a non-negative integer") from error
    if result < 0:
        raise SidecarRequestError(400, "invalid_integer", f"{field_name} must be a non-negative integer")
    return result


def _boolean(payload: Mapping[str, Any], key: str, fallback: bool) -> bool:
    if key not in payload:
        return fallback
    value = payload[key]
    if not isinstance(value, bool):
        raise SidecarRequestError(400, "invalid_boolean", f"{key} must be a boolean")
    return value


def _validate_session_id(session_id: str) -> None:
    if not _SESSION_ID.fullmatch(session_id):
        raise SidecarRequestError(
            400,
            "invalid_session_id",
            "session_id must contain 1-128 letters, digits, dots, underscores, colons or hyphens",
        )


def _session_route(path: str) -> tuple[str, str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) not in {3, 4} or parts[:2] != ["v1", "sessions"]:
        raise SidecarRequestError(404, "not_found", "route not found")
    session_id = parts[2]
    _validate_session_id(session_id)
    return session_id, parts[3] if len(parts) == 4 else ""


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


__all__ = [
    "SIDECAR_SCHEMA_VERSION",
    "ContextSidecarService",
    "SidecarRequestError",
    "SidecarServerConfig",
    "create_sidecar_server",
    "serve_sidecar",
]
