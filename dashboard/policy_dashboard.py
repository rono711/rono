import json
from pathlib import Path

import app as base

PORT = 8502
APP_DIR = (
    Path(__file__).resolve().parent.parent
    / "projects/heta/fastdds"
)

base.IMAGE = "rono/policy-python:0.1"
base.NAMES = {
    "subscriber": "rono-policy-subscriber",
    "publisher": "rono-policy-publisher",
}

base.RUN_SCRIPT = r"""
set -e
source /opt/fastdds-python/install/setup.bash
export PYTHONPATH="/opt/rono/policy/build:${PYTHONPATH:-}"
exec /usr/bin/python3 -u /app/policy_app.py "$@"
"""


def integer(data, name, default, minimum, maximum):
    value = int(data.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}: allowed range {minimum}–{maximum}")
    return value


def start_policy(role, data):
    domain = integer(data, "domain", 72, 0, 200)
    samples = integer(data, "samples", 1, 1, 100)
    interval = float(data.get("interval", 0.1))
    if not 0.01 <= interval <= 2:
        raise ValueError("Interval must be between 0.01 and 2 seconds")
    hold_seconds = integer(data, "hold_seconds", 60, 5, 300)
    reliability = data.get("reliability", "reliable")
    durability = data.get("durability", "transient_local")
    depth = integer(data, "history_depth", 20, 1, 100)
    if reliability not in ("reliable", "best_effort"):
        raise ValueError("Invalid reliability")
    if durability not in ("transient_local", "volatile"):
        raise ValueError("Invalid durability")

    filter_text = str(data.get("filter_customer", "")).strip()
    filter_customer = None
    if filter_text:
        filter_customer = int(filter_text)
        if not 1 <= filter_customer <= 4294967295:
            raise ValueError("Invalid subscriber filter ASN")

    customer = integer(data, "customer", 65005, 1, 4294967295)
    provider = integer(data, "provider", 65002, 1, 4294967295)
    version = integer(data, "version", 1, 1, 18446744073709551615)

    if version + samples - 1 > 18446744073709551615:
        raise ValueError("Version range exceeds unsigned 64-bit limits")
    action = data.get("action", "authorize")
    if action not in ("authorize", "revoke"):
        raise ValueError("Invalid policy action")

    if not (APP_DIR / "policy_app.py").is_file():
        raise RuntimeError(f"Missing application: {APP_DIR / 'policy_app.py'}")

    item = base.inspect(role)
    if item is not None:
        if item["State"]["Running"]:
            raise ValueError(f"{role} already running; stop it first")
        base.docker("rm", base.NAMES[role])

    command = [
        "run", "-d", "-i",
        "--name", base.NAMES[role],
        "--label", f"{base.OWNER}=true",
        "--network", "host",
        "--ipc", "host",
        "-v", f"{APP_DIR}:/app:ro",
        base.IMAGE,
        "bash", "-c", base.RUN_SCRIPT, "rono",
        role,
        "--domain", str(domain),
        "--reliability", reliability,
        "--durability", durability,
        "--history-depth", str(depth),
    ]

    if role == "publisher":
        command += [
            "--customer", str(customer),
            "--provider", str(provider),
            "--version", str(version),
            "--action", action,
            "--samples", str(samples),
            "--interval", str(interval),
            "--hold-seconds", str(hold_seconds),
        ]
    elif filter_customer is not None:
        command += ["--filter-customer", str(filter_customer)]

    base.docker(*command)


def policy_status(role):
    item = base.inspect(role)
    if item is None:
        return {"state": "not started", "count": 0, "logs": ""}

    result = base.docker("logs", "--tail", "200", base.NAMES[role])
    logs = result.stdout + result.stderr

    expected = (
        "cache_updated" if role == "subscriber" else "write_return"
    )
    count = 0

    for line in logs.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue

        if isinstance(record, dict) and record.get("event") == expected:
            count += 1

    return {
        "state": item["State"]["Status"],
        "count": count,
        "logs": logs,
    }


base.start = start_policy
base.status = policy_status

