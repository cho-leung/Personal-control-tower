"""One-shot and interactive terminal shell for the read-only chat slice."""

import sys

from .adapters import DeterministicIntentAdapter, LLMAdapterError
from .models import IntentValidationError
from .query import ChatQueryError, ControlTowerQueryService
from .service import ConversationalChiefOfStaff


EXIT_WORDS = frozenset(
    {"exit", "quit", "/exit", "/quit", "退出", "结束"}
)


def build_chat_service(vault_path, adapter=None):
    return ConversationalChiefOfStaff(
        adapter=adapter or DeterministicIntentAdapter(),
        query_service=ControlTowerQueryService(vault_path),
    )


def _respond(service, message, output_stream, error_stream):
    try:
        response = service.respond(message)
    except KeyboardInterrupt:
        error_stream.write(
            "Chat interrupted; no action was taken.\n"
        )
        return 130
    except (
        ChatQueryError,
        IntentValidationError,
        LLMAdapterError,
        TypeError,
        ValueError,
    ) as exc:
        error_stream.write(
            "Chat unavailable; no action was taken: "
            f"{exc}\n"
        )
        return 2

    output_stream.write(response.rstrip() + "\n")
    return 0


def run_chat(
    vault_path,
    message=None,
    adapter=None,
    input_stream=None,
    output_stream=None,
    error_stream=None,
):
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    error_stream = error_stream or sys.stderr
    service = build_chat_service(vault_path, adapter=adapter)

    if message is not None:
        return _respond(
            service,
            message,
            output_stream,
            error_stream,
        )

    output_stream.write(
        "Personal Control Tower v3-alpha｜Chief of Staff\n"
        "Milestone 1 is read-only. Type help or exit.\n"
    )

    while True:
        output_stream.write("ROOT> ")
        output_stream.flush()
        try:
            line = input_stream.readline()
        except KeyboardInterrupt:
            output_stream.write(
                "\nChief of Staff｜已中断；未执行任何操作。\n"
            )
            return 130
        except OSError as exc:
            error_stream.write(
                "Chat unavailable; no action was taken: "
                f"{exc}\n"
            )
            return 2

        if line == "":
            output_stream.write("\n")
            return 0

        message = line.strip()

        if message.casefold() in EXIT_WORDS:
            output_stream.write("Chief of Staff｜已退出。\n")
            return 0

        if not message:
            continue

        status = _respond(
            service,
            message,
            output_stream,
            error_stream,
        )

        if status != 0:
            return status
