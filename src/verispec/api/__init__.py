"""FastAPI HTTP control plane for VeriSpec-SDD (Port 8200)."""

from __future__ import annotations

from verispec.api.app import app, create_app

__all__ = ["app", "create_app"]
