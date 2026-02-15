"""Extract session metadata from Claude Code statusline JSON input.

Reads JSON from stdin, outputs shell variable assignments to stdout.
Used by the statusline command in ~/.claude/settings.json.
"""
import json
import os
import sys

d = json.load(sys.stdin)

p = d.get("cwd", "")
parts = p.rstrip("/").split("/")
cwd = "/".join(parts[-2:]) if len(parts) >= 2 else p

m = d.get("model", {})
model = m.get("display_name", "") if isinstance(m, dict) else m

c = d.get("cost", {})
cost = c.get("total_cost_usd", 0)
dur = c.get("total_duration_ms", 0)
mins = int(dur / 60000)

ctx = d.get("context_window", {})
pct = ctx.get("used_percentage", 0)
lines_add = c.get("total_lines_added", 0)
lines_rem = c.get("total_lines_removed", 0)

tin = ctx.get("total_input_tokens", 0)
tout = ctx.get("total_output_tokens", 0)
tink = f"{tin/1000:.1f}k" if tin >= 1000 else str(tin)
toutk = f"{tout/1000:.1f}k" if tout >= 1000 else str(tout)

ver = d.get("version", "")
apims = c.get("total_api_duration_ms", 0)
apisec = int(apims / 1000)

# Extract slug from transcript
tp = d.get("transcript_path", "")
slug = ""
if tp and os.path.exists(tp):
    try:
        with open(tp) as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("slug"):
                        slug = e["slug"]
                        break
                except (json.JSONDecodeError, KeyError):
                    pass
    except Exception:
        pass

# Shell-safe quoting
def q(s):
    return str(s).replace('"', '\\"')

print(f'cwd="{q(cwd)}" model="{q(model)}" cost="{cost:.2f}" mins="{mins}" '
      f'ctxpct="{pct}" ladd="{lines_add}" lrem="{lines_rem}" '
      f'tink="{q(tink)}" toutk="{q(toutk)}" ver="{q(ver)}" '
      f'apisec="{apisec}" slug="{q(slug)}"')
