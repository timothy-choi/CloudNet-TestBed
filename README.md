# CloudNet — Reliability Experiment Runner for Cloud Network Topologies

CloudNet lets you run reliability experiments on cloud infrastructure using simple YAML scenarios. It turns a declarative network topology into an experiment: deploy, validate connectivity, inject failure, detect drift, reconcile recovery, and return a clear pass/fail result.

**Primary providers:** **Mock** for local and CI-safe runs, and **AWS** for real VPC/EC2 experiments when credentials and cost controls are configured.

**Languages:** Python (FastAPI, topology compiler, providers), Bash (demos).

---

## 1-Minute Demo

Run the mock experiment locally with no cloud credentials:

```bash
git clone https://github.com/timothy-choi/CloudNet-TestBed.git
cd CloudNet-TestBed
make install
```

Terminal 1:

```bash
CLOUDNET_PROVIDER=mock make dev
```

Terminal 2:

```bash
make demo-mock
```

Expected proof points:

```text
✔ validate PASSED
✖ backend failure injected
✔ drift detected
✔ reconcile repaired system
✔ validate PASSED
```

The demo uses the mock provider, so it exercises the control plane and scenario lifecycle without creating cloud resources.

---

## Expected Output

The demo prints JSON for each API step, then a lifecycle line and final summary. The important shape should look like:

```text
CloudNet mock reliability experiment
==> Checking API
==> Creating topology
==> Planning
==> Deploying
==> Validating baseline connectivity
==> Injecting backend node-down
==> Validating drifted connectivity
==> Detecting drift
==> Reconciling
==> Validating after reconcile
==> Event timeline

Timeline: PLAN -> DEPLOY -> VALIDATE(PASS) -> FAILURE -> VALIDATE(FAIL) -> DRIFT -> RECONCILE -> VALIDATE(PASS)

==> Demo summary
Experiment: mock failure and recovery
Topology ID: 1
Baseline validation: PASSED
After node-down: FAILED
Drift detected: true
Reconcile: RECONCILED
After reconcile: PASSED
```

Terminal view:

```text
$ make demo-mock
CloudNet mock reliability experiment
...
✔ validate PASSED
✖ backend failure injected
✔ drift detected
✔ reconcile repaired system
✔ validate PASSED
```

---

## What Happens Internally

CloudNet:

1. Compiles the YAML topology into a provider-shaped plan.
2. Deploys infrastructure through the selected provider.
3. Validates connectivity between declared nodes.
4. Injects a failure, such as stopping the backend node.
5. Detects drift between desired state and provider state.
6. Reconciles the system and validates recovery.

---

## Why this is interesting

- Models control plane behavior instead of only checking static config.
- Simulates failure scenarios with repeatable scenario files.
- Validates that the system recovers, not just that resources exist.
- Runs locally with the mock provider and can run against real AWS infrastructure.

---

## Limitations

- CloudNet is not a production orchestrator.
- Failure types are intentionally limited.
- The networking model is simplified and does not cover arbitrary cloud graphs.
- AWS is the primary real-infrastructure provider; OpenStack and Proxmox paths are experimental/legacy.

---

## What problem does CloudNet solve?

CloudNet gives you one repeatable workflow to:

- **Define a cloud topology** with hosts, links, subnets, and firewall rules.
- **Run a failure experiment** against that topology.
- **Validate connectivity** before, during, and after the failure.
- **Detect drift** between desired topology and provider state.
- **Reconcile recovery** where the provider supports repair.

---

## Quick Start

Make the CLI runnable from this shell:

```bash
export PATH="$PWD/scripts:$PATH"
```

Start the API with the **mock** provider:

```bash
CLOUDNET_PROVIDER=mock make dev
```

In another terminal, run the first experiment workflow:

```bash
make demo-mock
```

The mock demo exercises the control plane without cloud credentials or billable resources:

```text
PLAN -> DEPLOY -> VALIDATE(PASS) -> FAILURE -> VALIDATE(FAIL) -> DRIFT -> RECONCILE -> VALIDATE(PASS)
```

If dependencies are not installed yet, run **`make install`** first.

---

## Scenario YAML

CloudNet scenarios are intentionally small. A failure/recovery experiment reads like this:

```yaml
scenario:
  name: backend_failure_test
steps:
  - validate: all
  - fail:
      node: backend
  - reconcile: true
```

