# Mint: Email & Calendar MCP Server

An MCP server that gives AI assistants access to your email and calendar. Works with Gmail and Outlook via a unified provider interface.

## Features

- **9 tools** — read inbox, read/search emails, draft emails, list/get/create/update/delete calendar events
- **6 prompts** — daily briefing, inbox prioritization, draft reply, schedule review, find availability, email summary
- **Google + Microsoft** — swap providers with a single env var
- **Drafts only** — email drafts are saved, never sent, so you always review before sending

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- OAuth credentials for Google and/or Microsoft (see below)

## Getting OAuth Credentials

### Google

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable the **Gmail API** and **Google Calendar API**
4. Go to **APIs & Services → OAuth consent screen** and configure it
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
6. Choose **Web application** as the application type
7. Add `{MINT_BASE_URL}/oauth/google/callback` as an authorized redirect URI (e.g. `http://localhost:8000/oauth/google/callback`)
8. Copy the **Client ID** and **Client Secret** into your `.env`

### Microsoft

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **New registration**, give it a name, and set the redirect URI to `{MINT_BASE_URL}/oauth/microsoft/callback`
3. Under **API permissions**, add:
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `Calendars.ReadWrite`
4. Under **Certificates & secrets**, create a new client secret
5. Copy the **Application (client) ID**, **Directory (tenant) ID**, and **Client Secret** into your `.env`

## Setup

```bash
git clone https://github.com/winstonkaranja/mint.git
cd mint
cp .env.example .env
# Fill in your OAuth credentials in .env
uv sync
```

## Running

```bash
uv run python server.py
```

The server starts on `http://localhost:8000`.

## Connecting to Clients

### claude.ai

Deploy the server publicly (for development, use ngrok):

```bash
ngrok http 8000
```

Then in claude.ai: **Settings → MCP Servers → Add** and enter your server URL (`https://<your-domain>/mcp`).

### ChatGPT

Deploy the server publicly, then:

1. Go to **Settings → Connectors → Create**
2. Enter your server URL and a name
3. In each chat, enable via **+ → Developer Mode**

### Claude Code

For local development:

```bash
claude mcp add mint http://localhost:8000/mcp
```

Or with a public URL:

```bash
claude mcp add mint https://<your-domain>/mcp
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

Mint uses a **provider protocol** pattern — `EmailProvider` and `CalendarProvider` define abstract interfaces, with concrete implementations for Google and Microsoft. The active provider is selected by the `MINT_PROVIDER` env var.

Dependencies are injected into tool functions using FastMCP's `Depends()`, which resolves the correct provider at request time based on the authenticated user's OAuth token. The server also acts as an OAuth proxy, handling the authorization flow so clients don't need to manage tokens directly.
