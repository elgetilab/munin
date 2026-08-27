"""
MCP Tool Executor.

Maps tool names to their implementations and handles execution.

Argument validation: before dispatch, ``execute_mcp_tool`` validates the
caller's ``arguments`` against the per-tool ``inputSchema`` declared in
``mcp/schemas.py``. Schema mismatches (wrong type, wrong shape, missing
required key, out-of-range integer) short-circuit with an ``{"error":
...}`` result so the model can self-correct on its next turn instead of
running a tool with garbage inputs. The gate is permissive on extra
unknown keys (the dispatcher already ignores them via
``arguments.get(...)``) so an over-eager model can't dead-end the turn
on a harmless extra field.

Validators compile once at module import; a malformed schema in
``MCP_TOOLS`` raises ``SchemaError`` at startup with a useful pointer
rather than failing the first user request that exercises it.
"""

import json
import logging
import time

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError

from metrics import observe_tool

from ._dispatch import get_dispatcher, verify_dispatch_registry  # noqa: F401
# Importing the dispatchers module triggers every @register_tool
# decorator at import time so the registry is populated before any
# request lands. main.py calls verify_dispatch_registry() at startup
# to catch schema/executor drift.
from . import dispatchers  # noqa: F401
from .schemas import MCP_TOOLS

logger = logging.getLogger(__name__)


