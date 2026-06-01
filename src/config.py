"""Config loader. Reads config/config.yaml + .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"
CONFIG_EXAMPLE = ROOT / "config" / "config.example.yaml"


def load_env() -> None:
    """Load .env from project root if present."""
    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load config.yaml, falling back to config.example.yaml."""
    p = path or CONFIG_PATH
    if not p.exists():
        if not CONFIG_EXAMPLE.exists():
            raise FileNotFoundError(f"No config at {p} or {CONFIG_EXAMPLE}")
        p = CONFIG_EXAMPLE
    with open(p) as f:
        return yaml.safe_load(f)


def require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Required env var {name} is not set. See .env.example.")
    return val


def resume_path() -> Path:
    """Real resume in private/ falls back to template."""
    private = ROOT / "resume" / "private" / "resume.yaml"
    if private.exists():
        return private
    return ROOT / "resume" / "resume.template.yaml"


def load_resume() -> dict[str, Any]:
    with open(resume_path()) as f:
        return yaml.safe_load(f)
