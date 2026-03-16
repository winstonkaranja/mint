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

- **7 intelligence-gated tools** — server-side priority scoring and email-calendar cross-referencing, so the AI gets 3-10 actionable items instead of 200 raw emails
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
| `get_daily_brief` | — | Morning snapshot: today's meetings + top emails + cross-referenced highlights |
| `get_priority_emails` | `limit` (default 10) | Top emails scored 0.0-1.0 by urgency, calendar relevance, and thread activity |
| `get_email_thread` | `email_id` | Full conversation thread for one email |
| `draft_email_reply` | `email_id`, `body`, `cc`, `bcc` | Draft a reply (never sends) |
| `get_upcoming_events` | `days` (default 7) | Calendar enriched with unread email counts from attendees |
| `create_reminder` | `title`, `start`, `description` | Create a 30-minute personal calendar reminder |
| `summarize_day` | — | End-of-day: what happened, what's pending, tomorrow's first events |

## Architecture

Mint uses a **provider protocol** pattern — `EmailProvider` and `CalendarProvider` define abstract interfaces, with concrete implementations for Google and Microsoft.

An **intelligence layer** (`mint/intelligence.py`) sits between the tools and providers. It handles priority scoring, email-calendar cross-referencing, and filtering. Tools call intelligence functions which call providers. Claude gets 3-10 actionable items per tool call instead of raw API responses.

A single `MintProvider` (subclass of FastMCP's `OAuthProxy`) handles OAuth for both providers. During sign-in, users see a provider picker. Their choice is embedded in the JWT claims, so each request is automatically routed to the correct email/calendar implementation.
