"""Read-only ONOS monitoring and benchmark evidence panel."""

import json
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

<h3>ONOS Intent Status</h3>
<pre id="rono-onos-status">Loading...</pre>

<h3>Latest Verified Benchmark</h3>
<pre id="rono-onos-results">Loading...</pre>

<p>Timing includes DDS sender startup, discovery, fixed hold,
and subsequent BGP/data-plane verification. It is not pure
DDS or ONOS latency.</p>
</section>
'''

    js = '''
<script>
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
