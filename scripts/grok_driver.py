# Copyright 2026 AlphaOne LLC
# SPDX-License-Identifier: Apache-2.0
"""
Thin stdlib wrapper around the xAI API for `grok-4.20-0309-reasoning`.

S67 + S68 use this to drive an actual reasoning model. Other scenarios are
deterministic and don't need it.

Environment:
  XAI_API_KEY      required
  XAI_MODEL        defaults to "grok-4.20-0309-reasoning"
  XAI_BASE_URL     defaults to "https://api.x.ai/v1"
  XAI_TIMEOUT_S    defaults to 60

Returns dict shape: {"text": str, "reasoning": str, "model": str, "usage": dict}.

Reasoning models on xAI return reasoning content in either the `reasoning_content`
field or as a separate channel under `choices[0].message.reasoning_content`.
We try both and concatenate non-empty.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from typing import Any


def _env(key: str, default: str | None = None) -> str:
    v = os.environ.get(key) or default
    if v is None:
        print(f"grok_driver: {key} not set", file=sys.stderr)
        raise SystemExit(2)
    return v


def grok_chat(prompt: str, system_msg: str = "",
              max_tokens: int = 1024, temperature: float = 0.4) -> dict[str, Any]:
    """One-shot chat completion call. Returns {text, reasoning, model, usage}."""
    api_key = _env("XAI_API_KEY")
    model   = _env("XAI_MODEL", "grok-4.20-0309-reasoning")
    base    = _env("XAI_BASE_URL", "https://api.x.ai/v1")
    timeout = float(os.environ.get("XAI_TIMEOUT_S", "60"))

    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": prompt})

    req_body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # xAI honors `reasoning_effort: "high"` for the *reasoning* SKUs;
        # the field is silently ignored on non-reasoning SKUs.
        "reasoning_effort": "high",
    }

    req = urllib.request.Request(
        url=f"{base.rstrip('/')}/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
        return {"text": "", "reasoning": "", "model": model,
                "usage": {}, "error": f"HTTP {e.code}: {body[:200]}"}
    except urllib.error.URLError as e:
        return {"text": "", "reasoning": "", "model": model,
                "usage": {}, "error": f"URLError: {e.reason}"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"text": "", "reasoning": "", "model": model,
                "usage": {}, "error": f"JSONDecodeError: {e}"}

    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    text = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if not reasoning:
        # fallback: some servers stream reasoning into the top level
        reasoning = data.get("reasoning_content") or ""

    return {
        "text": text.strip(),
        "reasoning": (reasoning or "").strip(),
        "model": data.get("model") or model,
        "usage": data.get("usage") or {},
    }


if __name__ == "__main__":
    # Minimal smoke test: `python3 grok_driver.py "what is 2+2?"`
    q = " ".join(sys.argv[1:]) or "Reply with a single word: pong"
    out = grok_chat(prompt=q, system_msg="Be concise.")
    print(json.dumps(out, indent=2))
