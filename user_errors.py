"""Errors whose deliberately authored messages are safe to show to users."""


class UserError(ValueError):
    """An expected validation failure, containing no internal or secret data."""
