import json
import os
import re
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Dict, Tuple


DEFAULT_PATTERNS = [
    {"name": "Anthropic key", "pattern": r"(?<![A-Za-z0-9_])sk-ant-[a-zA-Z0-9_\-]{20,}", "kind": "TOKEN"},
    {"name": "OpenAI key", "pattern": r"(?<![A-Za-z0-9_])sk-[a-zA-Z0-9_\-]{20,}", "kind": "TOKEN"},
    {"name": "GitHub Token", "pattern": r"(?<![A-Za-z0-9_])gh[pousr]_[a-zA-Z0-9]{36,}", "kind": "TOKEN"},
    {"name": "AWS Access Key ID", "pattern": r"(?<![A-Za-z0-9_])(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])", "kind": "AWS_KEY"},
    {"name": "Generic bearer token", "pattern": r"bearer\s+([a-zA-Z0-9_\-\.]{20,})", "flags": "i", "kind": "BEARER_TOKEN"},
    {"name": "URI with credentials", "pattern": r"([a-z0-9+.\-]+://[^:\s@/]+:)([^@\s/]+)(@)", "flags": "i", "kind": "URI_PASS"},
    {"name": "Key-value secrets", "pattern": r"""(["']?(?:password|passwd|secret|api[_-]?key|token|auth_token)["']?\s*[:=]\s*["']?)([^\s"',;}{]{6,})(["']?)""", "flags": "i", "kind": "KV_SECRET"},
    {"name": "JWT token pattern", "pattern": r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}", "kind": "JWT_TOKEN"},
]


def load_patterns():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json"),
        os.path.join(os.getcwd(), "patterns.json"),
    ]
    raw_patterns = None
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    raw_patterns = json.load(handle)
                break
            except Exception:
                continue
    compiled = []
    for item in raw_patterns or DEFAULT_PATTERNS:
        flags = re.IGNORECASE if "i" in item.get("flags", "") else 0
        compiled.append((re.compile(item["pattern"], flags), item["kind"]))
    return compiled


SECRET_PATTERNS = load_patterns()
BROKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "broker.py")
_BROKER_PROCESS = None


def get_runtime_dir() -> str:
    override = os.environ.get("SECRET_REDACTOR_RUNTIME_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return os.path.join(base, "llm-secret-redactor")
    return os.path.join("/tmp", f"llm-secret-redactor-{os.getuid()}")


def get_socket_path() -> str:
    return os.path.join(get_runtime_dir(), "broker.sock")


def _ensure_runtime_dir():
    directory = get_runtime_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.lstat(directory)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError(f"Unsafe broker runtime directory: {directory}")
    os.chmod(directory, 0o700)


def _send(request: Dict[str, Any]) -> Dict[str, Any]:
    encoded = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(get_socket_path())
        client.sendall(encoded)
        chunks = bytearray()
        while not chunks.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > 16 * 1024 * 1024:
                raise RuntimeError("Secret broker response exceeded size limit")
    if not chunks:
        raise RuntimeError("Secret broker returned an empty response")
    response = json.loads(chunks)
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Secret broker request failed"))
    return response


def _start_broker():
    global _BROKER_PROCESS
    if not os.path.isfile(BROKER_SCRIPT):
        raise RuntimeError(f"Secret broker executable is missing: {BROKER_SCRIPT}")
    _ensure_runtime_dir()
    _BROKER_PROCESS = subprocess.Popen(
        [sys.executable, BROKER_SCRIPT, "--serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 3
    last_error = None
    while time.monotonic() < deadline:
        try:
            _send({"operation": "ping"})
            return
        except (OSError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.025)
    raise RuntimeError(f"Secret broker did not start: {last_error}")


def broker_request(operation: str, session_id: str = "default", value: Any = None) -> Dict[str, Any]:
    request = {"operation": operation, "session": session_id or "default"}
    if value is not None:
        request["value"] = value
    try:
        return _send(request)
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, RuntimeError):
        _start_broker()
        return _send(request)


def mask_text(text: str, session_id: str) -> Tuple[str, Dict[str, str]]:
    if not isinstance(text, str) or not text:
        return text, {}
    response = broker_request("mask", session_id, text)
    return response["value"], response.get("mappings", {})


def unmask_text(text: str, session_id: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    return broker_request("unmask", session_id, text)["value"]


def mask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    response = broker_request("mask", session_id, obj)
    return response["value"], bool(response.get("changed"))


def unmask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    response = broker_request("unmask", session_id, obj)
    return response["value"], bool(response.get("changed"))


def clear_session(session_id: str):
    broker_request("clear", session_id)


def broker_stats():
    return broker_request("stats")


def shutdown_broker():
    global _BROKER_PROCESS
    try:
        response = _send({"operation": "shutdown"})
        if _BROKER_PROCESS is not None:
            try:
                _BROKER_PROCESS.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        _BROKER_PROCESS = None
        deadline = time.monotonic() + 2
        while os.path.lexists(get_socket_path()) and time.monotonic() < deadline:
            time.sleep(0.01)
        return response
    except (OSError, RuntimeError):
        return {"ok": True}
