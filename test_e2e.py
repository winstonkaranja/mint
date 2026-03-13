"""
End-to-end test for Mint MCP server.

Connects to the running server via FastMCP client with OAuth,
then exercises every tool and prompt.
"""

import asyncio
import json
from datetime import datetime, timedelta

from fastmcp import Client
from fastmcp.client.auth import OAuth


async def main():
    server_url = "http://localhost:8000/mcp"

    print("=== Mint E2E Test ===\n")
    print("Connecting to server with OAuth (browser will open for Google login)...\n")

    auth = OAuth(
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
        ],
        client_name="Mint E2E Test",
    )

    async with Client(server_url, auth=auth) as client:
        # --- List tools ---
        print("1. Listing available tools...")
        tools = await client.list_tools()
        for t in tools:
            print(f"   - {t.name}: {t.description}")
        print()

        # --- List prompts ---
        print("2. Listing available prompts...")
        prompts = await client.list_prompts()
        for p in prompts:
            print(f"   - {p.name}: {p.description}")
        print()

        # --- Email tools ---
        print("3. Reading inbox (max 5)...")
        result = await client.call_tool("read_inbox", {"max_results": 5})
        print(f"   Result: {result}")
        print()

        print("4. Searching emails for 'test'...")
        result = await client.call_tool("search_emails", {"query": "test", "max_results": 3})
        print(f"   Result: {result}")
        print()

        # --- Draft email ---
        print("5. Drafting a test email...")
        result = await client.call_tool("draft_email", {
            "to": ["test@example.com"],
            "subject": "Mint E2E Test Draft",
            "body": "This is a test draft created by the Mint e2e test. Safe to delete.",
        })
        print(f"   Draft ID: {result}")
        print()

        # --- Calendar tools ---
        now = datetime.now(tz=None)
        week_later = now + timedelta(days=7)

        print(f"6. Listing calendar events ({now.date()} to {week_later.date()})...")
        result = await client.call_tool("list_events", {
            "start": now.isoformat(),
            "end": week_later.isoformat(),
            "max_results": 5,
        })
        print(f"   Result: {result}")
        print()

        print("7. Creating a test event...")
        event_start = now + timedelta(days=1, hours=2)
        event_end = event_start + timedelta(hours=1)
        result = await client.call_tool("create_event", {
            "title": "Mint E2E Test Event",
            "start": event_start.isoformat(),
            "end": event_end.isoformat(),
            "description": "Created by Mint e2e test - safe to delete",
        })
        print(f"   Result: {result}")
        print()

        # Extract event ID from result
        event_data = json.loads(result.content[0].text)
        event_id = event_data.get("id")

        if event_id:
            print(f"8. Updating test event ({event_id})...")
            result = await client.call_tool("update_event", {
                "event_id": event_id,
                "title": "Mint E2E Test Event (Updated)",
                "location": "Test Location",
            })
            print(f"   Result: {result}")
            print()

            print(f"9. Getting test event ({event_id})...")
            result = await client.call_tool("get_event", {"event_id": event_id})
            print(f"   Result: {result}")
            print()

            print(f"10. Deleting test event ({event_id})...")
            result = await client.call_tool("delete_event", {"event_id": event_id})
            print(f"   Result: {result}")
            print()

        # --- Prompt tests ---
        print("11. Getting daily_briefing prompt...")
        prompt_result = await client.get_prompt("daily_briefing")
        for msg in prompt_result.messages:
            print(f"    [{msg.role}]: {msg.content.text[:100]}...")
        print()

        print("12. Getting prioritize_inbox prompt (max_emails=10)...")
        prompt_result = await client.get_prompt("prioritize_inbox", {"max_emails": "10"})
        for msg in prompt_result.messages:
            print(f"    [{msg.role}]: {msg.content.text[:100]}...")
        print()

        print("13. Getting schedule_review prompt (days_ahead=3)...")
        prompt_result = await client.get_prompt("schedule_review", {"days_ahead": "3"})
        for msg in prompt_result.messages:
            print(f"    [{msg.role}]: {msg.content.text[:100]}...")
        print()

        print("=== All tests completed! ===")


if __name__ == "__main__":
    asyncio.run(main())
