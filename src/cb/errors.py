"""Business error codes returned to MCP clients (spec §5.3)."""

FORBIDDEN = "FORBIDDEN"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
INVALID_TRANSITION = "INVALID_TRANSITION"
INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
CONFLICT = "CONFLICT"
INTERNAL = "INTERNAL"


class ServiceError(Exception):
    """A business-rule failure. Raising it inside a write transaction rolls it back."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}