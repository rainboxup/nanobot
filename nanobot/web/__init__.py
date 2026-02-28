"""Web (SaaS) layer for nanobot.

This package provides a FastAPI server with:
- JWT auth for the dashboard/APIs
- REST APIs for config management (providers/channels)
- WebSocket chat bridged to the existing MessageBus
- Static dashboard assets (vanilla JS)
"""

from nanobot.web.server import create_app

__all__ = ["create_app"]

