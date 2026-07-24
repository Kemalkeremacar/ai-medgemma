"""Kuyruk backend soyutlaması.

Orkestratör ve worker yalnızca bu arayüze bağımlıdır; böylece Redis yerine
ileride başka bir backend (DB/SQS/RabbitMQ) takılabilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class QueueMessage:
    """Kuyruktan çekilen tek bir mesaj.

    ``receipt`` backend'e özgü, ack/retry için kullanılan tanıtıcıdır.
    """

    job_id: str
    payload: dict[str, Any]
    receipt: str
    attempts: int = 1
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class QueueBackend(Protocol):
    """Dayanıklı iş kuyruğu sözleşmesi."""

    def enqueue(self, job_id: str, payload: dict[str, Any]) -> bool:
        """İşi kuyruğa ekler. Zaten aktifse ``False`` döner."""

    def dequeue(self, timeout: int = 5) -> QueueMessage | None:
        """İşi atomik olarak çeker ve 'processing' durumuna alır.

        ``timeout`` saniye boyunca iş yoksa ``None`` döner.
        """

    def ack(self, message: QueueMessage) -> None:
        """İşi başarıyla tamamlandı olarak işaretler (processing'den siler)."""

    def retry(self, message: QueueMessage, *, max_retries: int) -> bool:
        """İşi yeniden kuyruğa alır. Limit aşıldıysa dead-letter'a atar.

        Yeniden kuyruğa alındıysa ``True``, dead-letter'a atıldıysa ``False``.
        """

    def store_result(self, job_id: str, result: dict[str, Any]) -> None:
        """İş sonucunu okunabilir şekilde saklar."""

    def get_result(self, job_id: str) -> dict[str, Any] | None:
        """Saklanan iş sonucunu döner (yoksa None)."""

    def queue_depth(self) -> dict[str, int]:
        """Kuyruk derinliklerini döner (pending/processing/dead)."""

    def ping(self) -> bool:
        """Backend erişilebilir mi?"""
