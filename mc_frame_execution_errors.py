"""Execution page lookup outcomes shared by desktop and remote views."""

class ExecutionPagePending(Exception):
    """A bounded history page is being completed off the GUI thread."""


class ExecutionIdentityBlocked(Exception):
    """An ambiguous legacy insertion cannot be projected safely."""
