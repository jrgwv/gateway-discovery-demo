"""Shared helpers for AgentCore Gateway Lambda targets.

Gateway passes the tool arguments as the Lambda `event` and the tool name in
`context.client_context.custom["bedrockAgentCoreToolName"]`, formatted as
`<target_name>___<tool_name>`. We strip the target prefix before dispatching.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DELIMITER = "___"
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "8"))
USER_AGENT = "gateway-discovery-demo/1.0 (+https://github.com/jrgwv)"


class ToolError(Exception):
    """An error we want to return to the agent as a readable message."""


def gateway_context(context: Any) -> dict[str, str]:
    """Return the AgentCore metadata dict, or {} when invoked outside Gateway."""
    client_context = getattr(context, "client_context", None)
    custom = getattr(client_context, "custom", None) if client_context else None
    return dict(custom or {})


def resolve_tool_name(context: Any) -> str:
    full_name = gateway_context(context).get("bedrockAgentCoreToolName", "")
    if DELIMITER in full_name:
        return full_name.split(DELIMITER, 1)[1]
    return full_name


def http_get_json(base_url: str, params: dict[str, Any], *, raw_query: str | None = None) -> dict | None:
    """GET a JSON endpoint. Returns None on 404 (both public APIs use it for 'no matches')."""
    query = raw_query if raw_query is not None else urllib.parse.urlencode(
        {k: v for k, v in params.items() if v not in (None, "")}
    )
    url = f"{base_url}?{query}" if query else base_url
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    logger.info("GET %s", re.sub(r"(api_key=)[^&]+", r"\1***", url))
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        if err.code == 429:
            raise ToolError("Upstream API rate limit reached; try again shortly.") from err
        raise ToolError(f"Upstream API returned HTTP {err.code}.") from err
    except (urllib.error.URLError, TimeoutError) as err:
        raise ToolError(f"Upstream API unreachable: {err}") from err


def truncate(text: Any, limit: int) -> str:
    if text is None:
        return ""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def require_str(event: dict, key: str, max_len: int = 200) -> str:
    value = event.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{key}' is required.")
    value = value.strip()
    if len(value) > max_len:
        raise ToolError(f"'{key}' must be at most {max_len} characters.")
    return value


def dispatch(handlers: dict, event: dict, context: Any) -> dict:
    """Route to the right tool function and turn failures into agent-readable errors."""
    tool_name = resolve_tool_name(context) or event.pop("_tool", "")
    handler = handlers.get(tool_name)
    if handler is None:
        return {"error": f"Unknown tool '{tool_name}'."}
    try:
        return handler(event or {}, gateway_context(context))
    except ToolError as err:
        logger.warning("Tool %s failed: %s", tool_name, err)
        return {"error": str(err)}
