from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from thyca.core.protocol import ToolResult

Handler = Callable[[dict], Awaitable[str | ToolResult]]
ResourceKeyFn = Callable[[dict], str | None]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    handler: Handler
    parallel_safe: bool = True
    resource_key: ResourceKeyFn | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("ToolSpec.name must be a non-empty string")
        if not isinstance(self.description, str) or not self.description:
            raise ValueError("ToolSpec.description must be a non-empty string")
        if not isinstance(self.parameters, dict):
            raise ValueError("ToolSpec.parameters must be a dict")


class ToolRegistry:
    """Spec store: register specs, validate args, emit schemas.

    Execution (locks, caps, soft-timeout, tracking) lives in the gateway;
    this class never runs a handler.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        """Spec lookup for the gateway; None when unregistered."""
        return self._specs.get(name)

    def validate_args(self, spec: ToolSpec, arguments: dict) -> str | None:
        """Arg validation for the gateway; None when valid."""
        return _validate_args(spec.parameters, arguments)

    def to_openai_schema(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]


def _validate_args(schema: dict, arguments: dict) -> str | None:
    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for name in required:
        if name not in arguments:
            return f"missing argument: {name}"
    extra = schema.get("additionalProperties", True)
    if extra is False:
        allowed = set((schema.get("properties") or {}).keys())
        unexpected = [key for key in arguments if key not in allowed]
        if unexpected:
            return f"unexpected argument: {unexpected[0]}"
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, value in arguments.items():
            subschema = properties.get(name)
            if not isinstance(subschema, dict):
                continue
            expected = subschema.get("type")
            if expected is None:
                # Untyped properties (some MCP schemas) skip type checks;
                # handlers keep their domain validation.
                continue
            error = _type_error(name, value, expected)
            if error is not None:
                return error
    return None


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_type(value: object, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        # JSON-Schema parity: integral floats are integers; bools are not.
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    # Unknown type names stay forward-compatible: never reject on them.
    return True


def _type_error(name: str, value: object, expected: object) -> str | None:
    allowed = [
        candidate
        for candidate in (expected if isinstance(expected, list) else [expected])
        if isinstance(candidate, str)
    ]
    if not allowed:
        # Malformed schema: skip like unknown-type (forward-compatible).
        return None
    for candidate in allowed:
        if _matches_type(value, candidate):
            return None
    want = allowed[0] if len(allowed) == 1 else f"one of {allowed}"
    return f"argument {name!r} must be {want}, got {_type_name(value)}"