controls = """
<div class="controls">
<label>Domain ID
<input id="domain" type="number"
       min="0" max="200" value="72"></label>

<label>Subscriber filter ASN
<input id="filter_customer" type="number"
       min="1" max="4294967295" value="65005"
       placeholder="Blank = all ASNs"></label>

<label>Customer ASN
<input id="customer" type="number"
       min="1" max="4294967295" value="65005"></label>

<label>Provider ASN
<input id="provider" type="number"
       min="1" max="4294967295" value="65002"></label>

<label>Version
<input id="version" type="text"
       inputmode="numeric" value="1"></label>

<label>Reliability
<select id="reliability" style="padding:10px;font:inherit">
<option value="reliable">Reliable</option>
<option value="best_effort">Best Effort</option>
</select></label>

<label>Durability
<select id="durability" style="padding:10px;font:inherit">
<option value="transient_local">Transient Local</option>
<option value="volatile">Volatile</option>
</select></label>

<label>History depth per ASN pair
<input id="history_depth" type="number"
       min="1" max="100" value="20"></label>

<label>Publisher hold seconds
<input id="hold_seconds" type="number"
       min="5" max="300" value="60"></label>

<label>Sample count
<input id="samples" type="number"
       min="1" max="100" value="1"></label>

<label>Interval seconds
<input id="interval" type="number"
       min="0.01" max="2" step="0.01" value="0.1"></label>

<label>Action
<select id="action" style="padding:10px;font:inherit">
<option value="authorize">Authorize</option>
<option value="revoke">Revoke</option>
</select></label>
</div>
"""

page = base.PAGE

start = page.index('<div class="controls">')
end = page.index("</div>", start) + len("</div>")
page = page[:start] + controls + page[end:]

old = 'label:document.getElementById("label").value'
new = """
filter_customer:document.getElementById("filter_customer").value,
customer:document.getElementById("customer").value,
provider:document.getElementById("provider").value,
version:document.getElementById("version").value,
action:document.getElementById("action").value,
samples:document.getElementById("samples").value,
interval:document.getElementById("interval").value,
hold_seconds:document.getElementById("hold_seconds").value,
reliability:document.getElementById("reliability").value,
durability:document.getElementById("durability").value,
history_depth:document.getElementById("history_depth").value
"""

if old not in page:
    raise RuntimeError("Dashboard template changed; inspect dashboard/app.py")

page = page.replace(old, new)
page = page.replace(
    "<title>RONO DDS Dashboard</title>",
    "<title>RONO Policy Dashboard</title>",
)
page = page.replace(
    "Fast DDS Python control panel · HelloWorld baseline",
    "Typed policy updates · Authorization, revocation and ASN filtering",
)

# Replace the instructions below the cards with policy-specific guidance.
instructions_start = page.index("<p>Start the subscriber first")
instructions_end = page.index("</main>", instructions_start)

page = (
    page[:instructions_start]
    + """
<p>Start the subscriber first, then publish a policy.
Publisher sends the selected sample count with increasing versions, then stays alive for the hold time.
Subscriber remains running until stopped.</p>

<p>Subscriber filter is applied at Start; blank accepts all ASNs.
After changing filter, domain or QoS, Stop and Start the subscriber.
Customer ASN and Provider ASN describe the policy being published.</p>

<p>QoS selections apply when each container starts.
For the first test, use identical QoS settings on both endpoints.
History uses KEEP_LAST; depth applies per customer–provider pair.
Transient Local history is available only while its writer remains alive.
Counters show events from the last 200 log lines:
publisher write-return events and subscriber accepted cache updates.
Write-return events are not delivery acknowledgments.</p>

<p>Subscriber cache is in memory and resets on restart.
Old or duplicate versions are ignored per customer–provider pair
while the subscriber remains running.</p>
"""
    + page[instructions_end:]
)

page = page.replace('+" messages"', '+" events"')

page = page.replace("<h1>RONO</h1>", """
<header class="rono-brand">
  <div class="rono-eyebrow">UNIFIED RESEARCH PLATFORM</div>
  <h1>RONO</h1>
  <p class="rono-fullname">Routing Orchestration and
    <span>Network Optimization</span></p>
  <div class="rono-accent"></div>
</header>
""", 1)