Full runnable examples include the topology and provider-safe defaults, for example **`examples/backend-failure.yaml`**.

Example output:

```text
✔ validate PASSED
✖ backend failure injected
✔ drift detected
✔ reconcile repaired system
✔ validate PASSED
```

---

## Run a real experiment on AWS (optional)

Use the mock workflow first, then switch to AWS when you are ready to create real cloud resources.

Prerequisites:

- AWS CLI configured and authenticated.
- AWS credentials available to the API process as **`AWS_ACCESS_KEY_ID`** and **`AWS_SECRET_ACCESS_KEY`**.
- AWS region set.
- A valid AMI ID for that region.
- AWS Systems Manager access for connectivity validation; set **`AWS_INSTANCE_PROFILE_NAME`** to an instance profile with **AmazonSSMManagedInstanceCore**.

In one terminal, start the API with AWS selected:

```bash
export CLOUDNET_PROVIDER=aws
export AWS_REGION=us-west-2
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
export AWS_DEFAULT_AMI_ID=ami-0123456789abcdef0
export AWS_DEFAULT_INSTANCE_TYPE=t3.micro
export AWS_INSTANCE_PROFILE_NAME=your-ssm-instance-profile
export AWS_MAX_INSTANCES_PER_DEPLOY=3
export AWS_ALLOW_CREATE_INSTANCES=true

make dev
```

In another terminal, run the scenario through the CLI:

```bash
export PATH="$PWD/scripts:$PATH"
cloudnet run examples/backend-failure.yaml --cleanup
```

That run deploys EC2 instances, validates connectivity, injects a backend failure, detects drift, reconciles the system, validates recovery, and cleans up deployment resources after the run.

**Cost safety:** this path creates real AWS resources. CloudNet defaults to **`t3.micro`**, does **not** create NAT Gateways, does **not** provision ALBs by default, enforces the configured max node limit, and supports cleanup with:

```bash
cloudnet run examples/backend-failure.yaml --cleanup
```

Leave **`AWS_ALLOW_CREATE_INSTANCES=false`** until you intentionally want EC2 creation.

---

## Why use CloudNet?

- **Test failure recovery** without manually wiring VPCs, subnets, security groups, and instances for every experiment.
- **Validate cloud network connectivity** from declarative topology definitions.
- **Reproduce reliability experiments locally** with mock mode before using real cloud resources.
- **Run the same experiment model** against AWS when you are ready for production-like infrastructure.

---

## Run an experiment

Use **`make demo-mock`** for the guided local walkthrough. Use the CLI when you want to run a tracked scenario file:

```bash
cloudnet run examples/backend-failure.yaml
```

For a shorter deploy-and-validate example:

```bash
cloudnet run examples/simple-connectivity.yaml
```

Use **`cloudnet`** after adding `scripts/` to your `PATH`, or run **`./scripts/cloudnet`** from the project root. Exit code **0** means the scenario **PASSED**; exit code **1** means **FAILED** or an HTTP error. Use **`cloudnet run --json`** for the raw API response.

Scenario runs persist structured step results and emit **`SCENARIO_RUN`** and related events on the topology timeline.

---

## What CloudNet is not

- **Not a Terraform replacement** — it can compile plans and export Terraform for inspection, but it is not a general-purpose IaC workflow engine.
- **Not an AWS console wrapper** — it exposes a focused API and CLI for lab-style networks and experiments.
- **Not a production orchestrator** — it does not replace Kubernetes, CI platforms, or fleet managers for running services at scale.
- **It is a reliability testbed** — a control plane for defining intent, provisioning lab infrastructure, injecting faults, observing drift, reconciling where supported, and recording outcomes.

---

## Non-functional validation

Scenarios may declare optional **`requirements`** so experiments gate on measurable reliability targets—not only on correct step outcomes.

CloudNet evaluates:

- **Availability** — success rate across ICMP connectivity checks (ping tests) aggregated over all **`validate`** steps.
- **Latency** — average and **p95** latency computed from per-reply **`time=… ms`** values in ping output (Linux-style or mock).
- **Recovery** — wall-clock time from **failure injection** to the **first passing validate** (expect **pass**) that runs **after** a successful **reconcile** following that failure.

When **`requirements`** are present, overall **`PASSED`** requires every scenario step to pass **and** every stated threshold to be met. Results are returned under **`requirements`** in the **`POST /scenarios/run`** JSON and printed by the CLI after the numbered steps.

