"""Tanı-işlem geçmiş ödeme eğilimi sinyali katmanı (Adım 9b).

Mevcut HUV/SUT/tanı kural kararı bozulmadan, provizyon işlem satırındaki
``payment_procedure_code`` / ``payment_procedure_name`` alanlarıyla Qdrant
``diagnosis_procedure_pilot`` collection'ından geçmiş ödeme eğilimi sinyali arar.

Akış:
  1. Kurum + tanı + ödeme işlem kodu/adından sorgu metni üret.
  2. TEI embedding + Qdrant vektör araması (aday getirme).
  3. Strict post-filter: kurum eşleşsin, tanı kökü eşleşsin, işlem kodu/adı eşleşsin.
  4. Red/orange sinyalde ``diagnosis_payment_tendency`` RiskReason üret.
  5. Kararı gerekiyorsa orange / manual_review / manuel_inceleme'ye yükselt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..models import (
    DecisionType,
    KararDurumu,
    ProvizyonJob,
    RiskLevel,
    RiskReason,
)
from ..risk_normalizer import RISK_LEVEL_PRIORITY, _DT_PRIORITY

_LAYER = "diagnosis_payment_tendency"
_SIGNAL_RISK_LEVELS = {"red", "orange"}
_WS_RE = re.compile(r"\s+")


def _norm(value: Any) -> str:
    """Kurum/isim karşılaştırması için normalize eder (upper + boşluk sadeleştir)."""

    if value is None:
        return ""
    text = _WS_RE.sub(" ", str(value).strip())
    return text.upper()


def _dx_root(code: Any) -> str:
    """ICD-10 kök kodu (nokta öncesi), ör. ``I69.3 -> I69``."""

    if code is None:
        return ""
    return str(code).split(".")[0].strip().upper()


def _payment_fields(proc: Any) -> tuple[str, str]:
    """İşlem satırından ``payment_procedure_code`` / ``payment_procedure_name`` okur."""

    extra = getattr(proc, "model_extra", None) or {}
    code = extra.get("payment_procedure_code")
    name = extra.get("payment_procedure_name")
    return (str(code).strip() if code else "", str(name).strip() if name else "")


def _build_query_text(institution: str, dx_code: str, pay_code: str, pay_name: str) -> str:
    parts = ["Sinyal: Tanı-işlem ödeme eğilimi"]
    if institution:
        parts.append(f"Kurum: {institution}")
    if dx_code:
        parts.append(f"Tanı: {dx_code}")
    proc_bits = " ".join(bit for bit in (pay_code, pay_name) if bit)
    if proc_bits:
        parts.append(f"İşlem: {proc_bits}")
    return "\n".join(parts)


def _match_procedure(
    payload: dict[str, Any], pay_code: str, pay_name: str
) -> dict[str, Any] | None:
    """İşlem kodu/adı eşleşmesini üst seviyede veya ``top_procedures`` içinde arar."""

    pay_code_n = pay_code.strip()
    pay_name_n = _norm(pay_name)

    top_code = str(payload.get("procedure_code") or "").strip()
    top_name = _norm(payload.get("procedure_name"))
    if (pay_code_n and top_code and top_code == pay_code_n) or (
        pay_name_n and top_name and top_name == pay_name_n
    ):
        return {
            "procedure_code": top_code or pay_code_n,
            "procedure_name": payload.get("procedure_name") or pay_name,
        }

    for entry in payload.get("top_procedures") or []:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("procedure_code") or "").strip()
        name = _norm(entry.get("procedure_name"))
        if (pay_code_n and code and code == pay_code_n) or (
            pay_name_n and name and name == pay_name_n
        ):
            return {
                "procedure_code": code or pay_code_n,
                "procedure_name": entry.get("procedure_name") or pay_name,
            }
    return None


def _signal_matches(
    payload: dict[str, Any],
    *,
    institution: str,
    dx_roots: set[str],
    pay_code: str,
    pay_name: str,
) -> dict[str, Any] | None:
    """Strict post-filter; eşleşirse matched procedure bilgisini döner."""

    if not _norm(payload.get("institution_name")) == _norm(institution):
        return None
    if _dx_root(payload.get("diagnosis_code")) not in dx_roots:
        return None
    return _match_procedure(payload, pay_code, pay_name)


def _to_risk_reason(
    payload: dict[str, Any], matched: dict[str, Any], std_code: str
) -> RiskReason | None:
    """Red/orange sinyalden RiskReason üretir; aksi halde None."""

    risk_level = str(payload.get("risk_level") or "").strip().lower()
    if risk_level not in _SIGNAL_RISK_LEVELS:
        return None

    signal_type = str(payload.get("signal_type") or "diagnosis_payment_overlay")
    institution = payload.get("institution_name") or ""
    dx_code = payload.get("diagnosis_code") or ""
    proc_code = matched.get("procedure_code") or ""
    sample_size = payload.get("sample_size")
    rejected_cases = payload.get("rejected_cases")
    case_rej = payload.get("case_rejection_rate")
    amount_rej = payload.get("amount_rejection_rate")

    message = (
        f"Geçmiş ödeme eğilimi: {institution} için {dx_code} tanısı ve {proc_code} "
        f"işleminde {sample_size} örneklemde {rejected_cases} red vaka görülmüş. "
        f"Tutar red oranı {amount_rej}."
    )

    return RiskReason(
        code=std_code or proc_code,
        layer=_LAYER,
        rule_trigger=signal_type,
        message=message,
        action=(
            "Manuel inceleme önerilir; ödeme kurumunun bu tanı-işlem sepetindeki "
            "geçmiş red eğilimi kontrol edilmelidir."
        ),
        decision_type=DecisionType.MANUAL_REVIEW,
        risk_level=RiskLevel(risk_level),
        signal_type=signal_type,
        diagnosis_code=dx_code,
        procedure_code=proc_code,
        sample_size=sample_size,
        rejected_cases=rejected_cases,
        case_rejection_rate=case_rej,
        amount_rejection_rate=amount_rej,
    )


def collect_diagnosis_payment_signals(
    job: ProvizyonJob, *, reader: Any | None = None
) -> tuple[list[RiskReason], list[dict[str, Any]]]:
    """İşlem satırlarındaki ödeme kodlarıyla geçmiş ödeme sinyallerini toplar.

    Döner: (red/orange RiskReason listesi, eşleşen sinyal payload özetleri).
    """

    institution = ""
    extra = getattr(job, "model_extra", None) or {}
    if extra.get("institution_name"):
        institution = str(extra.get("institution_name")).strip()
    if not institution:
        return [], []

    dx_roots = {_dx_root(dx) for dx in job.diagnoses if dx}
    dx_roots.discard("")
    if not dx_roots:
        return [], []

    targets: list[tuple[str, str, str]] = []  # (std_code, pay_code, pay_name)
    for proc in job.procedures:
        pay_code, pay_name = _payment_fields(proc)
        if not pay_code and not pay_name:
            continue
        targets.append((str(proc.code or "").strip(), pay_code, pay_name))
    if not targets:
        return [], []

    if reader is None:
        from ..persistence.diagnosis_payment_qdrant import DiagnosisPaymentSignalReader

        reader = DiagnosisPaymentSignalReader()

    reasons: list[RiskReason] = []
    signals: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for std_code, pay_code, pay_name in targets:
        for dx_code in sorted(dx_roots):
            query = _build_query_text(institution, dx_code, pay_code, pay_name)
            candidates = reader.search_candidates(query)
            for payload in candidates:
                matched = _signal_matches(
                    payload,
                    institution=institution,
                    dx_roots=dx_roots,
                    pay_code=pay_code,
                    pay_name=pay_name,
                )
                if matched is None:
                    continue
                dedupe_key = (
                    str(payload.get("signal_type") or ""),
                    _dx_root(payload.get("diagnosis_code")),
                    str(matched.get("procedure_code") or ""),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                signals.append(
                    {
                        "signal_type": payload.get("signal_type"),
                        "institution_name": payload.get("institution_name"),
                        "diagnosis_code": payload.get("diagnosis_code"),
                        "procedure_code": matched.get("procedure_code"),
                        "procedure_name": matched.get("procedure_name"),
                        "sample_size": payload.get("sample_size"),
                        "rejected_cases": payload.get("rejected_cases"),
                        "case_rejection_rate": payload.get("case_rejection_rate"),
                        "amount_rejection_rate": payload.get("amount_rejection_rate"),
                        "risk_level": payload.get("risk_level"),
                        "risk_score": payload.get("risk_score"),
                        "matched_payment_procedure_code": pay_code or None,
                        "matched_payment_procedure_name": pay_name or None,
                        "score": payload.get("_score"),
                    }
                )
                reason = _to_risk_reason(payload, matched, std_code)
                if reason is not None:
                    reasons.append(reason)

    return reasons, signals


@dataclass
class DiagnosisPaymentDecision:
    karar: KararDurumu
    decision_type: DecisionType | None
    risk_level: RiskLevel | None
    risk_reasons: list[RiskReason] = field(default_factory=list)


def _sort_reasons(reasons: list[RiskReason]) -> list[RiskReason]:
    # Eşit risk_level + decision_type durumunda geçmiş ödeme sinyali öne alınır;
    # böylece /queue/recent risk_summary (ilk en yüksek öncelikli reason) bu sinyali gösterir.
    return sorted(
        reasons,
        key=lambda r: (
            RISK_LEVEL_PRIORITY.get(r.risk_level, 0),
            _DT_PRIORITY.get(r.decision_type, 0),
            1 if r.layer == _LAYER else 0,
        ),
        reverse=True,
    )


def apply_diagnosis_payment_signals(
    *,
    karar: KararDurumu,
    decision_type: DecisionType | None,
    risk_level: RiskLevel | None,
    risk_reasons: list[RiskReason],
    signal_reasons: list[RiskReason],
) -> DiagnosisPaymentDecision:
    """Geçmiş ödeme sinyallerini nihai karara işler.

    - Mevcut karar red ise red kalır; sinyaller yalnızca audit için eklenir.
    - Green/low_risk ama sinyal orange/red ise: risk_level>=orange, manual_review,
      nihai_karar manuel_inceleme.
    - risk_reasons yeniden sıralanır (risk_level, decision_type).
    """

    merged = list(risk_reasons) + list(signal_reasons)

    if not signal_reasons:
        return DiagnosisPaymentDecision(
            karar=karar,
            decision_type=decision_type,
            risk_level=risk_level,
            risk_reasons=_sort_reasons(merged),
        )

    signal_level = max(
        (r.risk_level for r in signal_reasons),
        key=lambda rl: RISK_LEVEL_PRIORITY.get(rl, 0),
    )

    new_karar = karar
    new_dt = decision_type
    new_rl = risk_level

    current_pri = RISK_LEVEL_PRIORITY.get(risk_level, 0) if risk_level else 0
    signal_pri = RISK_LEVEL_PRIORITY.get(signal_level, 0)

    # Mevcut karar zaten red ise (savunulabilir red) korunur.
    if current_pri >= RISK_LEVEL_PRIORITY[RiskLevel.RED]:
        return DiagnosisPaymentDecision(
            karar=new_karar,
            decision_type=new_dt,
            risk_level=new_rl,
            risk_reasons=_sort_reasons(merged),
        )

    if signal_pri > current_pri:
        new_rl = signal_level

    if decision_type != DecisionType.AUTOMATIC_DEFENSIBLE:
        new_dt = DecisionType.MANUAL_REVIEW
        new_karar = KararDurumu.MANUEL_INCELEME

    return DiagnosisPaymentDecision(
        karar=new_karar,
        decision_type=new_dt,
        risk_level=new_rl,
        risk_reasons=_sort_reasons(merged),
    )