page = page.replace("</style>", """
.rono-brand{padding:12px 0 8px}
.rono-eyebrow{font-size:11px;font-weight:700;letter-spacing:3px;
color:#7dd3fc;margin-bottom:8px}
.rono-brand h1{font-size:clamp(48px,8vw,76px);line-height:1.1;
letter-spacing:5px;margin:0;font-weight:800;color:#5ae0b5;
background:linear-gradient(110deg,#5ae0b5,#7dd3fc,#c4b5fd);
-webkit-background-clip:text;background-clip:text;
-webkit-text-fill-color:transparent;width:fit-content}
.rono-fullname{font-size:clamp(17px,3vw,23px);font-weight:500;
color:#e7edf7;margin:12px 0 16px;line-height:1.5}
.rono-fullname span{color:#7dd3fc}
.rono-accent{height:3px;width:90px;border-radius:4px;
background:linear-gradient(90deg,#5ae0b5,#7dd3fc,#c4b5fd)}
</style>
""", 1)


# Final RONO branding
import re

rono_header = """
<header class="rono-final-brand">
<svg viewBox="0 0 520 180" role="img"
     aria-label="RONO: Routing Orchestration and Network Optimization">
  <text x="0" y="174" textLength="520" lengthAdjust="spacingAndGlyphs"
        font-family="Arial, sans-serif" font-size="160" font-weight="800"><tspan fill="#ef4444">RO</tspan><tspan fill="#22c55e">NO</tspan></text>
  <text x="0" y="22" textLength="520" lengthAdjust="spacingAndGlyphs"
        fill="#e7edf7" font-family="Arial, sans-serif"
        font-size="20">Routing Orchestration and Network Optimization</text>
</svg>
</header>
"""

page, replacements = re.subn(
    r'<header\b[^>]*class="[^"]*rono-brand[^"]*"[^>]*>.*?</header>',
    lambda match: rono_header,
    page,
    count=1,
    flags=re.S,
)
if replacements != 1:
    raise RuntimeError("Could not locate the RONO branding header")

page = page.replace("</style>", """
.rono-final-brand{width:min(100%,440px);margin:12px 0 24px}
.rono-final-brand svg{display:block;width:100%;height:auto}
</style>
""", 1)


# Bold gradient RONO wordmark
import re

gradient_header = """
<header class="rono-gradient-brand">
<svg viewBox="0 0 520 185" role="img"
     aria-label="Routing Orchestration and Network Optimization — RONO">
  <defs>
    <linearGradient id="rono-bold-gradient"
                    x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F42A41"/>
      <stop offset="30%" stop-color="#C73545"/>
      <stop offset="65%" stop-color="#287D61"/>
      <stop offset="100%" stop-color="#006A4E"/>
    </linearGradient>
  </defs>
  <text x="0" y="22" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="#e7edf7" font-family="Arial, sans-serif"
        font-size="20">Routing Orchestration and Network Optimization</text>
  <text x="0" y="174" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="url(#rono-bold-gradient)"
        font-family="Arial Black, Arial, sans-serif"
        font-size="160" font-weight="900">RONO</text>
</svg>
</header>
"""

page, count = re.subn(
    r'<header\b[^>]*class="[^"]*rono-[^"]*"[^>]*>.*?</header>',
    lambda match: gradient_header,
    page, count=1, flags=re.S,
)
if count != 1:
    raise RuntimeError("RONO header not found")

page = page.replace("</style>", """
.rono-gradient-brand{width:min(100%,440px);margin:12px 0 24px}
.rono-gradient-brand svg{display:block;width:100%;height:auto}
</style>
""", 1)


# Bold gradient RONO wordmark
import re

gradient_header = """
<header class="rono-gradient-brand">
<svg viewBox="0 0 520 185" role="img"
     aria-label="Routing Orchestration and Network Optimization — RONO">
  <defs>
    <linearGradient id="rono-bold-gradient"
                    x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F42A41"/>
      <stop offset="30%" stop-color="#C73545"/>
      <stop offset="65%" stop-color="#287D61"/>
      <stop offset="100%" stop-color="#006A4E"/>
    </linearGradient>
  </defs>
  <text x="0" y="22" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="#e7edf7" font-family="Arial, sans-serif"
        font-size="20">Routing Orchestration and Network Optimization</text>
  <text x="0" y="174" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="url(#rono-bold-gradient)"
        font-family="Arial Black, Arial, sans-serif"
        font-size="160" font-weight="900">RONO</text>
</svg>
</header>
"""

