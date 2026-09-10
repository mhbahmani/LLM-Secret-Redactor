import json
import subprocess
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO_ROOT, "src", "secret_redactor")
SESSION = "test-session-123"

def run_hook(script_name: str, input_data: dict) -> dict:
    script_path = os.path.join(SRC_DIR, script_name)
    proc = subprocess.run(
        [sys.executable, script_path],
        input=json.dumps(input_data),
        text=True,
        capture_output=True
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
    gh_token = [t for t in tokens if "GITHUB_TOKEN" in t][0]
    res = run_hook("pre_tool_use.py", {
        "session_id": SESSION,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": f"curl -H 'Authorization: {gh_token}' https://api.github.com"}
    })
    updated_input = res.get("hookSpecificOutput", {}).get("updatedInput", {})
    assert "ghp_123456789012345678901234567890123456" in updated_input.get("command", "")
    print("✓ pre_tool_use: successfully restored real secret when tool is executed")

    print("\nALL LIFECYCLE TESTS PASSED.")

if __name__ == "__main__":
    test_flow()
