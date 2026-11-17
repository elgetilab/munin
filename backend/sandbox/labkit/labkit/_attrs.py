"""Attribute errors that name the fields an object does have.

In Phase 4 run 3 the model processed a Bruker spectrum with labkit, then
guessed `spec.ppmm` and `spec.ppmin`; the plain AttributeError did not say
what exists, it reran the same call and ran out of turns.
"""
from __future__ import annotations

import difflib


class FieldHelp:
    """Mixin: a missing attribute raises with a close match and the field list."""

    def __getattr__(self, name: str):
        # Only reached when normal lookup fails. Dunder lookups (copy,
        # pickle, numpy protocols) must fail plainly and fast.
        if name.startswith("__"):
            raise AttributeError(name)
        cls = type(self)
        fields = [f for f in getattr(cls, "__dataclass_fields__", {}) if not f.startswith("_")]
        methods = [m for m in dir(cls) if not m.startswith("_") and m not in fields]
        known = fields + sorted(methods)
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        hint = f"; did you mean {close[0]!r}?" if close else "."
        raise AttributeError(f"{cls.__name__} has no attribute {name!r}{hint} "
                             f"It has: {', '.join(known)}")