page, count = re.subn(
    r'<header\b[^>]*class="[^"]*rono-[^"]*"[^>]*>.*?</header>',
    lambda match: gradient_header,
    page, count=1, flags=re.S,
)
if count != 1:
    raise RuntimeError("RONO header not found")

page = page.replace("</style>", """
.rono-gradient-brand{width:min(100%,440px);margin:12px 0 24px}
.rono-gradient-brand svg{display:block;width:100%;height:auto}
</style>
""", 1)


# Bold gradient RONO wordmark
import re

gradient_header = """
<header class="rono-gradient-brand">
<svg viewBox="0 0 520 185" role="img"
     aria-label="Routing Orchestration and Network Optimization — RONO">
  <defs>
    <linearGradient id="rono-bold-gradient"
                    x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F42A41"/>
      <stop offset="30%" stop-color="#C73545"/>
      <stop offset="65%" stop-color="#287D61"/>
      <stop offset="100%" stop-color="#006A4E"/>
    </linearGradient>
  </defs>
  <text x="0" y="22" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="#e7edf7" font-family="Arial, sans-serif"
        font-size="20">Routing Orchestration and Network Optimization</text>
  <text x="0" y="174" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="url(#rono-bold-gradient)"
        font-family="Arial Black, Arial, sans-serif"
        font-size="160" font-weight="900">RONO</text>
</svg>
</header>
"""

page, count = re.subn(
    r'<header\b[^>]*class="[^"]*rono-[^"]*"[^>]*>.*?</header>',
    lambda match: gradient_header,
    page, count=1, flags=re.S,
)
if count != 1:
    raise RuntimeError("RONO header not found")

page = page.replace("</style>", """
.rono-gradient-brand{width:min(100%,440px);margin:12px 0 24px}
.rono-gradient-brand svg{display:block;width:100%;height:auto}
</style>
""", 1)


# Bold gradient RONO wordmark
import re

gradient_header = """
<header class="rono-gradient-brand">
<svg viewBox="0 0 520 185" role="img"
     aria-label="Routing Orchestration and Network Optimization — RONO">
  <defs>
    <linearGradient id="rono-bold-gradient"
                    x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F42A41"/>
      <stop offset="30%" stop-color="#C73545"/>
      <stop offset="65%" stop-color="#287D61"/>
      <stop offset="100%" stop-color="#006A4E"/>
    </linearGradient>
  </defs>
  <text x="0" y="22" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="#e7edf7" font-family="Arial, sans-serif"
        font-size="20">Routing Orchestration and Network Optimization</text>
  <text x="0" y="174" textLength="520"
        lengthAdjust="spacingAndGlyphs"
        fill="url(#rono-bold-gradient)"
        font-family="Arial Black, Arial, sans-serif"
        font-size="160" font-weight="900">RONO</text>
</svg>
</header>
"""

page, count = re.subn(
    r'<header\b[^>]*class="[^"]*rono-[^"]*"[^>]*>.*?</header>',
    lambda match: gradient_header,
    page, count=1, flags=re.S,
)
if count != 1:
    raise RuntimeError("RONO header not found")

page = page.replace("</style>", """
.rono-gradient-brand{width:min(100%,440px);margin:12px 0 24px}
.rono-gradient-brand svg{display:block;width:100%;height:auto}
</style>
""", 1)

base.PAGE = page








from automated_tests import install as install_automated_tests
install_automated_tests(base, APP_DIR)

from bgp_dashboard import install as install_bgp_dashboard
install_bgp_dashboard(base, APP_DIR)

from path_dashboard import install as install_path_dashboard
install_path_dashboard(base, APP_DIR)

if __name__ == "__main__":
    print(
        f"RONO Policy dashboard: http://127.0.0.1:{PORT}",
        flush=True,
    )
    server = base.ThreadingHTTPServer(
        ("127.0.0.1", PORT),
        base.Handler,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped; DDS containers retain their current state.")
    finally:
        server.server_close()