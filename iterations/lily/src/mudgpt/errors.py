"""Typed pre-write refusals, distinct from uncertain transport outcomes."""

PREWRITE_CODES = frozenset({
    "broker_changed", "generation_changed", "cursor_changed", "decision_expired",
    "not_connected", "controller_mismatch", "lease_mismatch", "lease_epoch_changed",
    "command_in_flight", "invalid_request",
})


class _Prewrite:
    delivery = "not_sent"

    def __init__(self, code, message):
        if code not in PREWRITE_CODES:
            raise ValueError("unregistered pre-write refusal code")
        self.code = code
        super().__init__(message)


class BrokerRefusal(_Prewrite, RuntimeError):
    pass


class PrewriteValueError(_Prewrite, ValueError):
    pass


class PrewritePermissionError(_Prewrite, PermissionError):
    pass


class PrewriteConnectionError(_Prewrite, ConnectionError):
    pass


def is_prewrite_error(payload):
    """Accept the versioned contract, never an English-message heuristic."""
    return (isinstance(payload, dict)
            and type(payload.get("error_schema")) is int
            and payload["error_schema"] == 1
            and isinstance(payload.get("error"), str)
            and isinstance(payload.get("error_code"), str)
            and payload["error_code"] in PREWRITE_CODES
            and payload.get("delivery") == "not_sent")


def error_payload(error):
    known = isinstance(error, _Prewrite)
    return {"error": str(error) or type(error).__name__, "error_schema": 1,
            "error_code": error.code if known else "unclassified_error",
            "delivery": "not_sent" if known else "unknown",
            "command_complete": False}


class BrokerRequestError(RuntimeError):
    """A broker error read by a client; legacy/unrecognized errors stay unknown."""
    def __init__(self, payload):
        recognized = is_prewrite_error(payload)
        self.code = payload["error_code"] if recognized else "unclassified_error"
        self.delivery = "not_sent" if recognized else "unknown"
        message = payload.get("error") if isinstance(payload, dict) else None
        super().__init__(message if isinstance(message, str) else "Invalid broker response; outcome unknown")

