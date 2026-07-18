"""`deadlineAt` semantik dogrulamasi (ADR-002 §12; Berke review #5).

Schema yalniz formati dogrular (RFC 3339 UTC, `contracts/schemas/common/
utc-timestamp`); suresi gecmis bir deadline'in reddedilmesi ayri bir
runtime kontroldur -- aksi halde suresi gecmis bir job icin pahali provider
cagrisi (LLM/Roboflow) bosuna yapilir.

Pipeline'lar bu kontrolu cancellation checkpoint'leriyle (check_cancelled)
AYNI noktalarda yapar: indirmeden once ve pahali provider cagrisindan hemen
once. `run_with_retry` her denemede pipeline'i BASTAN calistirdigi icin, bu
checkpoint retry'lar arasinda da dogal olarak tekrar degerlendirilir -- ayrica
bir "retry oncesi kontrol" kod yolu gerekmez.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.contracts.errors import ErrorCode, PipelineFailure


def check_deadline(deadline_at: str) -> None:
    """Deadline gecmisse stable `INVALID_DEADLINE` ile `PipelineFailure` firlatir."""
    deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > deadline:
        raise PipelineFailure(
            ErrorCode.INVALID_DEADLINE,
            "processing deadline has passed",
            details={"field": "payload.deadlineAt", "reason": "deadline is in the past"},
        )
