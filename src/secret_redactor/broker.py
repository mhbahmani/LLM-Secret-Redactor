#!/usr/bin/env python3
"""Per-user, memory-only secret broker for Claude Code and OpenCode hooks."""

import argparse
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import stat
import struct
import threading
import time


MAX_REQUEST_BYTES = 16 * 1024 * 1024
SESSION_TTL_SECONDS = int(os.environ.get("SECRET_REDACTOR_SESSION_TTL", "3600"))
EMPTY_IDLE_SECONDS = int(os.environ.get("SECRET_REDACTOR_BROKER_IDLE", "60"))


def runtime_dir():
    override = os.environ.get("SECRET_REDACTOR_RUNTIME_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return os.path.join(base, "llm-secret-redactor")
    return os.path.join("/tmp", f"llm-secret-redactor-{os.getuid()}")


def socket_path():
    return os.path.join(runtime_dir(), "broker.sock")


def ensure_runtime_dir():
    directory = runtime_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"Unsafe broker runtime directory: {directory}")
    if info.st_uid != os.getuid():
        raise RuntimeError(f"Broker runtime directory is not owned by the current user: {directory}")
    os.chmod(directory, 0o700)
    return directory


def load_patterns():
    pattern_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json")
    with open(pattern_file, "r", encoding="utf-8") as handle:
        raw_patterns = json.load(handle)
    compiled = []
    for item in raw_patterns:
        flags = re.IGNORECASE if "i" in item.get("flags", "") else 0
        compiled.append((re.compile(item["pattern"], flags), item["kind"]))
    return compiled


PATTERNS = load_patterns()


class SessionVault:
    def __init__(self):
        self.token_to_secret = {}
        self.secret_to_token = {}
        self.last_access = time.monotonic()

    def touch(self):
        self.last_access = time.monotonic()

    def token_for(self, kind, secret):
        self.touch()
        key = (kind, secret)
        existing = self.secret_to_token.get(key)
        if existing:
            return existing
        while True:
            token = f"__MASKED_{kind}_{secrets.token_hex(16).upper()}__"
            if token not in self.token_to_secret:
                break
        self.secret_to_token[key] = token
        self.token_to_secret[token] = secret
        return token

    def unmask_text(self, text):
        self.touch()
        for token, secret in sorted(self.token_to_secret.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(token, secret)
        return text


class BrokerState:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()
        self.empty_since = time.monotonic()
        self.stopping = False

    def session(self, session_id, create=True):
        session_id = str(session_id or "default")
        vault = self.sessions.get(session_id)
        if vault is None and create:
            vault = SessionVault()
            self.sessions[session_id] = vault
        if vault:
            vault.touch()
        return vault

    def clear(self, session_id):
        self.sessions.pop(str(session_id or "default"), None)
        if not self.sessions:
            self.empty_since = time.monotonic()

    def expire(self):
        now = time.monotonic()
        expired = [key for key, vault in self.sessions.items() if now - vault.last_access >= SESSION_TTL_SECONDS]
        for key in expired:
            self.sessions.pop(key, None)
        if expired and not self.sessions:
            self.empty_since = now


STATE = BrokerState()


def mask_text(text, vault, mappings):
    if not isinstance(text, str) or not text:
        return text
    result = text
    for regex, kind in PATTERNS:
        if kind == "KV_SECRET":
            def replace_kv(match):
                prefix, secret, suffix = match.group(1), match.group(2), match.group(3)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return match.group(0)
                token = vault.token_for("SECRET", secret)
                mappings[token] = secret
                return f"{prefix}{token}{suffix}"
            result = regex.sub(replace_kv, result)
        elif kind == "URI_PASS":
            def replace_uri(match):
                prefix, secret, suffix = match.group(1), match.group(2), match.group(3)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return match.group(0)
                token = vault.token_for("URIPASS", secret)
                mappings[token] = secret
                return f"{prefix}{token}{suffix}"
            result = regex.sub(replace_uri, result)
        elif kind == "BEARER_TOKEN":
            def replace_bearer(match):
                secret = match.group(1)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return match.group(0)
                token = vault.token_for("BEARER", secret)
                mappings[token] = secret
                return f"Bearer {token}"
            result = regex.sub(replace_bearer, result)
        else:
            def replace_standard(match, token_kind=kind):
                secret = match.group(0)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return secret
                token = vault.token_for(token_kind, secret)
                mappings[token] = secret
                return token
            result = regex.sub(replace_standard, result)
    return result


def transform_value(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [transform_value(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: transform_value(item, transform) for key, item in value.items()}
    return value


def handle_request(request):
    operation = request.get("operation")
    if operation == "ping":
        return {"ok": True, "pid": os.getpid()}

    session_id = request.get("session", "default")
    with STATE.lock:
        if operation == "clear":
            STATE.clear(session_id)
            return {"ok": True}
        if operation == "stats":
            return {"ok": True, "pid": os.getpid(), "sessions": len(STATE.sessions)}
        if operation == "shutdown":
            STATE.sessions.clear()
            STATE.stopping = True
            return {"ok": True}

        vault = STATE.session(session_id, create=operation == "mask")
        if operation == "tokens":
            tokens = [] if vault is None else sorted(vault.token_to_secret)
            return {"ok": True, "tokens": tokens}
        if operation == "reveal":
            if vault is None:
                return {"ok": True, "mappings": {}}
            requested = request.get("tokens")
            if requested is None:
                requested = list(vault.token_to_secret)
            mappings = {
                token: vault.token_to_secret[token]
                for token in requested
                if token in vault.token_to_secret
            }
            vault.touch()
            return {"ok": True, "mappings": mappings}
        if operation == "mask":
            mappings = {}
            original = request.get("value")
            transformed = transform_value(original, lambda text: mask_text(text, vault, mappings))
            return {"ok": True, "value": transformed, "changed": transformed != original, "mappings": mappings}
        if operation == "unmask":
            original = request.get("value")
            if vault is None:
                return {"ok": True, "value": original, "changed": False}
            transformed = transform_value(original, vault.unmask_text)
            return {"ok": True, "value": transformed, "changed": transformed != original}
    raise ValueError(f"Unsupported broker operation: {operation}")


class RequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        if hasattr(socket, "SO_PEERCRED"):
            peer = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _peer_pid, peer_uid, _peer_gid = struct.unpack("3i", peer)
            if peer_uid != os.getuid():
                return
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            response = handle_request(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def active_broker(path):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.2)
            client.connect(path)
            client.sendall(b'{"operation":"ping"}\n')
            return bool(client.recv(256))
    except OSError:
        return False


def serve():
    ensure_runtime_dir()
    path = socket_path()
    if os.path.lexists(path):
        if active_broker(path):
            return 0
        info = os.lstat(path)
        if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
            raise RuntimeError(f"Unsafe existing broker socket: {path}")
        os.unlink(path)

    server = BrokerServer(path, RequestHandler)
    os.chmod(path, 0o600)
    server.timeout = 1

    def stop(_signum, _frame):
        STATE.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not STATE.stopping:
            server.handle_request()
            with STATE.lock:
                STATE.expire()
                if not STATE.sessions and time.monotonic() - STATE.empty_since >= EMPTY_IDLE_SECONDS:
                    break
    finally:
        server.server_close()
        if os.path.lexists(path):
            os.unlink(path)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    raise SystemExit(serve())
