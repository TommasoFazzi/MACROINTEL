"""LLM Factory — tier-based client instantiation.

Usage:
    from src.llm.llm_factory import LLMFactory

    client = LLMFactory.get("t5")
    text = client.generate(prompt, max_tokens=100, json_mode=True)

Valid tiers: t1, t2, t3, t4a, t4b, t5
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Path to tier config
_ROUTING_YAML = Path(__file__).parent.parent.parent / "config" / "llm_routing.yaml"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseLLMClient(ABC):
    """Unified interface for all LLM providers."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        top_p: float | None = None,
        json_mode: bool = False,
        response_schema: type | dict | None = None,
        stop_sequences: list[str] | None = None,
    ) -> str:
        """Generate a text completion. Returns the response string."""

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | None = None,
        temperature: float = 0.3,
        top_p: float | None = None,
        max_tokens: int = 4096,
    ) -> Any:
        """Agentic multi-turn call with tool definitions. Returns raw provider response."""
        raise NotImplementedError(f"{self.__class__.__name__} does not support generate_with_tools()")


# ---------------------------------------------------------------------------
# Gemini client
# ---------------------------------------------------------------------------

class GeminiClient(BaseLLMClient):
    """Wraps google-generativeai SDK."""

    def __init__(self, model: str, timeout: int) -> None:
        import google.generativeai as genai
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set")
        genai.configure(api_key=api_key)
        self._model_name = model
        self._timeout = timeout
        self._genai = genai

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        top_p: float | None = None,
        json_mode: bool = False,
        response_schema: type | dict | None = None,
        stop_sequences: list[str] | None = None,
    ) -> str:
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if top_p is not None:
            generation_config["top_p"] = top_p
        if stop_sequences:
            generation_config["stop_sequences"] = stop_sequences

        if response_schema is not None:
            generation_config["response_mime_type"] = "application/json"
            generation_config["response_schema"] = response_schema
        elif json_mode:
            generation_config["response_mime_type"] = "application/json"

        system_instruction = system or None
        model = self._genai.GenerativeModel(
            self._model_name,
            system_instruction=system_instruction,
            generation_config=generation_config,
        )
        response = model.generate_content(
            prompt,
            request_options={"timeout": self._timeout},
        )
        return response.text

    def generate_content_raw(self, prompt: str, generation_config: dict) -> str:
        """Compatibility shim: passthrough to genai.GenerativeModel.generate_content().

        For gradual migration of report_generator.py call sites that pass complex
        generation_config dicts. Remove once all T1 call sites use generate().
        """
        model = self._genai.GenerativeModel(
            self._model_name,
            generation_config=generation_config,
        )
        response = model.generate_content(
            prompt,
            request_options={"timeout": self._timeout},
        )
        return response.text

    def __repr__(self) -> str:
        return f"GeminiClient(model={self._model_name!r})"


# ---------------------------------------------------------------------------
# Claude client (Anthropic)
# ---------------------------------------------------------------------------

