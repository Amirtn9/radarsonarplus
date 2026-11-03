"""Parse legacy server action callback IDs with one strict and reusable contract."""
import re

# act_COMMAND_ID or act_ID_COMMAND. No fallback to arbitrary numbers in a payload.
_ACTION_FIRST = re.compile(r"^act_([a-z][a-z0-9]*)_([1-9][0-9]*)(?:_.*)?$")
_ID_FIRST = re.compile(r"^act_([1-9][0-9]*)_([a-z][a-z0-9]*)(?:_.*)?$")


def parse_server_action(data: str):
    """Return (action, server_id) or None for an invalid/unsupported shape."""
    if not isinstance(data, str) or len(data) > 120:
        return None
    match = _ACTION_FIRST.fullmatch(data)
    if match:
        return match.group(1), int(match.group(2))
    match = _ID_FIRST.fullmatch(data)
    if match:
        return match.group(2), int(match.group(1))
    return None
