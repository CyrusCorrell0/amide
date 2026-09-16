"""Exceptions raised by the harness. Every one carries a message fit to print."""

from __future__ import annotations


class HarnessError(Exception):
    """Base class; the CLI prints ``str(error)`` and exits non-zero."""


class ProtocolError(HarnessError):
    """A protocol file is malformed or references something that does not exist."""


class ExpressionError(HarnessError):
    """A ``{{ }}`` template or a check expression could not be evaluated."""


class ValidationError(HarnessError):
    """Inputs handed to a tool do not match its manifest."""


class ToolError(HarnessError):
    """A tool ran and failed."""


class MissingRequirements(HarnessError):
    """A tool's declared requirements are not met on this machine."""

    def __init__(self, tool: str, missing: list[str], hint: str = "") -> None:
        self.tool = tool
        self.missing = missing
        self.hint = hint
        text = f"{tool} needs {', '.join(missing)}"
        super().__init__(f"{text}. {hint}" if hint else text)


class ApprovalRequired(HarnessError):
    """An expensive step was reached without anyone to approve it."""


class BudgetExceeded(HarnessError):
    """The run's wall-clock budget is spent."""