Example:

```yaml
requirements:
  availability:
    min_success_rate: 0.95
  latency:
    max_avg_ms: 100
    max_p95_ms: 250
  recovery:
    max_recovery_seconds: 120
```

The mock provider simulates variable latency and optional packet loss (see **`CLOUDNET_MOCK_PING_BASE_MS`**, **`CLOUDNET_MOCK_PING_JITTER_MS`**, **`CLOUDNET_MOCK_PING_LOSS_RATE`**). Events **`REQUIREMENT_EVALUATED`** and **`REQUIREMENT_FAILED`** are recorded on the topology event timeline when checks run.

---

## Supported topology classes

CloudNet compiles **nodes**, **links** (each link carries a **subnet** CIDR), and optional **firewall_rules** into a deployment-shaped plan. It does **not** claim arbitrary graph support. The supported, partially supported, and unsupported classes are defined in **[docs/topology-support.md](docs/topology-support.md)**.

**Golden examples** (used by regression tests; **no AWS required**):

- **`examples/topologies/valid-two-node.yaml`** — two hosts, one subnet
- **`examples/topologies/valid-three-tier.yaml`** — linear chain, ICMP/TCP rules
- **`examples/topologies/valid-multi-subnet-chain.yaml`** — multi-subnet chain
- **`examples/topologies/valid-firewall-icmp.yaml`** — ICMP firewall rule
- **`examples/topologies/partial-multihomed-warning.yaml`** — multi-homed warning path

Validate a file locally (**compile + quotas + overlap checks**; does not call the cloud):

```bash
./scripts/cloudnet validate-topology examples/topologies/valid-three-tier.yaml
```

**`make topology-test`** runs validator, golden plan, and mock lifecycle coverage for the topology support matrix. **`make ci`** runs those tests alongside the rest of the suite.

---

## Run reliability experiments in CI

GitHub Actions can gate pull requests on the same failure/recovery scenario you run locally—**no AWS credentials**. Workflow **`.github/workflows/cloudnet-scenario.yml`** installs dependencies, starts the API with **`CLOUDNET_PROVIDER=mock`**, waits until **`GET /health`** succeeds, then runs:

```bash
export PATH="$PWD/scripts:$PATH"
cloudnet run examples/backend-failure.yaml
```

The step fails if the CLI exits non-zero (**scenario `FAILED`**, HTTP error, or API never became ready). The main **`CI`** workflow (**`.github/workflows/ci.yml`**) still runs **`make ci`** (lint + unit tests) and a mock control-plane demo; the **CloudNet scenario** workflow focuses on **`examples/backend-failure.yaml`** as an end-to-end reliability check.

Locally, after **`CLOUDNET_PROVIDER=mock make dev`**, run **`make scenario-test`** to execute that scenario against **`CLOUDNET_API_BASE_URL`** (default **`http://127.0.0.1:8010`**). Related targets: **`make ci`** (lint + unit tests), **`make demo-scenario`** (same YAML with a short banner via **`scripts/demo_scenario.sh`**).

---

## Experiment reports

Runs can be persisted with **`topology_id`**, timestamps, total **`duration_ms`**, and per-step **`expected`** / **`actual`** / **`status`**. **`GET /scenarios/{scenario_run_id}/results`** returns the saved report; **`GET /topologies/{id}/events`** lists the event timeline.

---

## Safety and Production Readiness

CloudNet is built for lab-style reliability experiments; these guardrails keep runs predictable and reduce accidental spend or leaked resources.

### Resource quotas

Before a scenario persists topology metadata or reaches deploy, the engine validates the compiled topology against **scenario quotas** (HTTP **400** with a clear message if exceeded):

| Variable | Purpose |
|----------|---------|
| **`CLOUDNET_MAX_HOST_NODES_PER_SCENARIO`** | Upper bound on **`type: host`** nodes |
| **`CLOUDNET_MAX_NETWORKS_PER_SCENARIO`** | Upper bound on compiled network segments (links) |
| **`CLOUDNET_MAX_VPCS_PER_SCENARIO_RUN`** | Same segments cap framed for VPC-style providers |
| **`CLOUDNET_MAX_SCENARIO_DURATION_SECONDS`** | Wall-clock guard during the run (quota steps if exceeded) |
| **`CLOUDNET_MAX_SCENARIO_COST_RISK_UNITS`** | Proxy **hosts + network segments** for blast-radius budgeting |

