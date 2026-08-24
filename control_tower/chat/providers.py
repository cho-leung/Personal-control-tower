"""Provider-neutral structured completion backends for chat understanding."""

from abc import ABC, abstractmethod
import json
from typing import Mapping
from urllib import error, request


class LLMProviderError(RuntimeError):
    """A remote LLM provider failed without producing trusted output."""


class LLMProvider(ABC):
    """Generate schema-constrained text without any Control Tower tools."""

    @abstractmethod
    def generate_structured(
        self,
        *,
        message: str,
        instructions: str,
        schema: Mapping,
    ) -> str:
        raise NotImplementedError


class _RejectRedirects(request.HTTPRedirectHandler):
    """Never forward an Authorization header through an API redirect."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


class OpenAIResponsesProvider(LLMProvider):
    """OpenAI Responses API backend using a fixed official endpoint."""

    ENDPOINT = "https://api.openai.com/v1/responses"
    MAX_RESPONSE_BYTES = 1024 * 1024

    def __init__(
        self,
        api_key,
        model,
        timeout_seconds=30.0,
        transport=None,
    ):
        self._api_key = api_key
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self._transport = (
            transport if transport is not None else self._post_json
        )

    @classmethod
    def _post_json(cls, url, headers, payload, timeout):
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        outbound = request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )

        opener = request.build_opener(_RejectRedirects())

        try:
            with opener.open(outbound, timeout=timeout) as response:
                raw = response.read(cls.MAX_RESPONSE_BYTES + 1)
        except error.HTTPError as exc:
            raise LLMProviderError(
                f"OpenAI request failed with HTTP {exc.code}."
            ) from exc
        except (error.URLError, OSError, TimeoutError) as exc:
            raise LLMProviderError(
                "OpenAI request failed."
            ) from exc

        if len(raw) > cls.MAX_RESPONSE_BYTES:
            raise LLMProviderError(
                "OpenAI response exceeded the safe size limit."
            )

        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LLMProviderError(
                "OpenAI returned an invalid response."
            ) from exc

        if not isinstance(result, dict):
            raise LLMProviderError(
                "OpenAI returned an invalid response object."
            )

        return result

    @staticmethod
    def _output_text(response):
        if (
            not isinstance(response, Mapping)
            or response.get("status") != "completed"
            or response.get("error")
        ):
            raise LLMProviderError(
                "OpenAI did not complete the structured response."
            )

        output = response.get("output", [])

        if not isinstance(output, list):
            raise LLMProviderError(
                "OpenAI returned an invalid output collection."
            )

        texts = []

        for item in output:
            if not isinstance(item, Mapping):
                raise LLMProviderError(
                    "OpenAI returned an invalid output item."
                )

            if item.get("type") != "message":
                continue

            content_items = item.get("content")

            if not isinstance(content_items, list):
                raise LLMProviderError(
                    "OpenAI returned invalid message content."
                )

            for content in content_items:
                if not isinstance(content, Mapping):
                    raise LLMProviderError(
                        "OpenAI returned an invalid content item."
                    )

                if content.get("type") == "refusal":
                    raise LLMProviderError(
                        "OpenAI refused the structured response."
                    )

                if content.get("type") == "output_text":
                    text = content.get("text")

                    if isinstance(text, str):
                        texts.append(text)

        if len(texts) != 1:
            raise LLMProviderError(
                "OpenAI returned no unique structured output."
            )

        return texts[0]

    def generate_structured(
        self,
        *,
        message: str,
        instructions: str,
        schema: Mapping,
    ) -> str:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": message,
            "store": False,
            "max_output_tokens": 1200,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "control_tower_intent",
                    "strict": True,
                    "schema": dict(schema),
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "personal-control-tower/3.0-alpha.1",
        }

        try:
            response = self._transport(
                self.ENDPOINT,
                headers,
                payload,
                self.timeout_seconds,
            )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(
                "OpenAI provider transport failed."
            ) from exc

        return self._output_text(response)
