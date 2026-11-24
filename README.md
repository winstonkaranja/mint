# Mint: Email & Calendar MCP Server

A hosted MCP server that gives AI assistants access to your email and calendar. One URL, pick Google or Microsoft when you sign in.

## For Users

Add this URL to your AI client:

```
https://mint.winstonkaranja.me/mcp
```

**Claude.ai** — Settings → MCP Servers → Add → paste the URL.

**ChatGPT** — Settings → Connectors → Create → paste the URL.

**Claude Code** —
```bash
claude mcp add mint https://mint.winstonkaranja.me/mcp
```

When prompted, choose "Sign in with Google" or "Sign in with Microsoft" and authorize access.

## Features

- **11 intelligence-gated tools** — server-side priority scoring, email-calendar cross-referencing, conflict detection, recurrence awareness, and travel planning, so the AI gets 3-10 actionable items instead of 200 raw emails
- **Google + Microsoft** — choose your provider at sign-in, no config needed
- **Shared mailbox support** — pass `mailbox="sales@company.com"` to any tool to access shared or delegated mailboxes
- **Schedule conflict detection** — overlapping meetings are automatically flagged in daily briefs
- **Recurrence awareness** — recurring events are labeled with occurrence counts (e.g. "Recurring (2 of 5 this window)")
- **Thread metadata** — collapsed threads include participant lists and last-reply tracking
- **Location-aware travel planning** — physical meetings get Google Maps directions + Uber deep links; virtual meetings surface join URLs. Optional Google Maps API key adds travel time estimates and "leave by" alerts
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

All tools accept an optional `mailbox` parameter to access shared or delegated mailboxes (e.g. `mailbox="sales@company.com"`).

| Tool | Parameters | Description |
|------|-----------|-------------|
| `get_daily_brief` | `timezone`, `scoring_overrides`, `origin` | Morning snapshot: meetings + emails + conflicts + travel info + highlights |
| `get_priority_emails` | `limit` (default 10), `scoring_overrides` | Top emails scored 0.0-1.0 by urgency, calendar relevance, and thread activity |
| `get_email_thread` | `email_id` | Full conversation thread for one email |
| `draft_email_reply` | `email_id`, `body`, `cc`, `bcc` | Draft a reply (never sends). Scans for sensitive content before saving |
| `get_upcoming_events` | `days` (default 7), `timezone`, `origin` | Calendar enriched with unread counts, conflicts, recurrence, and travel info |
| `create_reminder` | `title`, `start`, `description` | Create a 30-minute personal calendar reminder |
| `summarize_day` | `timezone`, `scoring_overrides`, `origin` | End-of-day: what happened, what's pending, tomorrow's first events |
| `get_travel_time` | `origin`, `destination`, `arrive_by`, `mode` | Travel directions + time estimate between any two locations |
| `get_weekly_digest` | `timezone` | 7-day retrospective: email volume, top senders, busiest day, meeting count |
| `get_pending_drafts` | `max_results` (default 20) | List all saved drafts for review before sending |
| `server_status` | — | Health check: version, uptime, provider status, compliance metadata |

## Architecture

Mint uses a **provider protocol** pattern — `EmailProvider` and `CalendarProvider` define abstract interfaces, with concrete implementations for Google and Microsoft. All provider methods accept an optional `mailbox` parameter that routes API calls to shared/delegated mailboxes when specified.

An **intelligence layer** (`mint/intelligence.py`) sits between the tools and providers. It handles priority scoring, email-calendar cross-referencing, thread deduplication with participant tracking, schedule conflict detection, recurrence annotation, and location-aware travel planning. Tools call intelligence functions which call providers. Claude gets 3-10 actionable items per tool call instead of raw API responses.

A **maps module** (`mint/maps.py`) optionally integrates with Google Maps Directions API for travel time estimates. When `MINT_GOOGLE_MAPS_API_KEY` is configured ($200/month free credit covers ~40k requests), physical meeting locations get "leave by" alerts. Without the key, free Google Maps direction links and Uber deep links still work.

A single `MintProvider` (subclass of FastMCP's `OAuthProxy`) handles OAuth for both providers. During sign-in, users see a provider picker. Their choice is embedded in the JWT claims, so each request is automatically routed to the correct email/calendar implementation.
