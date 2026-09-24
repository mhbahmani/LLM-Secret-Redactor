import os
import json
import re
import hashlib
from typing import Tuple, Dict, Any

# Load secret patterns from patterns.json with hardcoded fallback
DEFAULT_PATTERNS = [
    {"name": "Anthropic key", "pattern": r"sk-ant-[a-zA-Z0-9_\-]{20,}", "kind": "TOKEN"},
    {"name": "OpenAI key", "pattern": r"sk-[a-zA-Z0-9_\-]{20,}", "kind": "TOKEN"},
    {"name": "GitHub Token", "pattern": r"gh[pousr]_[a-zA-Z0-9]{36,}", "kind": "TOKEN"},
    {"name": "AWS Access Key ID", "pattern": r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}", "kind": "AWS_KEY"},
    {"name": "Generic bearer token", "pattern": r"bearer\s+([a-zA-Z0-9_\-\.]{20,})", "flags": "i", "kind": "BEARER_TOKEN"},
    {"name": "URI with credentials", "pattern": r"([a-z0-9+.\-]+://[^:\s@/]+:)([^@\s/]+)(@)", "flags": "i", "kind": "URI_PASS"},
    {"name": "Key-value secrets", "pattern": r"""(["']?(?:password|passwd|secret|api[_-]?key|token|auth_token)["']?\s*[:=]\s*["']?)([^\s"',;}{]{6,})(["']?)""", "flags": "i", "kind": "KV_SECRET"},
    {"name": "JWT token pattern", "pattern": r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}", "kind": "JWT_TOKEN"},
]

def load_patterns():
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "patterns.json"),
        os.path.join(os.getcwd(), "patterns.json"),
    ]
    raw_patterns = None
    for c in candidates:
        if os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as f:
                    raw_patterns = json.load(f)
                break
            except Exception:
                pass
    if not raw_patterns:
        raw_patterns = DEFAULT_PATTERNS

    compiled = []
    for item in raw_patterns:
        flags = 0
        if "i" in item.get("flags", ""):
            flags |= re.IGNORECASE
        compiled.append((re.compile(item["pattern"], flags), item["kind"]))
    return compiled

SECRET_PATTERNS = load_patterns()

VAULT_DIR = os.environ.get("VAULT_DIR", "/tmp/claude_secret_vault")

def get_vault_path(session_id: str) -> str:
    os.makedirs(VAULT_DIR, mode=0o700, exist_ok=True)
    os.chmod(VAULT_DIR, 0o700)
    safe_session = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id or "default")
    return os.path.join(VAULT_DIR, f"vault_{safe_session}.json")

def load_vault(session_id: str) -> Dict[str, str]:
    path = get_vault_path(session_id)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise RuntimeError(f"Unable to read secret vault {path}: {exc}") from exc

def save_vault(session_id: str, vault: Dict[str, str]):
    path = get_vault_path(session_id)
    temp_path = f"{path}.{os.getpid()}.tmp"
    try:
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(vault, f, indent=2)
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    except Exception as exc:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise RuntimeError(f"Unable to persist secret vault {path}: {exc}") from exc

def _make_token(prefix: str, secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8].upper()
    return f"__MASKED_{prefix}_{digest}__"

def mask_text(text: str, session_id: str) -> Tuple[str, Dict[str, str]]:
    if not isinstance(text, str) or not text:
        return text, {}

    vault = load_vault(session_id)
    updated = False

    # 1. Check KV secrets
    def kv_sub(m: re.Match) -> str:
        nonlocal updated
        prefix_chars, secret, suffix_chars = m.group(1), m.group(2), m.group(3)
        # Avoid double masking existing masked tokens
        if secret.startswith("__MASKED_") and secret.endswith("__"):
            return m.group(0)
        token = _make_token("SECRET", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"{prefix_chars}{token}{suffix_chars}"

    # 2. Check URI password
    def uri_sub(m: re.Match) -> str:
        nonlocal updated
        prefix, secret, suffix = m.group(1), m.group(2), m.group(3)
        token = _make_token("URIPASS", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"{prefix}{token}{suffix}"

    # 3. Check Bearer token
    def bearer_sub(m: re.Match) -> str:
        nonlocal updated
        secret = m.group(1)
        token = _make_token("BEARER", secret)
        if vault.get(token) != secret:
            vault[token] = secret
            updated = True
        return f"Bearer {token}"

    # Standard patterns
    for regex, kind in SECRET_PATTERNS:
        if kind == "KV_SECRET":
            text = regex.sub(kv_sub, text)
        elif kind == "URI_PASS":
            text = regex.sub(uri_sub, text)
        elif kind == "BEARER_TOKEN":
            text = regex.sub(bearer_sub, text)
        else:
            def std_sub(m: re.Match, kind=kind) -> str:
                nonlocal updated
                secret = m.group(0)
                if secret.startswith("__MASKED_") and secret.endswith("__"):
                    return secret
                token = _make_token(kind, secret)
                if vault.get(token) != secret:
                    vault[token] = secret
                    updated = True
                return token
            text = regex.sub(std_sub, text)

    if updated:
        save_vault(session_id, vault)

    return text, vault

def unmask_text(text: str, session_id: str) -> str:
    if not isinstance(text, str) or not text:
        return text

    vault = load_vault(session_id)
    if not vault:
        return text

    # Sort tokens longest first to avoid partial collision
    for token, secret in sorted(vault.items(), key=lambda x: len(x[0]), reverse=True):
        if token in text:
            text = text.replace(token, secret)

    return text

def mask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    if isinstance(obj, str):
        masked, _ = mask_text(obj, session_id)
        return masked, masked != obj
    elif isinstance(obj, dict):
        new_dict = {}
        changed = False
        for k, v in obj.items():
            new_v, ch = mask_recursive(v, session_id)
            new_dict[k] = new_v
            if ch:
                changed = True
        return new_dict, changed
    elif isinstance(obj, list):
        new_list = []
        changed = False
        for item in obj:
            new_item, ch = mask_recursive(item, session_id)
            new_list.append(new_item)
            if ch:
                changed = True
        return new_list, changed
    return obj, False

def unmask_recursive(obj: Any, session_id: str) -> Tuple[Any, bool]:
    if isinstance(obj, str):
        unmasked = unmask_text(obj, session_id)
        return unmasked, unmasked != obj
    elif isinstance(obj, dict):
        new_dict = {}
        changed = False
        for k, v in obj.items():
            new_v, ch = unmask_recursive(v, session_id)
            new_dict[k] = new_v
            if ch:
                changed = True
        return new_dict, changed
    elif isinstance(obj, list):
        new_list = []
        changed = False
        for item in obj:
            new_item, ch = unmask_recursive(item, session_id)
            new_list.append(new_item)
            if ch:
                changed = True
        return new_list, changed
    return obj, False