### Cleanup

- **`scenario.cleanup_on_failure`**: When **true**, CloudNet attempts provider teardown after a **failed deploy step** or after the run finishes with **FAILED** (best-effort; failures are swallowed so the API still returns a report).
- **`cleanup: true`** (top-level scenario payload, same as **`POST /scenarios/run`** body **`cleanup`**): Request teardown **after** the scenario completes (often paired with **`cleanup_on_failure`** for failure paths).
- **CLI**: **`./scripts/cloudnet run … --cleanup`** sends the top-level **`cleanup`** flag.

YAML mirrors the JSON API: optional **`scenario.cleanup_on_failure`** and optional top-level **`cleanup`**.

### Structured logging

The **`cloudnet.scenario`** logger emits **JSON lines** at INFO for step events and run completion. Each line includes **`scenario_run_id`**, **`topology_id`**, **`provider`**, **`action`**, **`status`**, and related fields so log aggregation can correlate runs without scraping unstructured text.

### Config validation

**`GET /config/validate`** returns **`ok`** and a list of checks: provider selected, AWS region and credentials when **`CLOUDNET_PROVIDER=aws`**, **`AWS_MAX_INSTANCES_PER_DEPLOY`** greater than zero, scenario quota env vars positive, and whether mock/non-AWS modes avoid requiring AWS credentials.

### Mock mode and CI

- **Mock provider** (**`CLOUDNET_PROVIDER=mock`**) runs the full scenario pipeline without billable cloud resources.
- **CI** (for example **`.github/workflows/cloudnet-scenario.yml`**) starts the API in mock mode and runs **`cloudnet run`** on tracked YAML — **no AWS credentials** in the workflow.

### AWS cost protections

In addition to scenario quotas, AWS deploy paths honor **`AWS_MAX_INSTANCES_PER_DEPLOY`** and related settings from **`app/core/config.py`**. The cost-risk unit cap ties coarse topology size to configured limits without introducing new AWS services.

---

## Architecture

Under the hood, CloudNet keeps **desired state** (stored topology) separate from **actual state** (provider resource IDs after deploy). Drift compares them; reconcile repairs what the MVP supports.

```text
                    ┌─────────────────────────────────────────┐
                    │           CloudNet control plane          │
                    │  ┌─────────┐  ┌──────────┐  ┌───────────┐ │
  curl / HTTP ─────►│  │Topology │  │ Compiler │  │ Events / │ │
                    │  │ + SQLite│  │ plan/tf  │  │ drift /  │ │
                    │  └────┬────┘  └────┬─────┘  └────┬─────┘ │
                    │       │            │               │       │
                    └───────┼────────────┼───────────────┼───────┘
                            │            │               │
                            ▼            ▼               ▼
                    ┌──────────────┐  ┌─────────────────────────┐
                    │ Deployment   │  │ Provider adapter        │
                    │ resources DB │  │ Mock · AWS · legacy      │
                    └──────────────┘  └───────────┬─────────────┘
                                                  │
                                                  ▼
                                        VPC · subnets · SG · EC2 …
```

---

## Advanced usage

Use these when you need **raw HTTP**, **topology-only workflows**, **scenario file details**, or **implementation reference**—the same endpoints power **`make demo-mock`** and **`cloudnet run`** under the hood.

### Experiment lifecycle

Whether you use **`make demo-mock`** or a **scenario YAML**, the reliability narrative is the same pipeline:

```text
PLAN -> DEPLOY -> VALIDATE(PASS) -> FAILURE -> VALIDATE(FAIL) -> DRIFT -> RECONCILE -> VALIDATE(PASS)
```

- **Plan** — compile intent into a provider-shaped plan without creating cloud resources.
- **Deploy** — create subnets, instances, and rules from that plan.
- **Validate** — check connectivity, for example ICMP along links and rules.
- **Failure** — stop an instance or equivalent to simulate an outage.
- **Validate** — assert expected behavior under failure.
- **Drift** — compare desired topology to actual provider state.
- **Reconcile** — repair supported drift, for example restarting stopped instances.
- **Validate** — confirm recovery.

### Scenario file format (YAML)

Three required top-level keys and one optional block:

