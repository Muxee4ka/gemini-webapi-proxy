"""The outbound proxy of the account serving the current request.

Set by the pool around a call (see ``client/pool.py``) so that everything that call
does with Google - the Gemini RPCs, image and video downloads - leaves through the
same address the account signed in from.
"""

from __future__ import annotations

from contextvars import ContextVar

egress: ContextVar[str | None] = ContextVar("gop_egress", default=None)
