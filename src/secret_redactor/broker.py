#!/usr/bin/env python3
"""Per-user, memory-only secret broker for Claude Code and OpenCode hooks."""

import argparse
import fcntl
import json
import os
import secrets
import signal
import socket
import socketserver
import stat
import struct
import threading
import time

try:
    from .redaction import TOKEN_RE, load_patterns, redact, transform_value
except ImportError:
    from redaction import TOKEN_RE, load_patterns, redact, transform_value


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


PATTERNS = load_patterns()
DECISIONS = {"allow", "ask", "deny"}


def load_policy(path=None):
    """Per-tool decision for running a tool with restored secret values.
    While "enabled" is false, every tool receives restored values."""
    path = path or os.environ.get("SECRET_REDACTOR_POLICY") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "policy.json"
    )
    policy = {"enabled": False, "default": "ask", "tools": {}}
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            policy.update(json.load(handle))
    if not isinstance(policy["enabled"], bool):
        raise ValueError(f"Restore policy \"enabled\" must be true or false: {path}")
    values = [policy["default"], *policy["tools"].values()]
    if any(value not in DECISIONS for value in values):
        raise ValueError(f"Restore policy values must be one of {sorted(DECISIONS)}: {path}")
    return policy


def restore_decision(policy, tool):
    if not policy["enabled"]:
        return "allow"
    return policy["tools"].get(tool, policy["default"])


POLICY = load_policy()


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
        """Restore known tokens. Unknown ones (expired, other session) stay as-is."""
        self.touch()
        return TOKEN_RE.sub(lambda match: self.token_to_secret.get(match.group(0), match.group(0)), text)


class BrokerState:
    def __init__(self):
        self.sessions = {}
        self.lock = threading.RLock()
        self.empty_since = time.monotonic()
        self.stopping = False

    def session(self, session_id, create=True):
        vault = self.sessions.get(session_id)
        if vault is None and create:
            vault = SessionVault()
            self.sessions[session_id] = vault
        if vault:
            vault.touch()
        return vault

    def clear(self, session_id):
        self.sessions.pop(session_id, None)
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
SESSION_OPERATIONS = {"clear", "tokens", "reveal", "mask", "unmask"}


def mask_text(text, vault, mappings):
    def replace(kind, secret):
        token = vault.token_for(kind, secret)
        mappings[token] = secret
        return token

    return redact(text, PATTERNS, replace)


def handle_request(request):
    operation = request.get("operation")
    if operation == "ping":
        return {"ok": True, "pid": os.getpid()}

    session_id = request.get("session")
    if operation in SESSION_OPERATIONS and (not isinstance(session_id, str) or not session_id):
        raise ValueError(f"Broker operation {operation!r} requires a session id")
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
            transformed = original if vault is None else transform_value(original, vault.unmask_text)
            return {
                "ok": True,
                "value": transformed,
                "changed": transformed != original,
                "decision": restore_decision(POLICY, request.get("tool")),
            }
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


def acquire_instance_lock(directory):
    """Hold an exclusive lock for the broker's lifetime. The kernel releases it
    when the process dies, so a second broker can never replace a live one,
    even if the live one is too busy to answer for a while."""
    fd = os.open(os.path.join(directory, "broker.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def run_janitor():
    """Expire idle sessions, then stop once the broker has had none for a while.
    Runs off the accept loop so a long mask request never blocks new clients."""
    while not STATE.stopping:
        time.sleep(1)
        with STATE.lock:
            STATE.expire()
            if not STATE.sessions and time.monotonic() - STATE.empty_since >= EMPTY_IDLE_SECONDS:
                STATE.stopping = True


def serve():
    directory = ensure_runtime_dir()
    lock_fd = acquire_instance_lock(directory)
    if lock_fd is None:
        return 0

    # Holding the lock means any existing socket is stale.
    path = socket_path()
    if os.path.lexists(path):
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
    threading.Thread(target=run_janitor, daemon=True).start()
    try:
        while not STATE.stopping:
            server.handle_request()
    finally:
        server.server_close()
        # Safe while the lock is held: no other broker can own this path.
        if os.path.lexists(path):
            os.unlink(path)
        os.close(lock_fd)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    raise SystemExit(serve())
