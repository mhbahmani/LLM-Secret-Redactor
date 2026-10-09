import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, Optional, Tuple


try:
    from . import broker
except ImportError:
    import broker


SECRET_PATTERNS = broker.PATTERNS
BROKER_SCRIPT = os.path.abspath(broker.__file__)
_BROKER_PROCESS = None


get_runtime_dir = broker.runtime_dir
get_socket_path = broker.socket_path


class BrokerUnavailable(RuntimeError):
    """The broker closed the connection without answering, e.g. while exiting."""


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
        raise BrokerUnavailable("Secret broker returned an empty response")
    response = json.loads(chunks)
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "Secret broker request failed"))
    return response


def _start_broker():
    global _BROKER_PROCESS
    if not os.path.isfile(BROKER_SCRIPT):
        raise RuntimeError(f"Secret broker executable is missing: {BROKER_SCRIPT}")
    broker.ensure_runtime_dir()
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


def broker_request(
    operation: str, session_id: Optional[str] = None, value: Any = None, **fields: Any
) -> Dict[str, Any]:
    request = {"operation": operation, **fields}
    if session_id is not None:
        request["session"] = session_id
    if value is not None:
        request["value"] = value
    try:
        return _send(request)
    except (FileNotFoundError, ConnectionRefusedError, BrokerUnavailable):
        # Only start a broker when none is listening. A timeout means the
        # broker is alive but busy, and error replies are not retryable.
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


def restore_tool_input(tool_input: Any, session_id: str, tool: str) -> Dict[str, Any]:
    """Unmask tool input. The response lists tokens the session cannot resolve
    and the restore policy decision for the tool."""
    return broker_request("unmask", session_id, tool_input, tool=tool)


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
