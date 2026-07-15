"""RabbitMQ topolojisi (ADR-002 §5).

Exchange, queue ve routing key isimleri contract'in parcasidir. Environment
prefix'i (dev./staging./prod.) alabilir ancak payload contract'i degismez.
Tum exchange'ler durable topic; queue'lar durable; mesajlar persistent
(ADR-007 §13, §37).
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Exchange'ler (§5.1) ---
EXCHANGE_COMMANDS = "m4trust.ai.commands"
EXCHANGE_EVENTS = "m4trust.ai.events"
EXCHANGE_DEAD_LETTER = "m4trust.ai.dead-letter"

# --- Command routing key'leri (§5.2) — worker bunlari dinler ---
RK_DOC_REQUESTED = "ai.document-extraction.requested.v1"
RK_VIDEO_REQUESTED = "ai.video-analysis.requested.v1"
RK_CANCEL_REQUESTED = "ai.job.cancel.requested.v1"

# --- Result routing key'leri (§5.3) — worker bunlara basar ---
RK_DOC_COMPLETED = "ai.document-extraction.completed.v1"
RK_DOC_FAILED = "ai.document-extraction.failed.v1"
RK_VIDEO_COMPLETED = "ai.video-analysis.completed.v1"
RK_VIDEO_FAILED = "ai.video-analysis.failed.v1"

# --- Queue isimleri (§5.4) — FastAPI tarafi ---
QUEUE_DOC = "m4trust.ai.document-extraction.v1"
QUEUE_VIDEO = "m4trust.ai.video-analysis.v1"
QUEUE_CANCEL = "m4trust.ai.cancellation.v1"
QUEUE_DEAD_LETTER = "m4trust.ai.dead-letter.v1"


@dataclass(frozen=True)
class QueueBinding:
    queue: str
    routing_key: str


# Worker'in tuketecegi queue -> command routing key baglantilari.
COMMAND_BINDINGS: tuple[QueueBinding, ...] = (
    QueueBinding(QUEUE_DOC, RK_DOC_REQUESTED),
    QueueBinding(QUEUE_VIDEO, RK_VIDEO_REQUESTED),
    QueueBinding(QUEUE_CANCEL, RK_CANCEL_REQUESTED),
)

# jobType -> (completed, failed) result routing key.
RESULT_ROUTING: dict[str, tuple[str, str]] = {
    "DOCUMENT_EXTRACTION": (RK_DOC_COMPLETED, RK_DOC_FAILED),
    "VIDEO_ANALYSIS": (RK_VIDEO_COMPLETED, RK_VIDEO_FAILED),
}


def declare_topology(channel) -> None:
    """Exchange, queue ve binding'leri idempotent olarak tanimlar.

    Butun tanimlar durable; dead-letter exchange her command queue'suna baglanir.
    """
    channel.exchange_declare(EXCHANGE_COMMANDS, exchange_type="topic", durable=True)
    channel.exchange_declare(EXCHANGE_EVENTS, exchange_type="topic", durable=True)
    channel.exchange_declare(EXCHANGE_DEAD_LETTER, exchange_type="topic", durable=True)

    channel.queue_declare(QUEUE_DEAD_LETTER, durable=True)
    channel.queue_bind(QUEUE_DEAD_LETTER, EXCHANGE_DEAD_LETTER, routing_key="#")

    dlx_args = {"x-dead-letter-exchange": EXCHANGE_DEAD_LETTER}
    for binding in COMMAND_BINDINGS:
        channel.queue_declare(binding.queue, durable=True, arguments=dlx_args)
        channel.queue_bind(binding.queue, EXCHANGE_COMMANDS, routing_key=binding.routing_key)
