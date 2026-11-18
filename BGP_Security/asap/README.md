# RONO BGP security simulation POC

Based on Securing BGP ASAP: ASPA and other Post-ROV Defenses,
NDSS 2025.

Upstream source revision:
27b2a99d7f8bfffada82e57c79336bde4efcd9d7

Verified runtime:
Python 3.11.17; BGPy 13.0.13; Docker image rono-security-asap:0.1.
Resolved dependencies and runtime image inspection are retained.

## Replay

Run from the RONO repository root:

docker run --rm --network none \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -v "$PWD:/work" \
  rono-security-asap:0.1 \
  python projects/security/asap/run_scenarios.py

## Verified coverage

- Four original prefix/forged-origin scenarios.
- Four scenarios with an additional downstream observer.
- Two modeled ASPA record-availability cases.
- Three multihomed route-leak cases: BGP, ROV and ASPA.

Successful guarded replay:
results/22-security-asap/20261026T044445189754Z-replay

## Interpretation and boundaries

These are 13 deterministic regression cases, not independent
statistical trials or an Internet-scale reproduction.

ASPA adoption models validation and provider-record publication
together. No cryptographic RPKI object validation is implemented.

For route leaks, attacker-success labels indicate attraction
through the leaker; they do not establish loss or interception.

Preserve the earlier failed configuration and naming attempts.
The live FRR demonstration and larger evaluation remain unfinished.