| Key | Purpose |
|-----|---------|
| **`scenario`** | **`name:`** label; optional **`cleanup_on_failure: true`** (tear down resources after a failed deploy or failed run — see **Safety and Production Readiness**) |
| **`topology`** | Same as standalone topology YAML (`name`, `nodes`, `links`, `firewall_rules`) |
| **`steps`** | Ordered list; **each item is a single-key mapping** |
| **`requirements`** *(optional)* | **`availability`**, **`latency`**, **`recovery`** thresholds (see **Non-functional validation**) |
| **`cleanup`** *(optional)* | **`cleanup: true`** — after the scenario finishes, tear down deployment resources (same effect as a final **`cleanup`** step). Often used with **`scenario.cleanup_on_failure`**. CLI: **`cloudnet run --cleanup`**. |

**Steps:**

| Key | Example | Notes |
|-----|---------|--------|
| **`deploy`** | `deploy: true` | Same path as **`POST /topologies/{id}/deploy`**. If the scenario has **no** `deploy` step, CloudNet deploys automatically once before other steps. |
| **`validate`** | `validate: all` or `validate: { expect: pass \| fail }` | Connectivity validation |
| **`fail`** | `fail: { node: backend }` | Node-down / stop instance |
| **`drift`** | `drift: { expect: detected \| clean \| none }` | `none` means no drift (alias for **clean**). |
| **`reconcile`** | `reconcile: true` | Same as **`POST /topologies/{id}/reconcile`** |
| **`cleanup`** | `cleanup: true` *(optional)* | Tear down deployment resources (VPC delete on AWS; `delete_resource` per row on mock). Use **last** if included. |

Submit the same payload with **`POST /scenarios/run`** (JSON body, or YAML with **`Content-Type: application/x-yaml`**).

Successful runs return **`scenario`**, **`status`**, **`topology_id`**, **`duration_ms`**, **`steps`**, **`event_timeline_url`**, **`scenario_run_id`**, and optionally **`requirements`**; fetch persisted reports with **`GET /scenarios/{id}/results`** (historical runs may omit **`requirements`** if not stored).

### Raw HTTP lifecycle

1. **Plan** — `GET /topologies/{id}/plan` — compile a provider-shaped plan without creating resources.
2. **Deploy** — `POST /topologies/{id}/deploy` — create provider resources.
3. **Validate** — `POST /topologies/{id}/validate` — ICMP checks on links / rules.
4. **Fail** — `POST /topologies/{id}/failures/node-down` — simulate failure.
5. **Drift** — `GET /topologies/{id}/drift` — desired vs actual.
6. **Reconcile** — `POST /topologies/{id}/reconcile` — repair supported drift.
7. **Validate** — confirm recovery.
8. **Cleanup** — real AWS: `DELETE /provider/networks/{vpc_id}` or `CLOUDNET_DEMO_CLEANUP=true` on demo scripts.

### Demo scripts

| Command | Purpose |
|---------|---------|
| `make demo-mock` | Mock control-plane walkthrough (after `CLOUDNET_PROVIDER=mock make dev`). |
| `make demo-scenario` | Runs `cloudnet run examples/backend-failure.yaml` via the repo script. |
| `make demo-aws-control-plane` | Real AWS resources (costs money); needs `CLOUDNET_PROVIDER=aws`, credentials, `make check-api`. Optional `CLOUDNET_DEMO_CLEANUP=true`. |

### Interactive access on deployed nodes

Once hosts exist and are reachable (AWS via **SSM**), you can inspect access metadata, run commands, and start a tiny HTTP demo workload.

#### Safety and configuration

| Variable | Purpose |
|----------|---------|
| `CLOUDNET_ALLOW_EXEC` | Must be `true` for `POST .../exec` and `POST .../workloads/http-demo`. Default off. |
| `AWS_USE_SSM` | When `true` (default), access summaries include SSM and `ssm_exec`. |

Remote exec uses **AWS Systems Manager** with a **30 second** timeout; destructive patterns are rejected.

#### REST API

| Endpoint | Description |
|----------|-------------|
| `GET /topologies/{id}/access` | IPs, SSM availability, access methods. |
| `POST /topologies/{id}/nodes/{node}/exec` | `{"command": "..."}` |
| `POST /topologies/{id}/workloads/http-demo` | `{"node": "..."}` — background `python3 -m http.server 8080`. |

#### CLI (topology workflows)

The CLI matches YAML files to stored topologies by **`name`** (latest id wins if duplicated).

