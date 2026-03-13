# Mint: Email & Calendar MCP Server

A hosted MCP server that gives AI assistants access to your email and calendar. One URL, pick Google or Microsoft when you sign in.

## For Users

Add this URL to your AI client:

```
https://<deployed-domain>/mcp
```

**Claude.ai** — Settings → MCP Servers → Add → paste the URL.

**ChatGPT** — Settings → Connectors → Create → paste the URL.

**Claude Code** —
```bash
claude mcp add mint https://<deployed-domain>/mcp
```

When prompted, choose "Sign in with Google" or "Sign in with Microsoft" and authorize access.

## Features

- **9 tools** — read inbox, read/search emails, draft emails, list/get/create/update/delete calendar events
- **6 prompts** — daily briefing, inbox prioritization, draft reply, schedule review, find availability, email summary
- **Google + Microsoft** — choose your provider at sign-in, no config needed
- **Drafts only** — email drafts are saved, never sent, so you always review before sending

## For Operators

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- OAuth credentials for **both** Google and Microsoft

### Getting OAuth Credentials

#### Google

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project, enable **Gmail API** and **Google Calendar API**
3. Configure **OAuth consent screen**
4. Create **OAuth client ID** (Web application)
5. Add `{MINT_BASE_URL}/auth/callback` as an authorized redirect URI
6. Copy Client ID and Client Secret

#### Microsoft

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**, set redirect URI to `{MINT_BASE_URL}/auth/callback`
3. Under **Expose an API**, add a scope called `mcp-access`
4. Under **API permissions**, add `Mail.Read`, `Mail.ReadWrite`, `Calendars.ReadWrite`
5. Under **Certificates & secrets**, create a client secret
6. Copy Application (client) ID, Directory (tenant) ID, and Client Secret

### Setup

```bash
git clone https://github.com/winstonkaranja/mint.git
cd mint
cp .env.example .env
# Fill in ALL OAuth credentials in .env
uv sync
```

### Running locally

```bash
uv run python server.py
```

Visit `http://localhost:8000` to see the landing page. Add `http://localhost:8000/mcp` to your AI client.

### Docker

```bash
docker build -t mint .
docker run --env-file .env -p 8000:8000 mint
```

## Tools

| Tool | Parameters | Description |
|------|-----------|-------------|
| `read_inbox` | `max_results` (default 20) | Get recent emails from your inbox |
| `read_email` | `email_id` | Read a specific email by its ID |
| `search_emails` | `query`, `max_results` (default 20) | Search emails by query string |
| `draft_email` | `to`, `subject`, `body`, `cc`, `bcc` | Save an email as a draft |
| `list_events` | `start`, `end`, `max_results` (default 20) | List calendar events in a date range |
| `get_event` | `event_id` | Get a specific calendar event by ID |
| `create_event` | `title`, `start`, `end`, `location`, `description`, `attendees` | Create a new calendar event |
| `update_event` | `event_id`, `title`, `start`, `end`, `location`, `description`, `attendees` | Update an existing calendar event |
| `delete_event` | `event_id` | Delete a calendar event by ID |

## Prompts

| Prompt | Parameters | Description |
|--------|-----------|-------------|
| `daily_briefing` | — | Read inbox and today's calendar. Summarize priorities, flag urgent emails, show schedule. |
| `prioritize_inbox` | `max_emails` (default 20) | Read inbox, categorize by urgency, explain why. |
| `draft_reply` | `email_id` | Read the email, draft a professional response, save as draft. |
| `schedule_review` | `days_ahead` (default 7) | List upcoming events, flag conflicts, suggest prep needed. |
| `find_availability` | `days_ahead` (default 7) | Analyze calendar for free slots, present options for scheduling. |
| `email_summary` | `query` | Search emails matching query, summarize the thread/conversation. |

## Architecture

Mint uses a **provider protocol** pattern — `EmailProvider` and `CalendarProvider` define abstract interfaces, with concrete implementations for Google and Microsoft.

A single `MintProvider` (subclass of FastMCP's `OAuthProxy`) handles OAuth for both providers. During sign-in, users see a provider picker. Their choice is embedded in the JWT claims, so each request is automatically routed to the correct email/calendar implementation.
