"""OpenAI-compatible chat-completions client for the answer pipeline."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs/llm.yaml"

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(content: str) -> dict[str, Any]:
    """Parse a JSON object from an LLM response, tolerating code fences."""
    text = content.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    block = _JSON_BLOCK_RE.search(text)
    if block:
        try:
            parsed = json.loads(block.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # Fall back to the first balanced {...} span.
    start = text.find("{")
    if start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : index + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
    raise ValueError(f"Could not extract a JSON object from the LLM response: {text[:500]!r}")


class LLMClient:
    """Thin wrapper around an OpenAI-compatible chat completions endpoint.

    Configuration resolution order: constructor argument > environment variable
    (``LLM_BASE_URL``, ``LLM_MODEL``, ``LLM_API_KEY``) > ``configs/llm.yaml``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        config_path: Path = DEFAULT_CONFIG,
    ) -> None:
        config = self._load_config(config_path)
        self.base_url = base_url or os.environ.get("LLM_BASE_URL") or config.get("base_url") or ""
        self.model = model or os.environ.get("LLM_MODEL") or config.get("model") or ""
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or config.get("api_key") or "not-needed"
        if not self.base_url or not self.model:
            raise ValueError(
                "LLM endpoint is not configured. Set the LLM_BASE_URL and LLM_MODEL "
                "environment variables (or pass --base-url/--model to run_answers.py)."
            )
        self.temperature = float(config.get("temperature", 0.2))
        self.max_tokens = int(config.get("max_tokens", 3000))
        self.timeout_seconds = float(config.get("timeout_seconds", 180))
        self.max_retries = int(config.get("max_retries", 3))
        self.enable_thinking = bool(config.get("enable_thinking", False))
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _load_config(config_path: Path) -> dict[str, Any]:
        if not Path(config_path).is_file():
            return {}
        with Path(config_path).open("r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        return (data.get("llm") or {}) if isinstance(data, dict) else {}

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        """Call the chat completions endpoint and return a parsed JSON object."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                request_options: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                if not self.enable_thinking:
                    # Qwen3 "thinking" models can spend the whole output budget on reasoning
                    # and return an empty content; vLLM disables it per request.
                    request_options["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
                response = self._client.chat.completions.create(**request_options)
                content = response.choices[0].message.content or ""
                return _extract_json(content)
            except Exception as exc:  # noqa: BLE001 - retry any transient failure
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)
        raise RuntimeError(f"LLM request failed after {self.max_retries} attempts: {last_error}") from last_error