```bash
pip install -r backend/requirements.txt

./scripts/cloudnet apply examples/three-tier.yaml --deploy

export CLOUDNET_ALLOW_EXEC=true
./scripts/cloudnet access examples/three-tier.yaml
./scripts/cloudnet exec examples/three-tier.yaml frontend "hostname && ip -brief addr"
./scripts/cloudnet workload http-demo examples/three-tier.yaml --node frontend
```

Set `CLOUDNET_API_BASE_URL` if the API is not on `http://127.0.0.1:8010`.

### Topology aggregates (`GET /topologies/{id}/status`)

Aggregate view for dashboards or quick health checks:

```bash
curl http://127.0.0.1:8010/topologies/1/status
```

Example response:

```json
{
  "topology_id": 1,
  "status": "ACTIVE",
  "provider": "aws",
  "resources_summary": {
    "instances": 2,
    "subnets": 2,
    "security_groups": 1
  },
  "last_validation": "PASSED",
  "drift_detected": false
}
```

`last_validation` reflects the latest `VALIDATION` event (`PASSED` / `FAILED`) or `null` if none. If hosts are defined but not yet deployed, drift vs desired state may report drift.

---

## Provider resource identifiers

The database column remains `openstack_id` for backward compatibility. **API responses** list **`provider_resource_id` first**, then the legacy field:

- **`provider_resource_id`** — canonical cloud resource identifier for the active provider (prefer this in new code).
- **`openstack_id`** — same value; retained for older clients only.

---

## Example API response shapes

Representative JSON from the HTTP API (your IDs and counts will differ). Useful when integrating without scenario YAML or debugging step-by-step.

### Plan (`GET /topologies/{id}/plan`)

```json
{
  "topology_id": 7,
  "provider": "mock",
  "plan": {
    "vpc": { "cidr": "10.0.0.0/16" },
    "subnets": [
      { "cidr": "10.130.1.0/24" },
      { "cidr": "10.130.2.0/24" }
    ],
    "instances": [
      { "name": "frontend" },
      { "name": "backend" },
      { "name": "db" }
    ],
    "security_groups": [{ "name": "cloudnet-sg" }],
    "firewall_rules": []
  }
}
```

### Successful validation (`POST /topologies/{id}/validate`)

```json
{
  "topology_id": 7,
  "status": "PASSED",
  "results": [
    { "source": "frontend", "target": "backend", "status": "PASSED" }
  ]
}
```

### Drift (`GET /topologies/{id}/drift`)

Drift item `resource_type` depends on the provider (for example `aws_instance` on AWS, `provider_instance` with the mock provider, `nova_server` on OpenStack).

```json
{
  "topology_id": 7,
  "drift_detected": true,
  "items": [
    {
      "resource_type": "aws_instance",
      "name": "backend",
      "expected": "running",
      "actual": "stopped",
      "severity": "warning"
    }
  ]
}
```

### Reconcile (`POST /topologies/{id}/reconcile`)

```json
{
  "topology_id": 7,
  "status": "RECONCILED",
  "drift": {
    "topology_id": 7,
    "drift_detected": true,
    "items": [
      {
        "resource_type": "aws_instance",
        "name": "backend",
        "expected": "running",
        "actual": "stopped",
        "severity": "warning"
      }
    ]
  },
  "actions": [
    { "node": "backend", "action": "start", "result": "started" },
    { "action": "validate", "result": "PASSED" }
  ]
}
```

### Event timeline (`GET /topologies/{id}/events`)

```json
{
  "topology_id": 7,
  "events": [
    {
      "type": "DEPLOY_COMPLETE",
      "status": "SUCCESS",
      "message": "Deployed 3 instances",
      "metadata": { "instance_count": 3 }
    },
    {
      "type": "VALIDATION",
      "status": "SUCCESS",
      "message": "Topology validation PASSED",
      "metadata": {}
    }
  ]
}
```

**Experiment lifecycle** (same ordering as **Advanced usage: Experiment lifecycle** above); the mock demo prints a compact line such as:

```text
PLAN -> DEPLOY -> VALIDATE(PASS) -> FAILURE -> VALIDATE(FAIL) -> DRIFT -> RECONCILE -> VALIDATE(PASS)
```

---

## Cost safety checklist (AWS)

Use this before enabling real deployments. AWS experiments create real EC2 and networking resources, so keep cleanup and limits enabled while testing.

