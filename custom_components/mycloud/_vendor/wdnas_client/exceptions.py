class InvalidLoginError(Exception):
    """Authentication failed, optionally with a safe HTTP status code."""

    def __init__(self, message="Authentication failed", status_code=None):
        self.status_code = status_code
        super().__init__(message)


class SessionEstablishmentError(Exception):
    """The NAS accepted login but did not establish a usable client session."""

    def __init__(self, cleanup_error=None):
        self.cleanup_error = cleanup_error
        detail = (
            " and server-side cleanup did not complete"
            if cleanup_error is not None
            else ""
        )
        super().__init__(f"NAS session establishment failed{detail}")


class RequestFailedError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        self.message = f"Request failed: {status_code}"
        super().__init__(self.message)


class PowerActionBlockedError(Exception):
    """Raised when the NAS reports that a power action is unsafe."""

    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(
            f"NAS power action blocked by cgi_Detect_Dangerous: {status_code}"
        )


class UptimeUnavailableError(RequestFailedError):
    """Raised when a device does not expose uptime through the WD API."""

    def __init__(self, status_code=None):
        self.status_code = status_code
        detail = (
            f"HTTP {status_code} with no usable uptime response"
            if status_code is not None
            else "the response did not contain a supported uptime value"
        )
        self.message = f"Device uptime is unavailable: {detail}"
        Exception.__init__(self, self.message)