def _build_validators() -> dict[str, Draft202012Validator]:
    """Compile a Draft 2020-12 validator per tool. Runs check_schema first
    so a typo in MCP_TOOLS (e.g. ``"type": "intgeer"``) blows up at import
    with a clear message instead of silently passing every payload."""
    out: dict[str, Draft202012Validator] = {}
    for name, meta in MCP_TOOLS.items():
        schema = meta.get("inputSchema")
        if not isinstance(schema, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as e:
            raise RuntimeError(
                f"MCP_TOOLS[{name!r}].inputSchema is not a valid "
                f"Draft 2020-12 schema: {e.message}"
            ) from e
        out[name] = Draft202012Validator(schema)
    return out


_VALIDATORS: dict[str, Draft202012Validator] = _build_validators()


# ---------------------------------------------------------------------------
# Argument type coercion (tool-call robustness)
# ---------------------------------------------------------------------------
# Models emit JSON-ish arguments, not JSON: `top_k: "5"` instead of 5,
# `filters: "year:2023"` instead of an object, `top_k: 5.0` instead of 5. The
# first two are rejected by the schema validator and burn a tool call; the
# THIRD is worse, because jsonschema accepts an integral float as "integer" and
# the call then dies deeper in with "slice indices must be integers", which
# reaches the model as an opaque `Tool execution failed`.
#
# MEASURED 2026-08-26: the `search` tool went from a 0.000 error rate on
# Qwen3.6-35B-A3B to 0.122 on Qwen3.8-27B with no code change between the runs.
# Every failure was one of these three shapes. Model-emitted argument types are
# a moving target, so normalise them here rather than per tool.
#
# Deliberately CONSERVATIVE: only widen when the schema names a scalar type and
# the value converts losslessly and unambiguously. A string is parsed as JSON
# for object/array targets only when it actually parses AND yields the declared
# type, so `"year:2023"` is still rejected (it is not JSON) while
# `'{"year":"2023"}'` is accepted. Anything ambiguous is left alone for the
# validator to reject with its clear message.
def _coerce_scalar(value, want: str):
    """Return a coerced value, or None if no safe coercion applies."""
    if want == "integer":
        if isinstance(value, bool):
            return None                      # bool is an int subclass; never widen
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            t = value.strip()
            try:
                return int(t)
            except ValueError:
                try:
                    f = float(t)
                except ValueError:
                    return None
                return int(f) if f.is_integer() else None
        return None
    if want == "number":
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None
    if want == "boolean":
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return None
    if want == "string":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        return None
    if want in ("object", "array"):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                return None
            if want == "object" and isinstance(parsed, dict):
                return parsed
            if want == "array" and isinstance(parsed, list):
                return parsed
        return None
    return None


def _declared_types(prop_schema: dict) -> list[str]:
    """Types a property accepts, flattening the `anyOf`/list spellings."""
    if not isinstance(prop_schema, dict):
        return []
    t = prop_schema.get("type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [x for x in t if isinstance(x, str)]
    out = []
    for sub in prop_schema.get("anyOf") or prop_schema.get("oneOf") or []:
        out.extend(_declared_types(sub))
    return out


def coerce_argument_types(tool_name: str, arguments: dict) -> dict:
    """Widen model-emitted argument types toward the tool's declared schema.

    Returns a NEW dict when anything changed, else `arguments` unchanged, so
    the no-coercion path stays allocation-free and behaviour-identical.
    """
    if not isinstance(arguments, dict) or not arguments:
        return arguments
    schema = (MCP_TOOLS.get(tool_name) or {}).get("inputSchema") or {}
    props = schema.get("properties")
    if not isinstance(props, dict):
        return arguments
    out = None
    for key, value in arguments.items():
        want = _declared_types(props.get(key) or {})
        if not want or value is None:
            continue
        # Already one of the declared types: leave it completely alone.
        if any(_matches(value, w) for w in want):
            continue
        for w in want:
            coerced = _coerce_scalar(value, w)
            if coerced is not None:
                if out is None:
                    out = dict(arguments)
                out[key] = coerced
                logger.info("coerced %s.%s from %s to %s", tool_name, key,
                            type(value).__name__, w)
                break
    return out if out is not None else arguments


def _matches(value, want: str) -> bool:
    if want == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if want == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if want == "boolean":
        return isinstance(value, bool)
    if want == "string":
        return isinstance(value, str)
    if want == "object":
        return isinstance(value, dict)
    if want == "array":
        return isinstance(value, list)
    if want == "null":
        return value is None
    return False


def partition_by_concurrency_safety(
    tool_calls: list[dict],
) -> tuple[list[tuple[int, dict]], list[tuple[int, dict]]]:
    """Split ``tool_calls`` into (safe, unsafe), each tagged with its
    original index so callers can reassemble output in declared order.

    A tool is unsafe iff its MCP_TOOLS entry declares
    ``is_concurrency_safe: False``. Default is safe — most tools are
    read-only (paper_search, web_search, etc.) and gather happily.
    Mutators (``create_artifact``, ``update_artifact``, ``run_python``,
    ``compile_latex``, ``remember``, ``forget``, ``sandbox_reset``,
    ``save_artifact_to_documents``) flip the flag so the dispatcher
    serialises them.

    The model can emit unsafe tools in any order; we run them in
    declared order so the resulting state matches what the model would
    expect from reading its own tool-call list.
    """
    safe: list[tuple[int, dict]] = []
    unsafe: list[tuple[int, dict]] = []
    for idx, tc in enumerate(tool_calls):
        meta = MCP_TOOLS.get(tc.get("name") or "", {})
        bucket = safe if meta.get("is_concurrency_safe", True) else unsafe
        bucket.append((idx, tc))
    return safe, unsafe


# Narrow, per-tool argument aliases applied BEFORE schema validation.
# The model occasionally calls an artifact tool with `id` (the key our
# create_artifact result returns) instead of the schema's `artifact_id`,
# and a rejected-but-otherwise-correct call can knock it off its plan
# (chat 5e27dfa4, 2026-06-16). Remap that one known slip rather than
# reject. Kept deliberately minimal (one alias, artifact tools only):
# broad synonym normalisation would silently mask real arg-naming bugs we
# would rather see surface in the validation_error metric. Add an entry
# only with a logged slip to justify it.
_ARG_ALIASES: dict[str, dict[str, str]] = {
    "update_artifact": {"id": "artifact_id"},
    "read_artifact": {"id": "artifact_id"},
    "save_artifact_to_documents": {"id": "artifact_id"},
}


def _normalize_aliases(tool_name: str, arguments: dict) -> dict:
    """Copy known alias keys onto their canonical names when the canonical
    key is absent. Non-destructive: an explicit canonical key always wins,
    and the alias key is left in place (the artifact schemas don't set
    ``additionalProperties: false``, so a leftover ``id`` is harmless)."""
    aliases = _ARG_ALIASES.get(tool_name)
    if not aliases or not isinstance(arguments, dict):
        return arguments
    patched = dict(arguments)
    for alias, canonical in aliases.items():
        if canonical not in patched and alias in patched:
            patched[canonical] = patched[alias]
    return patched


def _format_validation_error(
    tool_name: str, errs: list[ValidationError]
) -> str:
    """Produce a single human + model-readable line listing EVERY schema
    error. Leads with the tool name so the model knows which call needs
    fixing, then lists each problem as ``pointer: message`` so the model
    can fix all of them in one retry. We used to report only the first
    error with a "(N more suppressed)" count, but hiding co-equal failures
    is what let a call missing BOTH `artifact_id` and `content` strand the
    model one fix at a time (chat 5e27dfa4, 2026-06-16)."""
    parts = []
    for e in errs:
        pointer = "/".join(str(p) for p in e.absolute_path) or "(root)"
        parts.append(f"{pointer}: {e.message}")
    return f"invalid arguments for {tool_name!r}: " + "; ".join(parts)


async def execute_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """
    Execute an MCP tool and return the result.

    Args:
        tool_name: Name of the tool to execute
        arguments: Tool arguments

    Returns:
        Tool result as a dictionary
    """
    # P1 #12: time + outcome counter for every dispatch.
    # Outcomes: validation_error | unknown_tool | error | success.
    t0 = time.monotonic()
    outcome = "error"
    try:
        result = await _dispatch_mcp_tool(tool_name, arguments)
        # Truthy check, not key-presence: tools like `run_python` return
        # an envelope with an `"error": null` field on success (the null
        # is meaningful — "no exception was raised"). Treating the key's
        # mere presence as failure misclassifies every successful
        # run_python / compile_latex / sandbox call as outcome="error",
        # which we saw in the live metrics on 2026-05-26.
        err = result.get("error") if isinstance(result, dict) else None
        if err:
            if isinstance(err, str) and err.startswith("invalid arguments"):
                outcome = "validation_error"
            elif isinstance(err, str) and err.startswith("Unknown tool"):
                outcome = "unknown_tool"
            else:
                outcome = "error"
        else:
            outcome = "success"
        return result
    finally:
        observe_tool(tool_name, outcome, time.monotonic() - t0)


async def _dispatch_mcp_tool(tool_name: str, arguments: dict) -> dict:
    """Validation + dispatch. Wrapped by ``execute_mcp_tool`` so the
    instrumentation lives in one place.

    The per-tool dispatch table now lives in ``mcp/dispatchers.py``
    (P2 #19); this function is just the validator + a registry
    lookup. ``verify_dispatch_registry`` runs at startup so a
    schema/executor mismatch can't reach a real request."""
    arguments = _normalize_aliases(tool_name, arguments or {})
    arguments = coerce_argument_types(tool_name, arguments)
    validator = _VALIDATORS.get(tool_name)
    if validator is not None:
        errs = sorted(
            validator.iter_errors(arguments or {}),
            key=lambda e: tuple(str(p) for p in e.absolute_path),
        )
        if errs:
            return {"error": _format_validation_error(tool_name, errs)}
    fn = get_dispatcher(tool_name)
    if fn is None:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return await fn(arguments or {})
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}
