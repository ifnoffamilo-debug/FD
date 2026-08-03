"""Compatibility module for installations upgraded from versions 1.0–1.1."""

from app.services.ai import AIService, ParsedOperation

KimiService = AIService

__all__ = ["AIService", "KimiService", "ParsedOperation"]
