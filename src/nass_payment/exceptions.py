"""Exceptions raised by the Nass payment client.

Kept dependency-free so ``client.py`` can be copied into any project.
"""


class NassError(Exception):
    """Base class for every Nass payment error."""


class NassAuthError(NassError):
    """Raised when logging in to Nass for an access token fails."""


class NassAPIError(NassError):
    """Raised when a Nass API call fails or returns an error response."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
