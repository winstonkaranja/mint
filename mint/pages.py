"""HTML pages for Mint's hosted multi-provider flow."""

from __future__ import annotations

import html as _html


def provider_picker_html(txn_id: str, csrf_token: str) -> str:
    """OAuth consent page: pick Google or Microsoft."""
    txn_id = _html.escape(txn_id, quote=True)
    csrf_token = _html.escape(csrf_token, quote=True)
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
  .btn svg {{ flex-shrink: 0; }}
  .divider {{ color: #999; font-size: .8rem; margin: .5rem 0; }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">&#127807;</div>
  <h1>Mint</h1>
  <p class="subtitle">Choose your email &amp; calendar provider</p>

  <form method="post">
    <input type="hidden" name="txn_id" value="{txn_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <input type="hidden" name="action" value="approve">
    <input type="hidden" name="provider" value="google">
    <button type="submit" class="btn">
      <svg width="20" height="20" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59a14.5 14.5 0 0 1 0-9.18l-7.98-6.19a24.001 24.001 0 0 0 0 21.56l7.98-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
      Sign in with Google
    </button>
  </form>

  <div class="divider">or</div>

  <form method="post">
    <input type="hidden" name="txn_id" value="{txn_id}">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <input type="hidden" name="action" value="approve">
    <input type="hidden" name="provider" value="microsoft">
    <button type="submit" class="btn">
      <svg width="20" height="20" viewBox="0 0 21 21"><rect x="1" y="1" width="9" height="9" fill="#f25022"/><rect x="11" y="1" width="9" height="9" fill="#7fba00"/><rect x="1" y="11" width="9" height="9" fill="#00a4ef"/><rect x="11" y="11" width="9" height="9" fill="#ffb900"/></svg>
      Sign in with Microsoft
    </button>
  </form>
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
  <div class="grid">
    <div><strong>read_inbox</strong>Recent emails</div>
    <div><strong>search_emails</strong>Find by query</div>
    <div><strong>draft_email</strong>Save a draft</div>
    <div><strong>list_events</strong>Calendar view</div>
    <div><strong>create_event</strong>New meeting</div>
    <div><strong>read_email</strong>Full message</div>
    <div><strong>get_event</strong>Event details</div>
    <div><strong>update_event</strong>Change event</div>
    <div><strong>delete_event</strong>Remove event</div>
  </div>

  <p style="margin-top:1.5rem; color:#666; font-size:.9rem;">
    Plus 6 built-in prompts: daily briefing, inbox prioritization, draft reply,
    schedule review, availability finder, and email summary.
  </p>
</section>

<footer>Mint &mdash; open-source MCP server for email &amp; calendar</footer>
</body>
</html>"""
