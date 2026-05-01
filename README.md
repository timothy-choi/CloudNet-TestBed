# CloudNet — Reliability Testing Platform for Cloud Topologies

CloudNet runs **failure scenarios** on cloud infrastructure and **verifies recovery**: deploy, validate connectivity, inject faults, detect drift, reconcile, and emit a clear pass/fail report.

**First command** (with the API running — see Quick Start):

```bash
./scripts/cloudnet run examples/backend-failure.yaml
```

Use **`./scripts/cloudnet`** from the project root, or add `scripts/` to your `PATH` and run **`cloudnet`**. Exit code **0** = scenario **PASSED**, **1** = **FAILED** or HTTP error. Use **`cloudnet run --json`** for the raw API response.

**Languages:** Python (FastAPI, topology compiler, providers), Bash (demos).

---

## Why use CloudNet?

- **Test failure recovery** without manually wiring VPCs, subnets, security groups, and instances for every experiment.
- **Validate cloud network connectivity** from declarative topology definitions (nodes, links, firewall rules).
- **Reproduce reliability experiments locally** with mock mode—no cloud credentials or billable resources.
- **Run the same experiment** against real AWS infrastructure when you are ready, using the same scenario engine.

---

## Quick Start

**1. Install and start the API** with the **mock** provider (no AWS credentials):

```bash
pip install -r backend/requirements.txt
CLOUDNET_PROVIDER=mock make dev
```

**2. Run a scenario** in another terminal:

```bash
./scripts/cloudnet run examples/backend-failure.yaml
```

For a shorter example (deploy + validate only), use **`examples/simple-connectivity.yaml`**.

**Optional:** **`make demo-scenario`** runs the backend-failure YAML with a short banner. **`CLOUDNET_PROVIDER=mock make demo-mock`** is an interactive walkthrough of the same reliability narrative.

That narrative is:

```text
PLAN → DEPLOY → VALIDATE(PASS) → FAILURE → VALIDATE(FAIL) → DRIFT → RECONCILE → VALIDATE(PASS)
```

---

## What users do with it

- **Define a topology** — nodes, links, and optional firewall rules; optionally describe **scenario** steps (deploy, validate, fail a node, drift checks, reconcile, cleanup).
- **Run an experiment** — **`POST /scenarios/run`** or **`./scripts/cloudnet run`** while the API is up.
- **Review pass/fail reports** — per-step expectations, outcomes, and durations.
- **Inspect drift and events** — topology timeline at **`GET /topologies/{id}/events`**; drift via API or scenario steps.
- **Use AWS mode** for real VPCs and instances when you want production-like infrastructure under test.

---

## What CloudNet is not

- **Not a Terraform replacement** — it can compile plans and export Terraform for inspection, but it is not a general-purpose IaC workflow engine.
- **Not an AWS console wrapper** — it exposes a focused API and CLI for lab-style networks and experiments.
- **Not a production orchestrator** — it does not replace Kubernetes, CI platforms, or fleet managers for running services at scale.
- **It is a reliability testbed** — a control plane for defining intent, provisioning lab infrastructure, injecting faults, observing drift, reconciling where supported, and recording outcomes.

---

## Experiment lifecycle

Whether you use **`make demo-mock`** or a **scenario YAML**, the narrative is the same pipeline:

```text
PLAN → DEPLOY → VALIDATE(PASS) → FAILURE → VALIDATE(FAIL) → DRIFT → RECONCILE → VALIDATE(PASS)
```

- **Plan** — compile intent into a provider-shaped plan (no cloud resources yet).
- **Deploy** — create subnets, instances, and rules from that plan.
- **Validate** — check connectivity (for example ICMP along links / rules).
- **Failure** — stop an instance or equivalent to simulate outage.
- **Validate** — assert behavior under failure (for example connectivity fails where expected).
- **Drift** — compare desired topology to actual provider state.
- **Reconcile** — repair supported drift (for example start stopped instances).
- **Validate** — confirm recovery.

The mock demo prints a compact timeline; scenario runs persist structured step results and emit **`SCENARIO_RUN`** (and related) events on the topology timeline.

---

## Experiment reports

Runs can be persisted with **`topology_id`**, timestamps, total **`duration_ms`**, and per-step **`expected`** / **`actual`** / **`status`**. **`GET /scenarios/{scenario_run_id}/results`** returns the saved report; **`GET /topologies/{id}/events`** lists the event timeline.

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
                    │ resources DB │  │ AWS · Mock · OpenStack… │
                    └──────────────┘  └───────────┬─────────────┘
                                                  │
                                                  ▼
                                        VPC · subnets · SG · EC2 …
