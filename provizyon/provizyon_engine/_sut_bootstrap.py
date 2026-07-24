"""SUT/HUV kütüphanelerini (``lib/``) import edilebilir hale getirir.

Kütüphaneler kendi paket köküne göre absolute import kullandığı için
``SUT_ROOT`` (``provizyon/lib/``) dizinini ``sys.path``'e eklemek yeterlidir.
Bu modül import edilince yan etki olarak path'i kurar.
"""

from __future__ import annotations

import sys

from . import settings

_SUT_ROOT_STR = str(settings.SUT_ROOT)
if settings.SUT_ROOT.exists() and _SUT_ROOT_STR not in sys.path:
    sys.path.insert(0, _SUT_ROOT_STR)
