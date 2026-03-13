from fastmcp import FastMCP

from mint.auth import build_auth
from mint.config import get_settings
from mint.prompts import register_prompts
from mint.tools.calendar import register_calendar_tools
from mint.tools.email import register_email_tools

settings = get_settings()
auth = build_auth(settings)

mcp = FastMCP(
    name="Mint",
    instructions="Email and calendar tools. Works with Gmail or Outlook depending on configuration.",
    auth=auth,
)

register_email_tools(mcp)
register_calendar_tools(mcp)
register_prompts(mcp)

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
