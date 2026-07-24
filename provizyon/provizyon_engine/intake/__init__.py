"""Provizyon alım (intake) katmanı.

Sağlık sisteminin "Hizmet Döküm Formu" (popup) PDF'inden ve klasördeki ekli
belgelerden otomatik olarak ``ProvizyonJob`` üretir. Böylece elle JSON yazmak
yerine gerçek provizyon kaynağı doğrudan kuyruğa bağlanabilir.
"""

from .popup_parser import PopupData, PopupDiagnosis, PopupProcedure, parse_popup_pdf
from .folder_intake import build_job_from_folder, find_popup_pdf
from .db_intake import fetch_pending_provizyonlar, fetch_provizyon

__all__ = [
    "PopupData",
    "PopupDiagnosis",
    "PopupProcedure",
    "parse_popup_pdf",
    "build_job_from_folder",
    "find_popup_pdf",
    "fetch_provizyon",
    "fetch_pending_provizyonlar",
]
