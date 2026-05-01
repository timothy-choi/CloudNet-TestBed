# CloudNet Testbed

CloudNet TestBed is a small **control plane** for describing network lab topologies, compiling deployment plans, provisioning provider resources (primarily **AWS**), running connectivity validation (ICMP via provider APIs), detecting **drift**, **reconciling** simple failures (for example stopped EC2 instances), and recording an **event timeline**. A **mock** provider exercises the same API flows without cloud credentials or billable resources—ideal for CI and local demos.

### Test your system reliability on real cloud infrastructure

CloudNet lets you define **failure scenarios** in YAML (desired topology plus ordered steps such as validate all links, stop a node, expect validation to fail, reconcile, expect validation to pass again). The API runs those steps sequentially using the same deploy, validate, failure injection, and reconcile paths as the interactive endpoints—no duplicate provisioning logic. Submit the scenario with **`POST /scenarios/run`**, or run **`./scripts/cloudnet run examples/backend-failure-scenario.yaml`** against a live API. With the mock provider and dev server running, try **`make demo-scenario`**.

**Languages:** Python (FastAPI, topology compiler, providers), Bash (demos and smoke scripts). A Go-based test runner is reserved for a later milestone.

---

## Architecture

CloudNet keeps **desired state** (stored topology: nodes, links, firewall rules) separate from **actual state** (provider resource IDs persisted after deploy). Drift compares them; reconcile repairs what the MVP supports.

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

## Lifecycle (demo story)

This is the narrative the mock AWS demo (`make demo-mock`) and real AWS demo (`make demo-aws-control-plane`) follow:

1. **Plan** — `GET /topologies/{id}/plan` compiles the stored topology into a provider-shaped plan (subnets, instances, rules) without creating infrastructure.
2. **Deploy** — `POST /topologies/{id}/deploy` creates provider resources and records them in the database.
3. **Validate** — `POST /topologies/{id}/validate` runs ICMP checks along links / firewall rules and records results.
4. **Fail** — `POST /topologies/{id}/failures/node-down` stops an instance (or equivalent) to simulate failure.
5. **Drift** — `GET /topologies/{id}/drift` compares desired topology to actual provider state (missing subnets, stopped instances, etc.).
6. **Reconcile** — `POST /topologies/{id}/reconcile` runs drift detection, then repairs supported drift (for example starting stopped instances).
7. **Validate** — Run validation again to confirm recovery.
8. **Cleanup** — Terminate instances and delete the demo VPC when using real AWS (`DELETE /provider/networks/{vpc_id}` or `CLOUDNET_DEMO_CLEANUP=true` for the AWS demo script).

---

## Demo commands

Run the **mock** control-plane loop (safe, no AWS credentials):

```bash
CLOUDNET_PROVIDER=mock make dev
# another terminal:
make demo-mock
```

Run an **end-to-end reliability scenario** (creates topology, deploys, runs validate / fail / reconcile steps from `examples/backend-failure-scenario.yaml`):

```bash
CLOUDNET_PROVIDER=mock make dev
# another terminal:
make demo-scenario
```

Run the **AWS** control-plane demo (creates real VPC/EC2 resources; costs money). The API process **must** use `CLOUDNET_PROVIDER=aws` with valid AWS credentials and instance creation allowed for the demo topology size:

```bash
CLOUDNET_PROVIDER=aws AWS_ALLOW_CREATE_INSTANCES=true make dev
# another terminal (same provider/credentials implied by your .env):
make check-api
make demo-aws-control-plane
```

`make check-api` calls `GET /provider/health` and requires **`connected: true`**. For AWS that means credentials and network reachability to EC2; for mock it always succeeds.

Optional: destroy the demo VPC at the end of the AWS script:

```bash
CLOUDNET_DEMO_CLEANUP=true make demo-aws-control-plane
```

---

## Using a deployed topology

CloudNet is not only about provisioning—it is an **interactive testbed**. Once hosts are deployed and reachable (AWS via **SSM**), you can inspect access metadata, run shell commands on nodes, and start a minimal HTTP demo workload.

### Safety and configuration

| Variable | Purpose |
|----------|---------|
| `CLOUDNET_ALLOW_EXEC` | Must be `true` to allow `POST .../exec` and `POST .../workloads/http-demo`. Default is off. |
| `AWS_USE_SSM` | When `true` (default), AWS access summaries report SSM availability and include `ssm_exec` in `access_methods`. |

Remote exec uses **AWS Systems Manager** (`AWS-RunShellScript`) with a **30 second** command timeout. Obvious destructive patterns are rejected (for example `rm -rf /`, `shutdown`, `reboot`, `mkfs`, fork bombs).

### REST API

| Endpoint | Description |
|----------|-------------|
| `GET /topologies/{id}/access` | Instance IDs, private/public IPs, SSM availability, suggested access methods. |
| `POST /topologies/{id}/nodes/{node}/exec` | Body: `{"command": "..."}` — run a shell command on the node’s instance. |
| `POST /topologies/{id}/workloads/http-demo` | Body: `{"node": "..."}` — start `python3 -m http.server 8080` in the background via SSM. |

### CLI (`scripts/cloudnet`)

Requires PyYAML (included in `backend/requirements.txt`). The CLI resolves the topology by matching the **`name`** field in your YAML against stored topologies (latest id wins if duplicates exist).

```bash
pip install -r backend/requirements.txt

# Create stored topology and deploy
./scripts/cloudnet apply examples/three-tier.yaml --deploy

# Allow exec on the API process
export CLOUDNET_ALLOW_EXEC=true

./scripts/cloudnet access examples/three-tier.yaml
./scripts/cloudnet exec examples/three-tier.yaml frontend "hostname && ip -brief addr"
./scripts/cloudnet workload http-demo examples/three-tier.yaml --node frontend
```

Set `CLOUDNET_API_BASE_URL` if the API is not on `http://127.0.0.1:8010`.

---

## Topology status

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

## Example output snippets

These are representative shapes from the API and demos (your IDs and counts will differ).

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

The mock demo script prints a compact timeline such as:

```text
Timeline: PLAN -> DEPLOY -> VALIDATE(PASS) -> FAILURE -> VALIDATE(FAIL) -> DRIFT -> RECONCILE -> VALIDATE(PASS)
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
