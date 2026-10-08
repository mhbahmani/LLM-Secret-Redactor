import json
import subprocess
import os
import sys
import tempfile
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src", "secret_redactor")
SESSION = "test-session-123"
TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="llm-redactor-lifecycle-")
os.environ["SECRET_REDACTOR_RUNTIME_DIR"] = TEST_RUNTIME_DIR

def run_hook(script_name: str, input_data: dict, env: dict = None) -> dict:
    script_path = os.path.join(SRC_DIR, script_name)
    proc = subprocess.run(
        [sys.executable, script_path],
        input=json.dumps(input_data),
        text=True,
        capture_output=True,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, f"Hook failed: {proc.stderr}"
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)

def test_flow():
    # 1. UserPromptSubmit blocks prompt with raw secret
    res = run_hook("user_prompt_submit.py", {
        "session_id": SESSION,
        "prompt": "Here is my secret sk-proj-1234567890abcdef1234567890"
    })
    assert res.get("decision") == "block", f"Prompt was not blocked: {res}"
    print("✓ user_prompt_submit: successfully blocked prompt with secret")

    # 2. PostToolUse intercepts tool output (e.g. cat .env)
    fake_tool_res = {
        "stdout": "DATABASE_URL=postgres://user:supersecretpass123@localhost/db\nGITHUB_TOKEN=ghp_123456789012345678901234567890123456"
    }
    res = run_hook("post_tool_use.py", {
        "session_id": SESSION,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "cat .env"},
        "tool_response": fake_tool_res
    })
    updated = res.get("hookSpecificOutput", {}).get("updatedToolOutput", {})
    stdout = updated.get("stdout", "")
    assert "supersecretpass123" not in stdout, "Password was not masked!"
    assert "ghp_123456789012345678901234567890123456" not in stdout, "GitHub token not masked!"
    assert "__MASKED_" in stdout, f"Expected masked token, got: {stdout}"
    print("✓ post_tool_use: successfully masked secrets from tool response")

    # Extract masked tokens from stdout
    import re
    tokens = re.findall(r"__MASKED_[A-Z_0-9]+__", stdout)
    assert len(tokens) >= 2, f"Found tokens: {tokens}"

    # 3. MessageDisplay unmasks delta on display
    llm_delta = f"Found the credentials. Password is {tokens[0]} and token is {tokens[1]}."
    res = run_hook("message_display.py", {
        "session_id": SESSION,
        "hook_event_name": "MessageDisplay",
        "delta": llm_delta
    })
    displayed = res.get("hookSpecificOutput", {}).get("displayContent", "")
    assert "supersecretpass123" in displayed or "ghp_123456789012345678901234567890123456" in displayed
    assert tokens[0] not in displayed
    print("✓ message_display: successfully restored real secrets for terminal display")

    # 4. PreToolUse unmasks token if Claude sends it back into a tool
    gh_token = [t for t in tokens if "TOKEN" in t][0]
    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"curl -H 'Authorization: {gh_token}' https://api.github.com"}
    })
    output = res.get("hookSpecificOutput", {})
    assert output.get("permissionDecision") == "ask", res
    assert "ghp_123456789012345678901234567890123456" in output["updatedInput"]["command"]
    print("✓ pre_tool_use: restored a secret for Bash after asking the user")

    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/x", "content": f"TOKEN={gh_token}"}
    })
    output = res["hookSpecificOutput"]
    assert "permissionDecision" not in output, res
    assert "ghp_123456789012345678901234567890123456" in output["updatedInput"]["content"]

    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "tool_name": "WebFetch",
        "tool_input": {"url": f"https://example.test/?k={gh_token}", "prompt": "x"}
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny", res
    print("✓ pre_tool_use: follows the per-tool restore policy")

    # 5. PreToolUse refuses tokens that the session cannot resolve
    stale = "__MASKED_TOKEN_" + "0" * 32 + "__"
    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/x", "content": f"KEY={stale}"}
    })
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny", res
    print("✓ pre_tool_use: blocked a tool call with an unknown mask token")

    # 6. Without a broker, hooks still keep secrets away from the model
    broken = {"SECRET_REDACTOR_RUNTIME_DIR": os.path.join(TEST_RUNTIME_DIR, "broker.sock", "missing")}
    res = run_hook("post_tool_use.py", {
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_response": fake_tool_res,
    }, env=broken)
    stdout = res["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert "supersecretpass123" not in stdout and "[REDACTED_URIPASS]" in stdout, stdout
    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": f"echo {gh_token}"},
    }, env=broken)
    assert res["hookSpecificOutput"]["permissionDecision"] == "deny", res
    print("✓ hooks: fail closed when the broker is unavailable")

    print("\nALL LIFECYCLE TESTS PASSED.")

if __name__ == "__main__":
    try:
        test_flow()
    finally:
        sys.path.insert(0, SRC_DIR)
        from vault import shutdown_broker
        shutdown_broker()
        shutil.rmtree(TEST_RUNTIME_DIR, ignore_errors=True)
