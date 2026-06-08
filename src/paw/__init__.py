# -*- coding: utf-8 -*-
"""paw — a terminal chat UI for QwenPaw.

A thin Textual front-end that drives an existing QwenPaw installation over
ACP (Agent Client Protocol) by spawning ``qwenpaw acp``.

paw never imports the QwenPaw backend — it only speaks ACP — so it stays
a light, independently-released client. Provider keys and model selection are
managed by QwenPaw itself, not by paw.
"""

from __future__ import annotations

from .__version__ import __version__

__all__ = ["__version__"]
