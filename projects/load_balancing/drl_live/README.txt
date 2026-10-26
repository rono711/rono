RONO live SDN PPO pilot environment
===================================
This is a small live episodic allocation environment, not a reproduction of
EfficientTE or Safe Load Balancing and not an optimality/safety guarantee.

Prerequisites: running CN Mininet, ONOS port 8182, fixed IPv4 fallback rules,
rl baseline/ecmp/actions modules beside this directory, Docker rono-cn-drl:0.1.
The host adapter is loaded into the running Mininet CLI and binds only
127.0.0.1:8766. It accepts authenticated fixed-schema requests; no shell or
arbitrary flow tuples are accepted. The Docker PPO client has no Docker socket
and needs --network host to reach the adapter. This local token is an engineering
control, not a completed security/authentication research evaluation.

Action mapping: 0=(0,0), 1=(0,1), 2=(1,0), 3=(1,1).
Two controlled UDP flows use cports43001/43002 to dst5401/5402; optional background
uses cport43003,dst5403. Exact protocol17 priority23000 rules are separate from
the previous TCP priority22000 manual experiment. Selected edge rules supersede
old choices; unused middle-switch rules can remain. There is no global deletion.
Paths are verified before traffic. Installation is not atomic; rule failures
abort the run and can leave partial changes requiring inspection.

Each step runs real 10-second receiver measurements after a 2-second omitted
warm-up. Sequential Popen starts have logged skew. Three finite clients and
servers are stopped on completion/error; only adapter-owned processes are
terminated. Stop previous manual traffic before running. Ports5401..5403 must
be free. Port utilization uses durationSec deltas and includes a 6-second
post-test provider settling period; its window differs from iperf's window.
Receiver UDP loss and throughput, not utilization, define satisfaction reward.
Reward=mean(min(receiverMbps/offeredMbps,1))-0.02*assignmentChangeFraction.
Background satisfaction is included to discourage starving it. These are
configured engineering choices to be frozen and evaluated, not validated SLA
weights. UDP packet loss differs from previous TCP retransmissions.

Observation: 2 normalized upcoming demands, 2 background-path load values,
8 previous trial measured transmit utilizations, 2 previous path assignments.
14 values in [0,1]. Raw utilizations retained; neural input clips above2.
A real fixed-path startup trial supplies initial measurements, excluded from
PPO transitions. Context changes each step; previous utilization is historical,
not a claim of instantaneous utilization under the next workload.
Training demands6/12/18/24Mbps, background0/8/12; evaluation context supports
held-out demands9/15/21 and background4/10/16. Dedicated evaluation harness and
repeated multi-seed baseline comparisons remain future implementation work.

smoke runs reset and 2 live steps (roughly1-3 minutes); does not train PPO.
train --steps64 runs real PPO updates and saves/reloads the policy (roughly
25-45min depending on ONOS polling). This is integration training only;
64 transitions do not establish convergence or improved network performance.
Only one client may use the serial adapter. Do not run environment checkers
against live traffic casually: they execute steps and therefore mutate policy.

The adapter must be stopped before exiting Mininet:
px rl_adapter.shutdown(); rl_adapter.server_close()
After this, exit the CLI normally. Start again after recreating Mininet.
Keep .rl-token private and out of git. No secrets are written to result logs.
