from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.dependencies import Depends

from mint.dependencies import get_email_provider
from mint.models import DraftEmailRequest, Email, EmailSummary
from mint.providers.base import EmailProvider


def register_email_tools(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def read_inbox(
        max_results: int = 20,
        provider: EmailProvider = Depends(get_email_provider),
    ) -> list[EmailSummary]:
        """Get recent emails from your inbox."""
        return await provider.get_inbox(max_results=max_results)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def read_email(
        email_id: str,
        provider: EmailProvider = Depends(get_email_provider),
    ) -> Email:
        """Read a specific email by its ID."""
        return await provider.get_email(email_id)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def search_emails(
        query: str,
        max_results: int = 20,
        provider: EmailProvider = Depends(get_email_provider),
    ) -> list[EmailSummary]:
        """Search emails by query string (subject, body, sender, etc.)."""
        return await provider.search_emails(query, max_results=max_results)

    @mcp.tool()
    async def draft_email(
        to: list[str],
        subject: str,
        body: str,
        cc: list[str] = [],
        bcc: list[str] = [],
        provider: EmailProvider = Depends(get_email_provider),
    ) -> str:
        """Save an email as a draft. Returns the draft ID. The user can review and send it from their mail client."""
        request = DraftEmailRequest(
            to=to, subject=subject, body=body, cc=cc, bcc=bcc
        )
        return await provider.draft_email(request)
