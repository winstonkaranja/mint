import logging
import time

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from mint.auth import MintProvider
from mint.config import get_settings
from mint.pages import landing_page_html
from mint.tools.smart import register_smart_tools

# Emit structured audit logs as bare JSON lines to stdout.
logging.basicConfig(format="%(message)s", level=logging.INFO)

_SERVER_VERSION = "1.1.0"
_PROVIDERS = ["google", "microsoft"]
_TOOLS_COUNT = 7
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


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "healthy",
        "server": "Mint",
        "version": _SERVER_VERSION,
        "uptime_seconds": round(time.monotonic() - _start_time),
        "providers": _PROVIDERS,
        "tools_count": _TOOLS_COUNT,
    })


@mcp.custom_route("/ready", methods=["GET"])
async def readiness_check(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ready"})


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