| Practice | Detail |
|----------|--------|
| Cap instance count | Set `AWS_MAX_INSTANCES_PER_DEPLOY` low (for example `2`) for demos. |
| Small instance type | Default `AWS_DEFAULT_INSTANCE_TYPE=t3.micro`. |
| No NAT Gateway | CloudNet does not create NAT Gateways. |
| No ALB by default | CloudNet does not provision Application Load Balancers. |
| Gate EC2 creation | Instances are refused unless `AWS_ALLOW_CREATE_INSTANCES=true`. |
| Clean up | Prefer `cloudnet run examples/backend-failure.yaml --cleanup`; for manual cleanup use `curl -X DELETE http://127.0.0.1:8010/provider/networks/{vpc_id}` or `CLOUDNET_DEMO_CLEANUP=true make demo-aws-control-plane`. |

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| **Backend not ready** (`curl` fails, CLI connection errors) | Start the API (**`make dev`** or **`uvicorn`** from **`backend/`**); wait for **`curl -fsS http://127.0.0.1:8010/health`**. Point the CLI at the right URL: **`export CLOUDNET_API_BASE_URL=http://127.0.0.1:<port>`** if not using the default port **8010**. |
| **Wrong provider selected** | CI and scenario workflows set **`CLOUDNET_PROVIDER=mock`**. Locally, use **`CLOUDNET_PROVIDER=mock`** for tests without cloud credentials. If **`CLOUDNET_PROVIDER`** is unset, see **Providers** (OpenStack may be selected when **`OPENSTACK_ENABLED=true`**). |
| **Scenario `FAILED` (expected vs actual)** | Re-run with **`cloudnet run <file> --json`** and inspect **`steps`** (`expected`, `actual`, `status`). Optional **`requirements`** blocks add availability/latency/recovery gates—see **Non-functional validation**. Check **`GET /topologies/{id}/events`** for **`SCENARIO_RUN`** and requirement events. |
| **AMI not found** | `AWS_DEFAULT_AMI_ID` exists in `AWS_REGION`; AMIs are regional. |
| **VPC limit exceeded** | Default VPC quota per region; delete unused VPCs or request a limit increase. |
| **`iam:PassRole` denied** | IAM user/role needs permission to pass the instance profile role used for SSM. |
| **SSM `InvalidInstanceId`** | Instance not registered with SSM yet (wait for agent); wrong region/account; or instance lacks `AmazonSSMManagedInstanceCore` and SSM Agent (use Amazon Linux 2023 or equivalent). |
| **Public IP null** | Expected for subnets without auto-assign public IP; validation uses SSM Run Command, not public SSH. |
| **Ping / validation failed** | Security group / ICMP rules; stopped instance; SSM connectivity; check `GET .../drift` and failure events. |

---

## Providers

Select infrastructure with `CLOUDNET_PROVIDER`:

### Primary providers

| Value | Notes |
|-------|--------|
| `mock` | Full control-plane path without cloud calls; recommended for local demos and CI. |
| `aws` | Real VPC, subnets, security groups, and EC2 when credentials and cost controls are configured. |

### Experimental/legacy providers

| Value | Notes |
|-------|--------|
| `openstack` | Legacy Nova/Neutron-oriented path retained for compatibility. |
| `proxmox` | Experimental health/list support; VM creation is not implemented yet. |

If `CLOUDNET_PROVIDER` is unset, CloudNet defaults to OpenStack when `OPENSTACK_ENABLED=true`; otherwise **mock**.

Copy `.env.example` to `.env` for local overrides. The example file defaults to **`OPENSTACK_ENABLED=false`** so a fresh copy keeps the **mock** provider unless you opt into OpenStack or set `CLOUDNET_PROVIDER` explicitly.

---

## Run locally (quick reference)

For **`CLOUDNET_PROVIDER=mock make dev`** followed by **`make demo-mock`**, see **Quick Start** above. The commands below are for developers running the API, docs, and tests.

Install dependencies:

```bash
make install
```

Mock backend:

```bash
CLOUDNET_PROVIDER=mock make dev
```

Health:

```bash
curl http://127.0.0.1:8010/health
```

Interactive API docs:

```text
http://127.0.0.1:8010/docs
```

Run tests:

```bash
make test
```

Lint plus tests (same as CI unit/lint stage):

```bash
make ci
```

