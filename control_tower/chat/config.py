"""Configuration for deterministic and remote chat understanding layers."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Mapping, Optional


class LLMConfigurationError(ValueError):
    """The selected LLM provider is missing safe, required settings."""


_CONFIG_KEYS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "OPENAI_MODEL",
    "OPENAI_API_KEY",
    "LLM_TIMEOUT_SECONDS",
)
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _optional_text(value, label):
    if value is None:
        return None

    if not isinstance(value, str):
        raise LLMConfigurationError(f"{label} must be text.")

    value = value.strip()

    if not value:
        return None

    if _CONTROL_RE.search(value):
        raise LLMConfigurationError(f"{label} is invalid.")

    return value


@dataclass(frozen=True)
class LLMSettings:
    """Resolved provider settings with secrets excluded from repr output."""

    provider: str = "offline"
    model: Optional[str] = None
    api_key: Optional[str] = field(default=None, repr=False)
    timeout_seconds: float = 30.0

    def __post_init__(self):
        provider = _optional_text(
            self.provider,
            "LLM provider",
        )

        if provider is None:
            raise LLMConfigurationError("LLM provider is required.")

        provider = provider.casefold()

        if provider == "deterministic":
            provider = "offline"

        if provider not in {"offline", "openai"}:
            raise LLMConfigurationError(
                f"Unsupported LLM provider: {provider}"
            )

        model = _optional_text(self.model, "LLM model")
        api_key = _optional_text(
            self.api_key,
            "OpenAI API key",
        )

        if model is not None and not _MODEL_RE.fullmatch(model):
            raise LLMConfigurationError("LLM model is invalid.")

        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not 1.0 <= float(self.timeout_seconds) <= 120.0
        ):
            raise LLMConfigurationError(
                "LLM timeout must be between 1 and 120 seconds."
            )

        if provider == "openai":
            if not model:
                raise LLMConfigurationError(
                    "OpenAI provider requires LLM_MODEL."
                )

            if not api_key:
                raise LLMConfigurationError(
                    "OpenAI provider requires OPENAI_API_KEY."
                )

        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(
            self,
            "timeout_seconds",
            float(self.timeout_seconds),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]):
        if not isinstance(values, Mapping):
            raise LLMConfigurationError(
                "LLM configuration must be a mapping."
            )

        timeout_value = values.get("LLM_TIMEOUT_SECONDS", "30")

        try:
            timeout_seconds = float(timeout_value)
        except (TypeError, ValueError) as exc:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be numeric."
            ) from exc

        return cls(
            provider=values.get("LLM_PROVIDER", "offline"),
            model=(
                values.get("LLM_MODEL")
                or values.get("OPENAI_MODEL")
            ),
            api_key=values.get("OPENAI_API_KEY"),
            timeout_seconds=timeout_seconds,
        )


def _read_dotenv(path: Path):
    path = Path(path)

    if not path.is_file():
        raise LLMConfigurationError(
            f"LLM config file does not exist: {path}"
        )

    try:
        if path.stat().st_size > 65536:
            raise LLMConfigurationError(
                "LLM config file is too large."
            )

        lines = path.read_text(encoding="utf-8").splitlines()
    except LLMConfigurationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise LLMConfigurationError(
            f"Cannot read LLM config file: {path}"
        ) from exc

    values = {}

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            raise LLMConfigurationError(
                f"Invalid LLM config line: {line_number}"
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not _KEY_RE.fullmatch(key):
            raise LLMConfigurationError(
                f"Invalid LLM config key on line {line_number}."
            )

        if key not in _CONFIG_KEYS:
            raise LLMConfigurationError(
                f"Unknown LLM config key: {key}"
            )

        if key in values:
            raise LLMConfigurationError(
                f"Duplicate LLM config key: {key}"
            )

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        if _CONTROL_RE.search(value):
            raise LLMConfigurationError(
                f"Invalid value for LLM config key: {key}"
            )

        values[key] = value

    return values


def load_llm_settings(
    config_path=None,
    environ=None,
    provider_override=None,
    model_override=None,
):
    """Load explicit dotenv values, then environment and CLI overrides."""

    values = _read_dotenv(config_path) if config_path else {}
    environment = os.environ if environ is None else environ

    if not isinstance(environment, Mapping):
        raise LLMConfigurationError(
            "Environment configuration must be a mapping."
        )

    for key in _CONFIG_KEYS:
        if key in environment:
            values[key] = environment[key]

    if provider_override is not None:
        values["LLM_PROVIDER"] = provider_override

    if model_override is not None:
        values["LLM_MODEL"] = model_override

    return LLMSettings.from_mapping(values)


def build_intent_adapter(settings: LLMSettings, provider=None):
    """Build the selected typed-intent adapter without touching the Vault."""

    if not isinstance(settings, LLMSettings):
        raise LLMConfigurationError(
            "Typed LLMSettings are required."
        )

    from .adapters import DeterministicIntentAdapter

    if settings.provider == "offline":
        if provider is not None:
            raise LLMConfigurationError(
                "A remote provider cannot be injected in offline mode."
            )

        return DeterministicIntentAdapter()

    from .providers import OpenAIResponsesProvider
    from .structured_intent import ProviderIntentAdapter

    backend = (
        provider
        if provider is not None
        else OpenAIResponsesProvider(
            api_key=settings.api_key,
            model=settings.model,
            timeout_seconds=settings.timeout_seconds,
        )
    )
    return ProviderIntentAdapter(backend)
