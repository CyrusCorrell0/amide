"""Builtin tools. One module per tool; the registry imports them all.

Every module imports its heavy dependencies inside the tool function, never
at module level, so importing the package stays cheap and ``amide tools
check`` can report what is missing without failing.
"""
