# Production readiness evidence

The reference sustained workload is 1,000 accepted messages per second for 60
seconds on the measured 16-CPU host. Scheduled admission to handler entry p95
must remain below 300 ms, with RBAC authorization
exercised on every send, 100% trace sampling and verified OTLP export. Recovery
target is at most 10 seconds, with no lost accepted messages in the tested failure
scenarios.

The current [60-second measurement](../artifacts/production-load.json) on
2026-10-04 passes all 16 gates: 1,019.20 accepted messages/sec, 259.22 ms
scheduled-admission-to-handler p95 and 152.77 ms send-RPC p95. All 61,200 messages
were accepted, delivered and fully observed with RBAC and 100% trace sampling.
Every accepted message had its exact ingress span, handler trace and all four
delivery stages exported. No RPC errors, loss or duplicate deliveries occurred;
both broker processes shut down with zero exit status.

The [four-CPU CI measurement](../artifacts/github-four-cpu-load.json) passes all
16 gates at its explicit 250/sec target: 254.84 accepted/sec, 156.87 ms scheduled
admission to handler p95, and all 15,300 messages delivered and fully traced.
This was the [GitHub acceptance run](https://github.com/Vokality/mas-framework/actions/runs/37218600198)
at commit `ee4d79c1f0326044543453637ff4516e83bf510a` on an AMD EPYC 7763 runner.
It establishes this smaller workload on that runner; the separate local report
establishes 1,000/sec on its measured host.

The [environment record](../artifacts/validation-environment.json) pins the
immutable image, 149-file runtime/test source manifest, authored frontend source,
wheel checksums and report checksum. This was an arm64 Linux container on a single
Apple M4 Max host with two broker processes, two consumers, one Redis primary,
two replicas and three Sentinels. Writes required local AOF and one replica AOF
confirmation. Earlier scoped runs retained in `artifacts/svelte-async-*.json`
failed latency at 469.29, 490.45 and 381.85 ms. The passing result includes bounded
peer validation, reduced callback/journal overhead and typed outbound metadata
reuse; these combined measurements do not isolate each optimization's effect or
establish a minimum achievable latency.

Subsequent dashboard-only styling and layout changes are pinned in the
[UI refinement record](../artifacts/dashboard-density.json), including the current
authored frontend, packaged assets, server wheel and paper/ink screenshots.
Runtime source in the accepted manifest remains unchanged; subsequent benchmark
changes add explicit CPU-relative workload selection and reporting.
The original capacity report and environment record retain their measured source
and image bindings; their frontend and wheel checksums describe that earlier run.

Runtime and integration checks passed all 675 tests on both Python 3.13.12 and
3.14.8, including actual Redis recovery. All six wheels built successfully, with
the verified Svelte bundle and CLI in the server wheel. Frontend verification
passed 19 Node tests, 26 Chromium browser tests, Svelte checks, formatting and
generated contract freshness. Browser fixtures use the actual management asset
server and model-generated intercepted API responses; the capacity gate uses
actual CLI brokers, RBAC, trace export and delivery. Ruff lint, formatting, Ty,
the dependency lock and installed dependency compatibility checks passed for the
project work. Unrelated `doekupay_test.py` and `scripts/` were excluded from lint
and formatting.

The implementation covers shared broker ownership with fencing, optimistic state
revisions, atomic reply commit and stable retry receipts, configured Redis write
confirmation, scoped OIDC operator access, HTTPS management, credential rotation
and revocation, bounded audit history with archival, and bounded health snapshots.

The Svelte dashboard has nine pages with reusable themed components, page-specific
filters, sorting, pagination, performance windows, trace drill-down and JSON
exports. Global controls select refresh cadence, pause polling, refresh manually,
change theme and manage reader access. Generated API contracts validate unknown
responses before domain state is updated. The management dashboard includes
shared fleet freshness, rolling throughput and
delivery p95 with coverage, retained charts, correlated trace waterfalls,
active/resolved alerts and actual exporter outcomes. Broker observations continue
without HTTP or browser polling. Live browser checks exercised two brokers and
complete request/reply traces; fixture checks covered narrow layouts, access
errors, storage outages, stale measurements and hostile text.

Core storage/network I/O and telemetry lifecycle are awaitable. Blocking TLS
loading, current certificate policy checks, telemetry setup/teardown and
retained-data conversion run in workers. Peer validation coalesces duplicate
certificates only within already-queued bounded snapshots; later arrivals recheck
current trust, revocation and expiry. Cancelling one caller preserves other checks.
Retained trace parsing, merging and commit JSON preparation use detached worker
data so cancellation cannot mutate live caches. The bounded FIFO span journal
detaches batches before worker conversion. Delivery retains validated tracing
metadata across the local queue, avoiding duplicate parsing while preserving wire
events and per-write authentication. Redis pools enforce explicit capacity and
bounded asynchronous acquisition. Cancellation and ownership have regression
coverage.

Recovery tests start actual isolated Redis processes: one AOF primary, two AOF
replicas and three Sentinels. They kill the primary, validate confirmed queued
messages and state revisions on the elected primary, and confirm a new durable
write. Separate tests restore a completed checksummed RDB snapshot and exercise
real process death around reply commits and broker ownership expiry.
Topology readiness confirms local and both replica AOFs. The finite-batch
recovery fixture uses a bounded 2000 ms barrier timeout to cover Redis's
one-second periodic replica fsync acknowledgement cadence and scheduling jitter;
production durability defaults and fsync count requirements remain unchanged.

Run the [load tool](../tools/validate_production.py) and retain its JSON output.
The default gate starts two separate broker processes through the actual CLI,
validates their readiness endpoints, checks cross-process trace export and
requires graceful zero-exit shutdown. Reported CPU and event-loop lag cover the
load driver; they do not represent aggregate broker CPU. The optional
`--broker-mode in_process --diagnostics` profile shares an event loop with clients
and cannot pass the production broker-isolation gate.
The load driver's pending-send bound follows the rate and latency budget
(300 sends for 1,000/sec at 300 ms, bounded to 32–4,096). Scheduled admission
waiting remains part of the end-to-end percentile. Fleet validation also requires
fresh members, retained history and traces, live delivery p95 below the same
target, and at least 95% joined latency coverage with complete counters.
Delivery diagnostics join exported stages by delivery identity and report their
accepted-message coverage. The transport write interval includes server
authentication and lease validation; it overlaps the write-start-to-receive
interval. Stage percentiles cannot be added to derive end-to-end latency.
Run the test suite, Ruff, formatting and `ty` before release. The dedicated
production validation workflow scales its throughput target to the available
CPU budget using `--rate-per-cpu 62.5`: the reference 1,000/sec divided by 16 CPUs.
The [standard public Ubuntu runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
has four vCPUs, producing a 250/sec target. CPU count is a resource scaling policy,
not a claim of equivalent speed across CPU models or linear capacity scaling.
The report retains the selected workload basis, usable CPU count, per-CPU budget
and actual target before the run starts. The default absolute `--rate 1000`
profile remains available for the reference hardware and target deployments.
Both profiles apply the same 300 ms latency, authorization, tracing, delivery,
observability, isolation, shutdown and recovery requirements. Hardware-relative
CI does not establish 1,000/sec capacity on its smaller runner.

The [Linux validation image](../tools/Dockerfile.validation) pins its Python/uv
base by digest and verifies the Redis source checksum. It runs the same gate
with private primary, replica and Sentinel processes and local AOF data inside
the container. A Docker volume used only for the JSON report does not change the
storage confirmation policy. Build with:

```bash
docker build -f tools/Dockerfile.validation -t mas-validation .
```

Run the image with a writable output directory mounted at `/workspace/artifacts`
to retain evidence.

Local proof does not certify the production environment. Before launch, verify
the actual IdP, CA rotation, host separation, Redis persistence/replication,
backup and archive storage, monitoring delivery, payloads and agent handler costs.
Repeat capacity and failure checks on the target topology. Redis acknowledgement
barriers are not a general zero-loss consensus guarantee, and RDB recovery restores
the backup's point in time. MAS provides at-least-once processing; business side
effects still require idempotency.
