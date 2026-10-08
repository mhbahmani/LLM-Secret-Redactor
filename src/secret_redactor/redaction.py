"""Secret pattern matching shared by the broker and the Claude Code hooks."""

import json
import os
import re


PATTERN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patterns.json")
TOKEN_RE = re.compile(r"__MASKED_[A-Z_]+?_[0-9A-F]{8,}__")

# Values that name a secret rather than contain one: dotted attribute
# lookups (settings.API_KEY), env references ($TOKEN, ${TOKEN}, %TOKEN%),
# and literal keywords.
REFERENCE_RE = re.compile(
    r"[A-Za-z_]+(?:\.[A-Za-z_]+)+"
    r"|\$\{?[A-Za-z_]\w*\}?"
    r"|%[A-Za-z_]\w*%"
    r"|(?i:null|none|undefined|true|false)"
)

# Token kinds are shorter than pattern kinds for the composite patterns.
TOKEN_KINDS = {
    "KV_SECRET": "SECRET",
    "URI_PASS": "URIPASS",
    "BEARER_TOKEN": "BEARER",
}


def load_patterns(pattern_file=PATTERN_FILE):
    with open(pattern_file, "r", encoding="utf-8") as handle:
        raw_patterns = json.load(handle)
    compiled = []
    for item in raw_patterns:
        flags = re.IGNORECASE if "i" in item.get("flags", "") else 0
        compiled.append((re.compile(item["pattern"], flags), item["kind"]))
    return compiled


def secret_group(regex):
    """Patterns with context groups keep the secret in group 2 (or 1 if alone)."""
    if regex.groups >= 2:
        return 2
    return 1 if regex.groups == 1 else 0


def redact(text, patterns, replace):
    """Replace every secret in text with replace(kind, secret)."""
    if not isinstance(text, str) or not text:
        return text
    for regex, kind in patterns:
        group = secret_group(regex)
        token_kind = TOKEN_KINDS.get(kind, kind)

        def substitute(match, group=group, token_kind=token_kind):
            secret = match.group(group)
            if not secret or TOKEN_RE.fullmatch(secret) or REFERENCE_RE.fullmatch(secret):
                return match.group(0)
            whole = match.group(0)
            start = match.start(group) - match.start()
            end = match.end(group) - match.start()
            return whole[:start] + replace(token_kind, secret) + whole[end:]

        text = regex.sub(substitute, text)
    return text


def transform_value(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [transform_value(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: transform_value(item, transform) for key, item in value.items()}
    return value