CI runs `make ci`, starts the API with `CLOUDNET_PROVIDER=mock`, waits for `/health`, then runs `make demo-mock`.

---

## AWS setup (summary)

Create or choose an IAM principal with EC2 and related permissions. Example `.env` entries:

```bash
CLOUDNET_PROVIDER=aws
AWS_REGION=us-west-2
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
AWS_DEFAULT_AMI_ID=ami-0123456789abcdef0
AWS_DEFAULT_INSTANCE_TYPE=t3.micro
AWS_INSTANCE_PROFILE_NAME=your-ssm-instance-profile
AWS_KEY_NAME=your-ec2-keypair
AWS_ALLOW_CREATE_INSTANCES=false
AWS_MAX_INSTANCES_PER_DEPLOY=3
AWS_SSH_ALLOWED_CIDR=203.0.113.10/32
```

Connectivity validation uses **SSM Run Command**, not public SSH. Instances need an IAM instance profile with **AmazonSSMManagedInstanceCore** and an AMI with **SSM Agent** (for example Amazon Linux 2023).

```bash
curl http://localhost:8010/provider/health
```

---

## Experimental/legacy providers

Copy `.env.example` to `.env` for OpenStack credentials. Set `OPENSTACK_ENABLED=true` when you want connection sanity checks.

Proxmox variables (`PROXMOX_HOST`, `PROXMOX_USER`, …) are documented in `.env.example`; initial support focuses on health and listing.

---

## Control plane API overview

| Step | Endpoint |
|------|----------|
| Scenario run | `POST /scenarios/run` |
| Scenario report | `GET /scenarios/{scenario_run_id}/results` |
| Plan | `GET /topologies/{id}/plan` |
| Deploy | `POST /topologies/{id}/deploy` |
| Validate | `POST /topologies/{id}/validate` |
| Ping test | `POST /topologies/{id}/tests/ping` |
| List connectivity tests | `GET /topologies/{id}/tests` |
| Node failure | `POST /topologies/{id}/failures/node-down` |
| Recover node | `POST /topologies/{id}/recover/node` |
| Drift | `GET /topologies/{id}/drift` |
| Reconcile | `POST /topologies/{id}/reconcile` |
| Status | `GET /topologies/{id}/status` |
| Access | `GET /topologies/{id}/access` |
| Exec | `POST /topologies/{id}/nodes/{node}/exec` |
| HTTP demo workload | `POST /topologies/{id}/workloads/http-demo` |
| Resources | `GET /topologies/{id}/resources` |
| Events | `GET /topologies/{id}/events` |
| Failure history | `GET /topologies/{id}/failures` |
| Terraform JSON | `GET /topologies/{id}/terraform` |
| Terraform zip | `GET /topologies/{id}/terraform.zip` |
| Provider health | `GET /provider/health` |
| Provider networks | `GET /provider/networks` |
| Create VPC (AWS) | `POST /provider/networks` |
| Delete VPC (AWS) | `DELETE /provider/networks/{vpc_id}` |

---

## Terraform export

Export compiled Terraform as JSON (no credentials required to generate files):

```bash
curl http://127.0.0.1:8010/topologies/{topology_id}/terraform
```

Zip download:

```bash
curl -o cloudnet-terraform.zip \
  http://127.0.0.1:8010/topologies/{topology_id}/terraform.zip
```

---

## Firewall rules in topology

Topologies may include `firewall_rules` (for example ICMP between nodes). These compile to security group rules on the shared CloudNet security group for AWS deployments.

---

## Compile-only example

`POST /compile` validates and compiles a topology JSON payload without persisting it:

```bash
curl -X POST http://127.0.0.1:8010/compile \
  -H "Content-Type: application/json" \
  -d '{
    "name": "simple-two-node-lab",
    "nodes": [
      {"name": "client-a", "type": "host"},
      {"name": "client-b", "type": "host"}
    ],
    "links": [
      {"from": "client-a", "to": "client-b", "subnet": "10.10.1.0/24"}
    ]
  }'
```

---

## Project layout

```text
backend/app/     FastAPI app, routes, providers, services
scripts/         Demos, run helpers
tests/           Pytest suite
```

---

## Ports and utilities

If port `8010` is busy:

```bash
make free-port
# or
make run-port PORT=8020
```

Failure-recovery script (legacy provider resource naming):

```bash
make check-api
make demo-failure-recovery
```
