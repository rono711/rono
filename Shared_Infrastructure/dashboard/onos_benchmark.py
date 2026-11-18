"""Read-only ONOS monitoring and benchmark evidence panel."""

import hmac
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def install(base, app_file):
    root = Path(app_file).resolve().parents[2]
    onos_dir = root / "Shared_Infrastructure/onos"
    results_dir = (
        root / "Shared_Infrastructure/results/21-onos-benchmark"
    )

    if str(onos_dir) not in sys.path:
        sys.path.insert(0, str(onos_dir))

    import onos_client

    parent = base.Handler

    class Handler(parent):
        def do_POST(self):
            if urlsplit(self.path).path != "/api/onos-verify":
                return super().do_POST()

            if not hmac.compare_digest(
                self.headers.get("X-RONO-Token", ""),
                base.TOKEN
            ):
                return self.reply(403, {
                    "error": "Invalid request token"
                })

            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(onos_dir / "shared_lab.py"),
                        "verify-connected"
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )

                if result.returncode:
                    return self.reply(503, {
                        "status": "FAIL",
                        "error": "Connected-state verification failed"
                    })

                decoder = json.JSONDecoder()
                payload = None

                for index, char in enumerate(result.stdout):
                    if char != "{":
                        continue
                    try:
                        candidate, _ = decoder.raw_decode(
                            result.stdout[index:]
                        )
                    except ValueError:
                        continue
                    if isinstance(candidate, dict):
                        payload = candidate
                        break

                if not isinstance(payload, dict):
                    raise RuntimeError("Invalid verification response")

                if (
                    payload.get("status") != "PASS"
                    or payload.get("expected") != "connected"
                ):
                    raise RuntimeError("Connected state not confirmed")

                return self.reply(200, {
                    "status": "PASS",
                    "expected": "connected",
                    "bgp": {
                        name: {
                            "state": next(
                                iter(info.get("peers", {}).values()),
                                {}
                            ).get("state")
                        }
                        for name, info in payload.get("bgp", {}).items()
                    },
                    "route_verified": bool(
                        payload.get("route", {}).get("paths")
                    ),
                })

            except Exception:
                return self.reply(503, {
                    "status": "FAIL",
                    "error": "ONOS verification unavailable"
                })

        def do_GET(self):
            path = urlsplit(self.path).path

            if path == "/api/onos-monitor":
                try:
                    intents = onos_client.owned_intents()
                    expected = set(onos_client.KEYS)

                    states = {
                        item.get("key"): item.get("state")
                        for item in intents
                    }

                    return self.reply(200, {
                        "status": (
                            "PASS"
                            if set(states) == expected
                            and all(
                                state == "INSTALLED"
                                for state in states.values()
                            )
                            else "CHECK"
                        ),
                        "intents": states,
                        "count": len(intents),
                    })
                except Exception:
                    return self.reply(503, {
                        "status": "UNAVAILABLE",
                        "error": "ONOS monitoring unavailable"
                    })

            if path == "/api/onos-results":
                try:
                    folders = sorted(
                        p for p in results_dir.iterdir()
                        if p.is_dir()
                        and (p / "summary.json").is_file()
                    ) if results_dir.exists() else []

                    completed = []

                    for folder in folders:
                        summary = json.loads(
                            (folder / "summary.json").read_text()
                        )
                        if (
                            summary.get("complete") is True
                            and summary.get("restoration") == "PASS"
                            and not summary.get("error")
                            and summary.get("transitions_recorded")
                                == 2 * summary.get("cycles_requested", -1)
                            and summary.get("passed")
                                == summary.get("transitions_recorded")
                        ):
                            completed.append((folder, summary))

                    if not completed:
                        return self.reply(200, {
                            "status": "NO_RESULTS"
                        })

                    folder, summary = completed[-1]

                    statistics_file = (
                        folder / "statistical_analysis.json"
                    )

                    statistics = (
                        json.loads(statistics_file.read_text())
                        if statistics_file.is_file()
                        else None
                    )

                    return self.reply(200, {
                        "status": "PASS",
                        "campaign": folder.name,
                        "summary": summary,
                        "statistics": statistics,
                    })

                except Exception:
                    return self.reply(503, {
                        "status": "UNAVAILABLE",
                        "error": "Benchmark evidence unavailable"
                    })

            return super().do_GET()

    base.Handler = Handler

    panel = '''
<section class="card">
<h2>RONO ONOS Control and Benchmark Evidence</h2>
<p>Read-only monitoring of the shared ONOS-controlled FRR transit
lab. No network configuration changes are performed here.</p>

<button onclick="refreshOnosPanel()">Refresh ONOS Status</button>
<button id="rono-onos-verify-btn"
        onclick="verifyOnosConnected()">
Verify Connected
</button>
<pre id="rono-onos-verification">Not yet verified</pre>

<h3>ONOS Intent Status</h3>
<pre id="rono-onos-status">Loading...</pre>

<h3>Latest Verified Benchmark</h3>

<div id="rono-onos-metrics"
     style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0">
</div>

<div id="rono-onos-table"></div>

<details style="margin-top:16px">
<summary>View raw experimental evidence</summary>
<pre id="rono-onos-results">Loading...</pre>
</details>

<p>Timing includes DDS sender startup, discovery, fixed hold,
and subsequent BGP/data-plane verification. It is not pure
DDS or ONOS latency.</p>
</section>
'''

    js = '''
<script>
async function verifyOnosConnected() {
  const button = document.getElementById("rono-onos-verify-btn");
  const output = document.getElementById("rono-onos-verification");

  button.disabled = true;
  output.textContent = "Verifying BGP and data-plane connectivity...";

  try {
    const response = await fetch("/api/onos-verify", {
      method: "POST",
      headers: {
        "X-RONO-Token": token
      }
    });

    const data = await response.json();

    if (!response.ok || data.status !== "PASS") {
      throw new Error(data.error || "Verification failed");
    }

    output.textContent = JSON.stringify(data, null, 2);

  } catch (error) {
    output.textContent = "Verification failed: " + error.message;
  } finally {
    button.disabled = false;
  }
}

async function refreshOnosPanel() {
  const status = document.getElementById("rono-onos-status");
  const results = document.getElementById("rono-onos-results");

  try {
    const response = await fetch("/api/onos-monitor");
    const data = await response.json();
    status.textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    status.textContent = "Monitoring unavailable";
  }

  try {
    const response = await fetch("/api/onos-results");
    const data = await response.json();
    results.textContent = JSON.stringify(data, null, 2);

    const metrics = document.getElementById("rono-onos-metrics");
    const table = document.getElementById("rono-onos-table");

    if (response.ok && data.status === "PASS" && data.summary) {
      const summary = data.summary;
      const actions = summary.summary || [];

      const cards = [
        ["Transitions", `${summary.passed}/${summary.transitions_recorded}`],
        ["Restoration", summary.restoration],
        ...actions.map(a => [
          `${a.action} median`,
          a.median_ms == null ? "N/A" : `${a.median_ms.toFixed(2)} ms`
        ])
      ];

      metrics.replaceChildren();

      cards.forEach(([label, value]) => {
        const card = document.createElement("div");
        card.style.cssText =
          "background:#101b2d;border:1px solid #33445e;border-radius:8px;padding:14px";

        const heading = document.createElement("div");
        heading.textContent = label;
        heading.style.cssText = "font-size:12px;color:#a8b8cc";

        const number = document.createElement("strong");
        number.textContent = value;
        number.style.cssText = "display:block;font-size:19px;margin-top:7px";

        card.append(heading, number);
        metrics.appendChild(card);
      });

      const stats = data.statistics?.actions || {};
      const rows = Object.entries(stats);

      if (rows.length) {
        const format = v =>
          typeof v === "number" ? v.toFixed(2) : "N/A";

        const header =
          "<tr><th>Action</th><th>Mean (ms)</th><th>SD (ms)</th>" +
          "<th>95% CI (ms)</th></tr>";

        const body = rows.map(([action, value]) => {
          const ci = value.ci95_mean_ms || [];
          return `<tr><td>${action}</td><td>${format(value.mean_ms)}</td>` +
                 `<td>${format(value.std_ms)}</td>` +
                 `<td>[${format(ci[0])}, ${format(ci[1])}]</td></tr>`;
        }).join("");

        table.innerHTML =
          `<div style="overflow-x:auto"><table style="width:100%;text-align:left">` +
          header + body + "</table></div>";
      } else {
        table.textContent = "Statistical analysis unavailable";
      }
    } else {
      metrics.replaceChildren();
      table.textContent = "No verified benchmark available";
    }
  } catch (error) {
    results.textContent = "Results unavailable";
  }
}

refreshOnosPanel();
setInterval(refreshOnosPanel, 10000);
</script>
'''

    base.PAGE = (
        base.PAGE.replace("</main>", panel + "</main>", 1)
        .replace("</html>", js + "</html>", 1)
    )
