"""Task-local output sink for an agent-initiated terminal command."""
from contextvars import ContextVar

output_sink = ContextVar("terminal_output_sink", default=None)
