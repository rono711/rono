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
    customer = integer(data, "customer", 65005, 1, 4294967295)
    provider = integer(data, "provider", 65002, 1, 4294967295)
    version = integer(data, "version", 1, 1, 18446744073709551615)
    action = data.get("action", "authorize")
    if action not in ("authorize", "revoke"):
        raise ValueError("Invalid policy action")

    item = base.inspect(role)
    if item is not None:
        if item["State"]["Running"]:
            raise ValueError(f"{role} already running; stop it first")
        base.docker("rm", base.NAMES[role])

    command = [
        "run", "-d", "-i",
        "--name", base.NAMES[role],
        "--label", f"{base.OWNER}=true",
        "--network", "host", "--ipc", "host",
        "-v", f"{APP_DIR}:/app:ro",
        base.IMAGE,
        "bash", "-c", base.RUN_SCRIPT, "rono",
        role, "--domain", str(domain),
    ]
    if role == "publisher":
        command += [
            "--customer", str(customer),
            "--provider", str(provider),
            "--version", str(version),
            "--action", action,
        ]
    base.docker(*command)


def policy_status(role):
    item = base.inspect(role)
    if item is None:
        return {"state": "not started", "count": 0, "logs": ""}

    result = base.docker("logs", "--tail", "200", base.NAMES[role])
    logs = result.stdout + result.stderr
    expected = "cache_updated" if role == "subscriber" else "write_return"
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
<input id="domain" type="number" min="0" max="200" value="72"></label>
<label>Customer ASN
<input id="customer" type="number" min="1" value="65005"></label>
<label>Provider ASN
<input id="provider" type="number" min="1" value="65002"></label>
<label>Version
<input id="version" type="number" min="1" value="1"></label>
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
customer:document.getElementById("customer").value,
provider:document.getElementById("provider").value,
version:document.getElementById("version").value,
action:document.getElementById("action").value
"""
if old not in page:
    raise RuntimeError("Dashboard template changed; inspect before updating")
page = page.replace(old, new)

page = page.replace(
    "Fast DDS Python control panel · HelloWorld baseline",
    "Typed policy updates · Authorization, revocation and version checks",
)
page = page.replace(
    "Publisher waits for discovery and exits after sending.",
    "Publisher writes one policy and exits after a short delivery window.",
)
page = page.replace(
    "Messages use the bundled example's default QoS.",
    "QoS: RELIABLE · TRANSIENT_LOCAL · KEEP_LAST depth 20.",
)
page = page.replace(
    "Counters describe this run; they do not establish a reliability guarantee.",
    "Counters show recent write-return/cache-update events, not delivery "
    "acknowledgments. Subscriber cache is in memory and resets on restart. "
    "ASN filtering is not enabled yet.",
)
page = page.replace('+" messages"', '+" events"')
base.PAGE = page

if __name__ == "__main__":
    print(f"RONO Policy dashboard: http://127.0.0.1:{PORT}", flush=True)
    server = base.ThreadingHTTPServer(("127.0.0.1", PORT), base.Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped; use Stop buttons before closing.")
    finally:
        server.server_close()