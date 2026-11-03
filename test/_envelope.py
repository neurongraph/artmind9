"""Compare CLI payloads while ignoring the envelope `_echo_json` adds.

Every JSON result carries `command`, `warnings` and `next` (artmind/cli_conventions.py).
A test pinning what a command *built* compares through `plain()`, which drops
those three keys when they are empty/derived; one pinning the envelope asserts
on the keys directly (test_cli_conventions_unit.py).
"""


def plain(payload):
    if not isinstance(payload, dict):
        return payload
    out = {k: v for k, v in payload.items() if k != "command"}
    if out.get("warnings") == []:
        out.pop("warnings")
    if out.get("next") is None:
        out.pop("next", None)
    return out