class ClaudeClient(BaseLLMClient):
    """Wraps anthropic SDK."""

    def __init__(self, model: str, timeout: int) -> None:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model_name = model
        self._timeout = timeout

    def generate(
        self,
        prompt: str,
        system: str | list | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        top_p: float | None = None,
        json_mode: bool = False,
        response_schema: type | dict | None = None,
        stop_sequences: list[str] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if top_p is not None:
            kwargs["top_p"] = top_p
        if stop_sequences:
            kwargs["stop_sequences"] = stop_sequences
        # Prompt caching: list-form system triggers the beta header
        if isinstance(system, list):
            kwargs["extra_headers"] = {"anthropic-beta": "prompt-caching-2024-07-31"}

        response = self._client.messages.create(
            **kwargs,
            timeout=self._timeout,
        )
        return response.content[0].text

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str | list | None = None,
        temperature: float = 0.3,
        top_p: float | None = None,
        max_tokens: int = 4096,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tools": tools,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if top_p is not None:
            kwargs["top_p"] = top_p
        # Prompt caching: list-form system triggers the beta header
        if isinstance(system, list):
            kwargs["extra_headers"] = {"anthropic-beta": "prompt-caching-2024-07-31"}

        return self._client.messages.create(
            **kwargs,
            timeout=self._timeout,
        )

    def __repr__(self) -> str:
        return f"ClaudeClient(model={self._model_name!r})"


# ---------------------------------------------------------------------------
# OpenAI-compatible client (DeepSeek, Mistral, etc.)
# ---------------------------------------------------------------------------

class OpenAICompatibleClient(BaseLLMClient):
    """Wraps openai SDK with base_url override for OpenAI-compatible providers."""

    def __init__(self, model: str, base_url: str, timeout: int, api_key_env: str) -> None:
        from openai import OpenAI
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"{api_key_env} is not set")
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model_name = model
        self._timeout = timeout

    def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
        top_p: float | None = None,
        json_mode: bool = False,
        response_schema: type | dict | None = None,
        stop_sequences: list[str] | None = None,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})

        user_content = prompt
        if response_schema is not None:
            # Schema-in-prompt: inject JSON example for validation guidance
            schema_hint = self._build_schema_hint(response_schema)
            user_content = f"{prompt}\n\nRespond with valid JSON matching this schema:\n{schema_hint}"
            json_mode = True

        messages.append({"role": "user", "content": user_content})

        kwargs: dict[str, Any] = {
            "model": self._model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": self._timeout,
        }
        if top_p is not None:
            kwargs["top_p"] = top_p
        if stop_sequences:
            kwargs["stop"] = stop_sequences
        if json_mode or response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def generate_with_schema_retry(
        self,
        prompt: str,
        response_schema: type,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> str:
        """Generate with one retry on Pydantic validation failure.

        Returns the raw JSON string on success; raises ValidationError if both
        attempts fail.
        """
        import pydantic

        first_result = self.generate(
            prompt=prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
        )
        try:
            response_schema.model_validate_json(first_result)
            return first_result
        except (pydantic.ValidationError, Exception):
            logger.warning("T3 first attempt failed Pydantic validation — retrying with schema reminder")

        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        retry_prompt = (
            f"{prompt}\n\n"
            f"IMPORTANT: Your previous response failed JSON schema validation. "
            f"You MUST return valid JSON matching exactly this Pydantic schema:\n"
            f"```json\n{schema_json}\n```"
        )
        retry_result = self.generate(
            prompt=retry_prompt,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            response_schema=response_schema,
        )
        # Let ValidationError propagate if retry also fails
        response_schema.model_validate_json(retry_result)
        return retry_result

    @staticmethod
    def _build_schema_hint(response_schema: type | dict) -> str:
        """Build a JSON schema example string for prompt injection."""
        if isinstance(response_schema, dict):
            return json.dumps(response_schema, indent=2)
        try:
            return json.dumps(response_schema.model_json_schema(), indent=2)
        except AttributeError:
            return str(response_schema)

    def __repr__(self) -> str:
        return f"OpenAICompatibleClient(model={self._model_name!r})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_VALID_TIERS = {"t1", "t2", "t3", "t4a", "t4b", "t5"}

# API key env var per provider
_PROVIDER_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible_deepseek": "DEEPSEEK_API_KEY",
    "openai_compatible_mistral": "MISTRAL_API_KEY",
}

# Map base_url → env var name for OpenAI-compatible providers
_BASE_URL_KEY_ENV = {
    "https://api.deepseek.com": "DEEPSEEK_API_KEY",
    "https://api.mistral.ai/v1": "MISTRAL_API_KEY",
}


class LLMFactory:
    """Creates LLM client instances based on tier config in llm_routing.yaml."""

    _config: dict | None = None

    @classmethod
    def _load_config(cls) -> dict:
        if cls._config is None:
            with open(_ROUTING_YAML) as f:
                data = yaml.safe_load(f)
            cls._config = data["tiers"]
        return cls._config

    @classmethod
    def get(cls, tier: str) -> BaseLLMClient:
        """Return the LLM client for the given tier.

        Args:
            tier: One of t1, t2, t3, t4a, t4b, t5

        Raises:
            ValueError: If tier is invalid or required API key is missing.
        """
        if tier not in _VALID_TIERS:
            raise ValueError(
                f"Invalid LLM tier {tier!r}. Valid tiers: {sorted(_VALID_TIERS)}"
            )

        config = cls._load_config()
        tier_cfg = config[tier]
        provider = tier_cfg["provider"]
        model = tier_cfg["model"]
        timeout = tier_cfg.get("timeout", 60)

        if provider == "gemini":
            return GeminiClient(model=model, timeout=timeout)

        if provider == "anthropic":
            return ClaudeClient(model=model, timeout=timeout)

        if provider == "openai_compatible":
            base_url = tier_cfg["base_url"]
            api_key_env = _BASE_URL_KEY_ENV.get(base_url)
            if api_key_env is None:
                raise ValueError(
                    f"No API key env var mapped for base_url {base_url!r}. "
                    f"Add it to _BASE_URL_KEY_ENV in llm_factory.py."
                )
            return OpenAICompatibleClient(
                model=model,
                base_url=base_url,
                timeout=timeout,
                api_key_env=api_key_env,
            )

        raise ValueError(f"Unknown provider {provider!r} for tier {tier!r}")
