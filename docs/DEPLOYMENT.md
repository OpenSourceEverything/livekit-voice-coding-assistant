# Deployment boundaries

The current container is a synthetic-source demonstration. It is not a remote
desktop connector. Hosting an agent elsewhere does not grant it access to a
developer's computer or repositories.

## Worker-only hosting

An agent worker can run in a container on private infrastructure while LiveKit
Cloud continues to provide media transport. The worker registers over an
outbound WebSocket; it does not require public inbound application ports.
Restrict any health endpoint to private monitoring. Size the VM from measured
CPU, memory, concurrent calls, and model configuration rather than assuming
the smallest instance is sufficient.

A general-purpose VPS, including a DigitalOcean droplet, is a possible lab
target, not a tested configuration for this project. Start with synthetic
source, protected credentials, unprivileged execution, restart policy, bounded
logs, health monitoring, graceful draining, and a rollback test.

[Official agent deployment guide](https://docs.livekit.io/deploy/custom/deployments/)
(checked 2026-10-22).

## Full media-stack hosting

Self-hosting LiveKit Server is additional work: public-IP/UDP routing,
domain and trusted TLS, reverse proxy, TURN connectivity, and operational
monitoring. Redis is recommended for production. Self-hosted SIP is another
service to configure and qualify. Media throughput, call concurrency, failure
recovery, provider costs, and network restrictions need physical tests.

[Official server deployment guide](https://docs.livekit.io/transport/self-hosting/deployment/)
(checked 2026-10-22).

## Current recommendation

Keep the existing Cloud demonstration while qualifying real phone behavior.
Treat worker-only VPS deployment as a later bounded lab. Do not provision paid
resources or claim self-hosting/scaling experience until the deployment,
measurements, recovery, and evidence actually exist.
