# -*- coding: utf-8 -*-
"""paw — a terminal chat UI for QwenPaw.

A thin Textual front-end that drives a QwenPaw agent over ACP (Agent Client
Protocol). It can run a local QwenPaw (`qwenpaw acp`) or a bundled one
installed via ``paw[bundled]``.

paw never imports the QwenPaw backend — it only speaks ACP — so it stays
a light, independently-released client.
"""

from __future__ import annotations

from .__version__ import __version__

__all__ = ["__version__"]
