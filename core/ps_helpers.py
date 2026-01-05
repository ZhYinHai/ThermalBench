import re

RUNMAP_RE = re.compile(r"RUN MAP:\s*(.+)$")


def ps_quote(s: str) -> str:
    # Safe for PowerShell single-quoted strings
    return "'" + str(s).replace("'", "''") + "'"


def build_ps_array_literal(items: list[str]) -> str:
    """
    For -Command usage: returns "'a','b','c'" so PowerShell binds it to [string[]] param.
    """
    return ",".join(ps_quote(x) for x in items)