```

---

## Advanced usage

Use these when you need **raw HTTP**, **topology-only workflows**, **scenario file details**, or **implementation reference**—the same endpoints power **`cloudnet run`** under the hood.

### Scenario file format (YAML)

Three top-level keys:

| Key | Purpose |
|-----|---------|
| **`scenario`** | `name:` label for the experiment |
| **`topology`** | Same as standalone topology YAML (`name`, `nodes`, `links`, `firewall_rules`) |
| **`steps`** | Ordered list; **each item is a single-key mapping** |

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

Successful runs return **`scenario`**, **`status`**, **`topology_id`**, **`duration_ms`**, **`steps`**, **`event_timeline_url`**, and **`scenario_run_id`**; fetch persisted reports with **`GET /scenarios/{id}/results`**.

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
| `make demo-scenario` | Runs `./scripts/cloudnet run examples/backend-failure.yaml`. |
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

**Experiment lifecycle** (same ordering as **Quick Start: Run an experiment** above); the mock demo prints a compact line such as:

```text
PLAN → DEPLOY → VALIDATE(PASS) → FAILURE → VALIDATE(FAIL) → DRIFT → RECONCILE → VALIDATE(PASS)
```

---

## Cost safety checklist (AWS)

Use this before enabling real deployments:

| Practice | Detail |
|----------|--------|
| Cap instance count | Set `AWS_MAX_INSTANCES_PER_DEPLOY` low (for example `2`) for demos. |
| Small instance type | Default `AWS_DEFAULT_INSTANCE_TYPE=t3.micro`. |
| No NAT Gateway | CloudNet does not create NAT Gateways. |
| No ALB by default | CloudNet does not provision Application Load Balancers. |
| Gate EC2 creation | Instances are refused unless `AWS_ALLOW_CREATE_INSTANCES=true`. |
| Clean up | After demos: `curl -X DELETE http://127.0.0.1:8010/provider/networks/{vpc_id}` or use `CLOUDNET_DEMO_CLEANUP=true` with `make demo-aws-control-plane`. |

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| **AMI not found** | `AWS_DEFAULT_AMI_ID` exists in `AWS_REGION`; AMIs are regional. |
| **VPC limit exceeded** | Default VPC quota per region; delete unused VPCs or request a limit increase. |
| **`iam:PassRole` denied** | IAM user/role needs permission to pass the instance profile role used for SSM. |
| **SSM `InvalidInstanceId`** | Instance not registered with SSM yet (wait for agent); wrong region/account; or instance lacks `AmazonSSMManagedInstanceCore` and SSM Agent (use Amazon Linux 2023 or equivalent). |
| **Public IP null** | Expected for subnets without auto-assign public IP; validation uses SSM Run Command, not public SSH. |
| **Ping / validation failed** | Security group / ICMP rules; stopped instance; SSM connectivity; check `GET .../drift` and failure events. |

---

## Providers

Select infrastructure with `CLOUDNET_PROVIDER`:

| Value | Notes |
|-------|--------|
| `mock` | Full control-plane path without cloud calls; used in CI. |
| `aws` | Real VPC, subnets, security groups, EC2 (when allowed). |
| `openstack` | Nova/Neutron-oriented naming in API responses. |
| `proxmox` | Health/list oriented; VM creation not implemented yet. |

If `CLOUDNET_PROVIDER` is unset, CloudNet defaults to OpenStack when `OPENSTACK_ENABLED=true`; otherwise **mock**.

Copy `.env.example` to `.env` for local overrides. The example file defaults to **`OPENSTACK_ENABLED=false`** so a fresh copy keeps the **mock** provider unless you opt into OpenStack or set `CLOUDNET_PROVIDER` explicitly.

---

## Run locally (quick reference)

For **`CLOUDNET_PROVIDER=mock make demo-mock`** and **`./scripts/cloudnet run examples/backend-failure.yaml`**, see **Quick Start: Run an experiment** above. The commands below are for developers running the API, docs, and tests.

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
AWS_KEY_NAME=your-ec2-keypair
AWS_ALLOW_CREATE_INSTANCES=false
AWS_MAX_INSTANCES_PER_DEPLOY=2
AWS_SSH_ALLOWED_CIDR=203.0.113.10/32
```

Connectivity validation uses **SSM Run Command**, not public SSH. Instances need an IAM instance profile with **AmazonSSMManagedInstanceCore** and an AMI with **SSM Agent** (for example Amazon Linux 2023).

```bash
curl http://localhost:8010/provider/health
```

---

## OpenStack & Proxmox

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

Failure-recovery script (OpenStack-oriented naming):

```bash
make check-api
make demo-failure-recovery
```
