"""LLM backend abstraction.

Two backends:
  - `claude` CLI (headless `-p` mode) — uses your Claude Code subscription, no
    separate API key required. Default.
  - Anthropic SDK — requires ANTHROPIC_API_KEY in env. Faster (no subprocess).

Pick via config.llm.backend or env LLM_BACKEND.
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Iterable

from . import config


@dataclass
class LLMConfig:
    backend: str = "claude_cli"     # "claude_cli" or "anthropic_sdk"
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 2000
    timeout: int = 180
    workers: int = 4                # parallel calls


def _load(*, model_override: str | None = None) -> LLMConfig:
    cfg = config.load_config()
    llm_cfg = cfg.get("llm") or {}
    return LLMConfig(
        backend=os.environ.get("LLM_BACKEND") or llm_cfg.get("backend", "claude_cli"),
        model=(
            model_override
            or os.environ.get("CLAUDE_MODEL")
            or llm_cfg.get("model", "claude-sonnet-4-6")
        ),
        max_tokens=int(llm_cfg.get("max_tokens", 2000)),
        timeout=int(llm_cfg.get("timeout", 180)),
        workers=int(llm_cfg.get("workers", 4)),
    )


# ============== Backends ==============

def _call_claude_cli(prompt: str, cfg: LLMConfig) -> str:
    cmd = [
        "claude",
        "-p",
        "--model", cfg.model,
        "--fallback-model", "claude-haiku-4-5-20251001",
    ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=cfg.timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr.strip()[:400]}")
    return proc.stdout.strip()


def _call_anthropic_sdk(prompt: str, cfg: LLMConfig) -> str:
    from anthropic import Anthropic
    api_key = config.require_env("ANTHROPIC_API_KEY")
    client = Anthropic(api_key=api_key)
    r = client.messages.create(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return r.content[0].text.strip()


def call(prompt: str, cfg: LLMConfig | None = None) -> str:
    cfg = cfg or _load()
    if cfg.backend == "anthropic_sdk":
        return _call_anthropic_sdk(prompt, cfg)
    return _call_claude_cli(prompt, cfg)


def call_many(
    prompts: Iterable[tuple[object, str]],
    *,
    model: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> dict[object, str]:
    """Parallel call. Returns dict keyed by the first element of each tuple.

    Optional `model` overrides config.llm.model — useful for using haiku for
    bulk-score and reserving sonnet for tailored drafting.
    """
    cfg = _load(model_override=model)
    out: dict[object, str] = {}
    items = list(prompts)
    total = len(items)
    done = 0
    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = {ex.submit(call, p, cfg): k for k, p in items}
        for fut in as_completed(futures):
            k = futures[fut]
            try:
                out[k] = fut.result()
            except Exception as e:
                out[k] = f"__ERROR__:{type(e).__name__}:{str(e)[:200]}"
            done += 1
            if progress:
                progress(done, total)
    return out
