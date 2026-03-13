from __future__ import annotations

from fastmcp import FastMCP
from fastmcp.prompts import Message


def register_prompts(mcp: FastMCP) -> None:

    @mcp.prompt(
        description="Read inbox and today's calendar. Summarize priorities, flag urgent emails, show schedule.",
    )
    def daily_briefing() -> list[Message]:
        return [
            Message(
                "Read my inbox (last 20 emails) and today's calendar events. "
                "Give me a morning briefing:\n"
                "1. Urgent or time-sensitive emails that need immediate attention\n"
                "2. Today's schedule with any conflicts highlighted\n"
                "3. Action items I should prioritize\n"
                "Keep it concise and scannable.",
            )
        ]

    @mcp.prompt(
        description="Read inbox, categorize by urgency (urgent/action-needed/informational/low-priority), explain why.",
    )
    def prioritize_inbox(max_emails: int = 20) -> list[Message]:
        return [
            Message(
                f"Read my inbox (last {max_emails} emails) and categorize each one:\n"
                "- **Urgent**: needs response within hours\n"
                "- **Action needed**: requires a response or task, but not immediately\n"
                "- **Informational**: FYI only, no action required\n"
                "- **Low priority**: newsletters, notifications, etc.\n\n"
                "For each email, explain briefly why you categorized it that way.",
            )
        ]

    @mcp.prompt(
        description="Read the email, draft a professional response, save as draft. Ask user to review.",
    )
    def draft_reply(email_id: str) -> list[Message]:
        return [
            Message(
                f"Read email {email_id} and draft a professional reply. "
                "Match the tone of the original message. "
                "Save the reply as a draft using the draft_email tool. "
                "Then show me the draft so I can review it before sending.",
            )
        ]

    @mcp.prompt(
        description="List upcoming events, flag conflicts, suggest prep needed.",
    )
    def schedule_review(days_ahead: int = 7) -> list[Message]:
        return [
            Message(
                f"List my calendar events for the next {days_ahead} days. "
                "For each day:\n"
                "1. Show the schedule in chronological order\n"
                "2. Flag any time conflicts or back-to-back meetings\n"
                "3. Note any meetings that might need preparation\n"
                "Summarize with an overview of how busy each day looks.",
            )
        ]

    @mcp.prompt(
        description="Analyze calendar for free slots, present options for scheduling.",
    )
    def find_availability(days_ahead: int = 7) -> list[Message]:
        return [
            Message(
                f"Check my calendar for the next {days_ahead} days and find available "
                "time slots during business hours (9 AM - 5 PM). "
                "Present the free slots grouped by day, showing:\n"
                "- Duration of each free block\n"
                "- What meetings are before/after each slot\n"
                "Suggest the best slots for scheduling a new meeting.",
            )
        ]

    @mcp.prompt(
        description="Search emails matching query, summarize the thread/conversation.",
    )
    def email_summary(query: str) -> list[Message]:
        return [
            Message(
                f'Search my emails for "{query}" and summarize what you find. '
                "Group related emails into threads/conversations. "
                "For each thread, provide:\n"
                "- Participants involved\n"
                "- Key points and decisions made\n"
                "- Any outstanding action items or open questions\n"
                "- Current status (resolved, waiting for response, etc.)",
            )
        ]
