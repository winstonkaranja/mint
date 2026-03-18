"""HTML pages for Mint's hosted multi-provider flow."""

from __future__ import annotations

import html as _html
from typing import Any


def provider_picker_html(
    txn_id: str,
    csrf_token: str,
    *,
    sso_enabled: bool = False,
    sso_label: str | None = None,
    sso_enforce_only: bool = False,
) -> str:
    """OAuth consent page: pick Google, Microsoft, and/or enterprise SSO.

    When *sso_enforce_only* is True, only the SSO button is shown —
    consumer Google/Microsoft options are hidden.
    """
    txn_id = _html.escape(txn_id, quote=True)
    csrf_token = _html.escape(csrf_token, quote=True)
    sso_label_escaped = _html.escape(sso_label or "SSO", quote=True)

    # --- provider button fragments ---

    google_btn = f"""\
  <form method="post">
    <input type="hidden" name="txn_id" value="{txn_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <input type="hidden" name="action" value="approve">
    <input type="hidden" name="provider" value="google">
    <button type="submit" class="btn">
      <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59a14.5 14.5 0 0 1 0-9.18l-7.98-6.19a24.001 24.001 0 0 0 0 21.56l7.98-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
      Sign in with Google
    </button>
  </form>"""

    microsoft_btn = f"""\
  <form method="post">
    <input type="hidden" name="txn_id" value="{txn_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <input type="hidden" name="action" value="approve">
    <input type="hidden" name="provider" value="microsoft">
    <button type="submit" class="btn">
      <svg width="20" height="20" viewBox="0 0 21 21"><rect x="1" y="1" width="9" height="9" fill="#f25022"/><rect x="11" y="1" width="9" height="9" fill="#7fba00"/><rect x="1" y="11" width="9" height="9" fill="#00a4ef"/><rect x="11" y="11" width="9" height="9" fill="#ffb900"/></svg>
      Sign in with Microsoft
    </button>
  </form>"""

    sso_btn = f"""\
  <form method="post">
    <input type="hidden" name="txn_id" value="{txn_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <input type="hidden" name="action" value="approve">
    <input type="hidden" name="provider" value="sso">
    <button type="submit" class="btn btn-sso">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
      Sign in with {sso_label_escaped}
    </button>
  </form>"""

    divider = '  <div class="divider">or</div>'

    # Assemble the button section based on SSO configuration.
    if sso_enforce_only and sso_enabled:
        buttons = sso_btn
    elif sso_enabled:
        buttons = "\n\n".join([google_btn, divider, microsoft_btn, divider, sso_btn])
    else:
        buttons = "\n\n".join([google_btn, divider, microsoft_btn])

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — Mint</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: system-ui, -apple-system, sans-serif;
    background: #f8faf8; color: #1a1a1a;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }}
  .card {{
    background: #fff; border-radius: 12px; padding: 2.5rem 2rem;
    max-width: 400px; width: 100%; box-shadow: 0 2px 12px rgba(0,0,0,.08);
    text-align: center;
  }}
  .logo {{ font-size: 2rem; margin-bottom: .25rem; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .25rem; font-weight: 600; }}
  .subtitle {{ color: #666; font-size: .9rem; margin-bottom: 2rem; }}
  .btn {{
    display: flex; align-items: center; justify-content: center; gap: .6rem;
    width: 100%; padding: .85rem 1rem; border: 1px solid #ddd; border-radius: 8px;
    background: #fff; font-size: .95rem; cursor: pointer;
    transition: background .15s, border-color .15s; margin-bottom: .75rem;
    color: #1a1a1a; text-decoration: none;
  }}
  .btn:hover {{ background: #f5f5f5; border-color: #bbb; }}
  .btn-sso {{ border-color: #4a6cf7; color: #4a6cf7; font-weight: 500; }}
  .btn-sso:hover {{ background: #f0f3ff; border-color: #3451d1; }}
  .btn svg {{ flex-shrink: 0; }}
  .divider {{ color: #999; font-size: .8rem; margin: .5rem 0; }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">&#127807;</div>
  <h1>Mint</h1>
  <p class="subtitle">Choose your email &amp; calendar provider</p>

{buttons}
</div>
</body>
</html>"""


def landing_page_html(mcp_url: str) -> str:
    """Product landing page shown at /."""
    mcp_url = _html.escape(mcp_url, quote=True)
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mint — Email &amp; Calendar for AI</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: system-ui, -apple-system, sans-serif;
    background: #f8faf8; color: #1a1a1a; line-height: 1.6;
  }}
  .hero {{
    max-width: 640px; margin: 0 auto; padding: 4rem 1.5rem 3rem;
    text-align: center;
  }}
  .logo {{ font-size: 3rem; }}
  h1 {{ font-size: 2rem; margin: .5rem 0 .25rem; font-weight: 700; }}
  .tagline {{ color: #555; font-size: 1.1rem; margin-bottom: 2rem; }}
  .url-box {{
    display: flex; align-items: center; gap: .5rem;
    background: #fff; border: 1px solid #ddd; border-radius: 8px;
    padding: .6rem 1rem; margin: 0 auto 2.5rem; max-width: 480px;
  }}
  .url-box code {{
    flex: 1; font-size: .95rem; color: #333; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
  }}
  .url-box button {{
    padding: .4rem .8rem; border: 1px solid #ddd; border-radius: 6px;
    background: #fff; cursor: pointer; font-size: .85rem;
    transition: background .15s;
  }}
  .url-box button:hover {{ background: #f0f0f0; }}
  section {{ max-width: 640px; margin: 0 auto; padding: 0 1.5rem 2rem; }}
  h2 {{ font-size: 1.15rem; margin: 2rem 0 .75rem; }}
  .steps {{ padding-left: 1.2rem; }}
  .steps li {{ margin-bottom: .5rem; }}
  pre {{
    background: #f0f0f0; border-radius: 6px; padding: .75rem 1rem;
    overflow-x: auto; font-size: .85rem;
  }}
  .grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: .75rem; margin-top: .75rem;
  }}
  .grid div {{
    background: #fff; border: 1px solid #eee; border-radius: 8px;
    padding: .75rem 1rem; font-size: .9rem;
  }}
  .grid div strong {{ display: block; margin-bottom: .2rem; }}
  footer {{
    text-align: center; padding: 2rem 1rem; color: #999; font-size: .8rem;
  }}
</style>
</head>
<body>
<div class="hero">
  <div class="logo">&#127807;</div>
  <h1>Mint</h1>
  <p class="tagline">Email &amp; calendar tools for AI assistants.<br>
  Works with Gmail and Outlook — pick your provider when you sign in.</p>

  <div class="url-box">
    <code id="mcp-url">{mcp_url}</code>
    <button onclick="navigator.clipboard.writeText(document.getElementById('mcp-url').textContent)">Copy</button>
  </div>
</div>

<section>
  <h2>Connect to your AI client</h2>

  <h3>Claude.ai / ChatGPT</h3>
  <ol class="steps">
    <li>Copy the URL above</li>
    <li>Open <strong>Settings &rarr; MCP Servers &rarr; Add</strong> (or Connectors in ChatGPT)</li>
    <li>Paste the URL and save</li>
    <li>When prompted, choose Google or Microsoft and sign in</li>
  </ol>

  <h3>Claude Code</h3>
  <pre>claude mcp add mint {mcp_url}</pre>

  <h2>What you get</h2>
  <p style="color:#666; font-size:.9rem; margin-bottom:.75rem;">
    7 intelligence-gated tools. Emails are scored by urgency and cross-referenced
    with your calendar — you get 3-10 actionable items, not 200 raw emails.
  </p>
  <div class="grid">
    <div><strong>get_daily_brief</strong>Morning snapshot</div>
    <div><strong>get_priority_emails</strong>Top emails by urgency</div>
    <div><strong>get_email_thread</strong>Full conversation</div>
    <div><strong>draft_email_reply</strong>Draft a reply</div>
    <div><strong>get_upcoming_events</strong>Calendar + related emails</div>
    <div><strong>create_reminder</strong>Personal reminder</div>
    <div><strong>summarize_day</strong>End-of-day wrap-up</div>
  </div>
</section>

<footer>Mint &mdash; open-source MCP server for email &amp; calendar</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# /docs — Interactive API documentation
# ---------------------------------------------------------------------------

def _schema_type_label(prop: dict[str, Any]) -> str:
    """Derive a human-readable type label from a JSON Schema property."""
    if "anyOf" in prop:
        parts = [_schema_type_label(branch) for branch in prop["anyOf"]]
        non_null = [p for p in parts if p != "null"]
        if len(non_null) == 1 and len(parts) > 1:
            return f"{non_null[0]} | null"
        return " | ".join(parts)

    if "allOf" in prop and len(prop["allOf"]) == 1:
        return _schema_type_label(prop["allOf"][0])

    if "$ref" in prop:
        ref = prop["$ref"]
        # e.g. "#/$defs/ScoringOverrides" -> "ScoringOverrides"
        return ref.rsplit("/", 1)[-1]

    schema_type = prop.get("type", "any")

    if schema_type == "array":
        items = prop.get("items", {})
        item_label = _schema_type_label(items) if items else "any"
        return f"list[{item_label}]"

    type_map = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
    return type_map.get(schema_type, schema_type)


def _render_param_row(name: str, prop: dict[str, Any], is_required: bool) -> str:
    """Render a single parameter as an HTML table row."""
    name_escaped = _html.escape(name)
    type_label = _html.escape(_schema_type_label(prop))
    description = _html.escape(prop.get("description", ""))

    if is_required:
        required_badge = '<span class="badge badge-required">required</span>'
        default_cell = "&mdash;"
    else:
        required_badge = '<span class="badge badge-optional">optional</span>'
        default = prop.get("default")
        if default is None:
            default_cell = '<code class="default">None</code>'
        elif isinstance(default, str):
            default_cell = f'<code class="default">&quot;{_html.escape(default)}&quot;</code>'
        elif isinstance(default, list):
            default_cell = '<code class="default">[]</code>'
        else:
            default_cell = f'<code class="default">{_html.escape(str(default))}</code>'

    return f"""\
      <tr>
        <td><code>{name_escaped}</code></td>
        <td><code>{type_label}</code></td>
        <td>{default_cell}</td>
        <td>{required_badge}</td>
        <td class="param-desc">{description}</td>
      </tr>"""


def _render_tool_card(tool: dict[str, Any]) -> str:
    """Render a single tool as a collapsible card."""
    name = _html.escape(tool["name"])
    description = _html.escape(tool.get("description") or "")
    is_read_only = tool.get("read_only", False)

    badge = (
        '<span class="badge badge-read">read-only</span>'
        if is_read_only
        else '<span class="badge badge-mutate">mutating</span>'
    )

    params = tool.get("parameters", [])

    if params:
        rows = "\n".join(
            _render_param_row(p["name"], p["schema"], p["required"])
            for p in params
        )
        params_html = f"""\
    <table>
      <thead>
        <tr>
          <th>Parameter</th><th>Type</th><th>Default</th><th>Status</th><th>Description</th>
        </tr>
      </thead>
      <tbody>
{rows}
      </tbody>
    </table>"""
    else:
        params_html = '    <p class="no-params">No parameters.</p>'

    return f"""\
  <details class="tool-card" open>
    <summary>
      <strong>{name}</strong>
      {badge}
    </summary>
    <p class="tool-desc">{description}</p>
{params_html}
  </details>"""


def tools_docs_html(tools: list[dict[str, Any]]) -> str:
    """Interactive API documentation page served at /docs."""
    tool_cards = "\n\n".join(_render_tool_card(t) for t in tools)
    tool_count = len(tools)
    read_count = sum(1 for t in tools if t.get("read_only"))
    mutate_count = tool_count - read_count

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>API Documentation &mdash; Mint</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: system-ui, -apple-system, sans-serif;
    background: #f8faf8; color: #1a1a1a; line-height: 1.6;
  }}

  /* --- header --- */
  .hero {{
    max-width: 760px; margin: 0 auto; padding: 3rem 1.5rem 1.5rem;
    text-align: center;
  }}
  .logo {{ font-size: 2.5rem; }}
  h1 {{ font-size: 1.75rem; margin: .25rem 0 .15rem; font-weight: 700; }}
  .tagline {{ color: #555; font-size: .95rem; margin-bottom: .5rem; }}
  .stats {{
    display: flex; justify-content: center; gap: 1.5rem;
    font-size: .85rem; color: #777; margin-bottom: 1.5rem;
  }}

  /* --- tool cards --- */
  .tools {{ max-width: 760px; margin: 0 auto; padding: 0 1.5rem 3rem; }}

  .tool-card {{
    background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
    margin-bottom: 1rem; overflow: hidden;
    box-shadow: 0 1px 4px rgba(0,0,0,.04);
    transition: box-shadow .15s;
  }}
  .tool-card[open] {{ box-shadow: 0 2px 8px rgba(0,0,0,.07); }}

  .tool-card summary {{
    padding: .9rem 1.25rem; cursor: pointer;
    display: flex; align-items: center; gap: .6rem;
    list-style: none; user-select: none;
  }}
  .tool-card summary::-webkit-details-marker {{ display: none; }}
  .tool-card summary::before {{
    content: "\\25B6"; font-size: .65rem; color: #999;
    transition: transform .15s;
  }}
  .tool-card[open] summary::before {{ transform: rotate(90deg); }}

  .tool-card summary strong {{
    font-size: 1rem; font-family: 'SF Mono', 'Fira Code', monospace;
  }}

  .tool-desc {{
    padding: 0 1.25rem .5rem; margin: 0;
    color: #555; font-size: .9rem;
  }}

  .no-params {{
    padding: 0 1.25rem 1rem; margin: 0;
    color: #999; font-size: .85rem; font-style: italic;
  }}

  /* --- badges --- */
  .badge {{
    display: inline-block; font-size: .7rem; font-weight: 600;
    padding: .15rem .5rem; border-radius: 4px; text-transform: uppercase;
    letter-spacing: .03em;
  }}
  .badge-read {{ background: #e8f5e9; color: #2e7d32; }}
  .badge-mutate {{ background: #fff3e0; color: #e65100; }}
  .badge-required {{ background: #fce4ec; color: #c62828; }}
  .badge-optional {{ background: #e3f2fd; color: #1565c0; }}

  /* --- parameter table --- */
  table {{
    width: 100%; border-collapse: collapse;
    font-size: .85rem; margin: 0 0 .75rem;
  }}
  thead {{ background: #fafafa; }}
  th {{
    text-align: left; padding: .5rem 1rem; font-weight: 600;
    color: #555; font-size: .75rem; text-transform: uppercase;
    letter-spacing: .04em; border-bottom: 1px solid #eee;
  }}
  td {{
    padding: .45rem 1rem; border-bottom: 1px solid #f0f0f0;
    vertical-align: top;
  }}
  td code {{ font-size: .82rem; color: #333; }}
  code.default {{ color: #888; }}
  .param-desc {{ color: #666; font-size: .82rem; }}

  /* --- footer --- */
  footer {{
    text-align: center; padding: 2rem 1rem; color: #999; font-size: .8rem;
  }}
  footer a {{ color: #777; }}
</style>
</head>
<body>
<div class="hero">
  <div class="logo">&#127807;</div>
  <h1>Mint API Documentation</h1>
  <p class="tagline">MCP tools for email &amp; calendar intelligence</p>
  <div class="stats">
    <span>{tool_count} tools</span>
    <span>{read_count} read-only</span>
    <span>{mutate_count} mutating</span>
  </div>
</div>

<div class="tools">
{tool_cards}
</div>

<footer>
  <a href="/">&#8592; Back to Mint</a>
</footer>
</body>
</html>"""
