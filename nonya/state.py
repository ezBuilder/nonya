"""Session state vocabulary (string constants kept identical to the v1 bash port)."""

ERROR = "ERROR"            # API error / crash banner
RATE_LIMIT = "RATE_LIMIT"  # overload / 429 / 503 / "rate limit"
TOOL_PENDING = "TOOL_PENDING"  # mid tool-use / actively generating
COMPLETED = "COMPLETED"    # turn finished (end_turn / task_complete)
IDLE_WAIT = "IDLE_WAIT"    # quiet, no decisive marker
STALLED = "STALLED"        # turn started but never completed
UNKNOWN = "UNKNOWN"

ALL = {ERROR, RATE_LIMIT, TOOL_PENDING, COMPLETED, IDLE_WAIT, STALLED, UNKNOWN}
