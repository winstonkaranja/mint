from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse

from mint.auth import MintProvider
from mint.config import get_settings
from mint.pages import landing_page_html
from mint.prompts import register_prompts
from mint.tools.calendar import register_calendar_tools
from mint.tools.email import register_email_tools

settings = get_settings()
auth = MintProvider(settings)

mcp = FastMCP(
    name="Mint",
    instructions=(
        "Email and calendar tools. "
        "Works with Gmail or Outlook — the provider is chosen during sign-in."
    ),
    auth=auth,
)

register_email_tools(mcp)
register_calendar_tools(mcp)
register_prompts(mcp)

mcp_url = f"{settings.base_url.rstrip('/')}/mcp"


@mcp.custom_route("/", methods=["GET"])
async def landing(request: Request) -> HTMLResponse:
    return HTMLResponse(landing_page_html(mcp_url))


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
