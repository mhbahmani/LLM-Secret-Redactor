#!/usr/bin/env python3
import http.server
import json
import sys
import time

LOG_PATH = "/tmp/mock_llm_requests.log"


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _log(self, body_bytes, path):
        with open(LOG_PATH, "ab") as f:
            f.write(b"\n=== " + time.strftime("%Y-%m-%d %H:%M:%S").encode() + b" " + path.encode() + b" ===\n")
            f.write(body_bytes if body_bytes else b"(empty)")
            f.write(b"\n")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._send_json({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send_json({})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
        self._log(raw, self.path)

        if self.path.rstrip("/").endswith("/chat/completions"):
            streaming = False
            try:
                req = json.loads(raw or b"{}")
                streaming = bool(req.get("stream"))
            except Exception:
                req = {}

            msgs = req.get("messages") or []
            has_tool_result = any(m.get("role") == "tool" for m in msgs)
            last_user = ""
            for m in reversed(msgs):
                if m.get("role") == "user":
                    last_user = m.get("content", "") if isinstance(m.get("content"), str) else ""
                    break

            if streaming:
                # Echo back the last user text. Emit a read tool call on .env.test
                # only on the first turn (no tool result yet) so the loop terminates.
                tool_call = None
                if not has_tool_result:
                    for t in (req.get("tools") or []):
                        fn = (t.get("function") or {}).get("name", "")
                        if "read" in fn.lower():
                            tool_call = {
                                "type": "function",
                                "function": {"name": fn, "arguments": json.dumps({"filePath": ".env.test"})},
                            }
                            break
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                if tool_call:
                    def _chunk(kw, fr=None):
                        return {
                            "id": "chatcmpl-mock01",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "mock",
                            "choices": [{"index": 0, "delta": kw, "finish_reason": fr}],
                        }
                    t = {
                        "index": 0,
                        "id": "call_mock01",
                        "type": "function",
                        "function": {"name": tool_call["function"]["name"], "arguments": tool_call["function"]["arguments"]},
                    }
                    echo = last_user[:400] or "(empty prompt)"
                    self.wfile.write(f"data: {json.dumps(_chunk({'role': 'assistant', 'content': echo}))}\n\n".encode())
                    self.wfile.write(f"data: {json.dumps(_chunk({'content': None, 'tool_calls': [t]}))}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    self.close_connection = True
                    return
                echo = last_user[:400] or "(empty prompt)"
                for token in [echo]:
                    chunk = {
                        "id": "chatcmpl-mock01",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "mock",
                        "choices": [{"index": 0, "delta": {"content": token}, "finish_reason": None}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(0.05)
                last = {
                    "id": "mock",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "mock",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
                self.wfile.write(f"data: {json.dumps(last)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True
            else:
                echo = last_user[:400] or "(empty prompt)"
                if has_tool_result:
                    self._send_json({
                        "id": "mock",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": "mock",
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": echo},
                            "finish_reason": "stop",
                        }],
                    })
                else:
                    tool_call = None
                    for t in (req.get("tools") or []):
                        fn = (t.get("function") or {}).get("name", "")
                        if "read" in fn.lower():
                            tool_call = {
                                "type": "function",
                                "function": {"name": fn, "arguments": json.dumps({"filePath": ".env.test"})},
                            }
                            break
                    if tool_call:
                        self._send_json({
                            "id": "mock",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": "mock",
                            "choices": [{
                                "index": 0,
                                "message": {"role": "assistant", "content": echo, "tool_calls": [tool_call]},
                                "finish_reason": "tool_calls",
                            }],
                        })
                    else:
                        self._send_json({
                            "id": "mock",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": "mock",
                            "choices": [{
                                "index": 0,
                                "message": {"role": "assistant", "content": echo},
                                "finish_reason": "stop",
                            }],
                        })
        else:
            self._send_json({})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9876
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock LLM on 127.0.0.1:{port}, logging to {LOG_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass