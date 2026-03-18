import logging
import time

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from mint.auth import MintProvider
from mint.config import get_settings
from mint.notifications import subscribe, subscriber_count
from mint.pages import landing_page_html, tools_docs_html
from mint.scim import (
    scim_create_user,
    scim_delete_user,
    scim_get_user,
    scim_get_users,
    scim_patch_user,
)
from mint.tools.smart import register_smart_tools

# Emit structured audit logs as bare JSON lines to stdout.
logging.basicConfig(format="%(message)s", level=logging.INFO)

_SERVER_VERSION = "2.0.0"
_PROVIDERS = ["google", "microsoft"]
_TOOLS_COUNT = 10
_start_time = time.monotonic()

settings = get_settings()
auth = MintProvider(settings)

mcp = FastMCP(
    name="Mint",
    instructions=(
        "Email and calendar assistant. Uses server-side intelligence to score, "
        "filter, and cross-reference emails with calendar events. Each tool "
        "returns focused, actionable data — not raw inbox dumps. "
        "Works with Gmail or Outlook — the provider is chosen during sign-in."
    ),
    auth=auth,
)

register_smart_tools(mcp)

mcp_url = f"{settings.base_url.rstrip('/')}/mcp"


@mcp.custom_route("/", methods=["GET"])
async def landing(request: Request) -> HTMLResponse:
    return HTMLResponse(landing_page_html(mcp_url))


@mcp.custom_route("/docs", methods=["GET"])
async def api_docs(request: Request) -> HTMLResponse:
    """Auto-generated interactive documentation for all registered MCP tools."""
    registered_tools = await mcp.list_tools()
    tool_metadata = []

    for tool in registered_tools:
        annotations = tool.annotations
        is_read_only = bool(
            annotations and getattr(annotations, "readOnlyHint", False)
        )

        schema = tool.parameters or {}
        properties = schema.get("properties", {})
        required_set = set(schema.get("required", []))

        params = [
            {
                "name": param_name,
                "schema": param_schema,
                "required": param_name in required_set,
            }
            for param_name, param_schema in properties.items()
        ]

        tool_metadata.append({
            "name": tool.name,
            "description": tool.description,
            "read_only": is_read_only,
            "parameters": params,
        })

    return HTMLResponse(tools_docs_html(tool_metadata))


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    health: dict = {
        "status": "healthy",
        "server": "Mint",
        "version": _SERVER_VERSION,
        "uptime_seconds": round(time.monotonic() - _start_time),
        "providers": _PROVIDERS,
        "tools_count": _TOOLS_COUNT,
        "sse_subscribers": subscriber_count(),
        "compliance": {
            "encryption_in_transit": "TLS 1.3 (Azure-managed)",
            "data_at_rest": "none (stateless — no email content persisted)",
            "hipaa_ready": True,
            "data_region": settings.data_region,
        },
    }
    return JSONResponse(health)


@mcp.custom_route("/notifications", methods=["GET"])
async def sse_notifications(request: Request) -> StreamingResponse:
    """Stream real-time alerts (high-priority emails, upcoming events) via SSE."""
    return StreamingResponse(
        subscribe(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@mcp.custom_route("/ready", methods=["GET"])
async def readiness_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready"})


# ---------------------------------------------------------------------------
# SCIM 2.0 User provisioning (RFC 7644)
# ---------------------------------------------------------------------------

@mcp.custom_route("/scim/v2/Users", methods=["GET"])
async def scim_list(request: Request) -> JSONResponse:
    return await scim_get_users(request)


@mcp.custom_route("/scim/v2/Users", methods=["POST"])
async def scim_create(request: Request) -> JSONResponse:
    return await scim_create_user(request)


@mcp.custom_route("/scim/v2/Users/{user_id}", methods=["GET"])
async def scim_read(request: Request) -> JSONResponse:
    return await scim_get_user(request)


@mcp.custom_route("/scim/v2/Users/{user_id}", methods=["DELETE"])
async def scim_delete(request: Request) -> Response:
    return await scim_delete_user(request)


@mcp.custom_route("/scim/v2/Users/{user_id}", methods=["PATCH"])
async def scim_update(request: Request) -> JSONResponse:
    return await scim_patch_user(request)


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
