# CloudNet Testbed

CloudNet Testbed is an OpenStack-backed network testing platform for building virtual network labs, deploying real application nodes into them, running connectivity tests, and injecting failures to observe how network behavior affects applications.

The project is intentionally small at this stage. The first milestone is a topology compiler that turns a simple topology definition into a deployment plan. OpenStack provisioning, failure injection, and the Go test runner come later.

## Core Workflow

Define topology -> Compile deployment plan -> Provision OpenStack resources -> Run tests -> Inject failures -> Observe results

## Language Split

- Python: FastAPI control plane, topology compiler, OpenStack orchestration
- Go: network test runner
- Bash: setup, demo, and smoke scripts

## MVP Scope

The MVP is focused on a thin but useful path through the system:

- Define network topologies in YAML or JSON.
- Compile topologies into deployment plans.
- Expose the compiler through a small FastAPI control plane.
- Persist topology definitions in SQLite while the API contract settles.
- Add OpenStack Nova/Neutron orchestration after the compiler contract is stable.
- Add a Go-based network test runner after basic provisioning exists.
- Add Bash scripts for setup, demos, and smoke tests as workflows settle.

No UI or OpenStack provisioning is included yet.

## First Milestone

Compile a simple topology into a deployment plan.

Input topology:

```yaml
name: simple-two-node-lab
nodes:
  - name: client-a
    type: host
  - name: client-b
    type: host
links:
  - from: client-a
    to: client-b
    subnet: 10.10.1.0/24
```

Compiled deployment plan:

```json
{
  "topology_name": "simple-two-node-lab",
  "networks": [
    {
      "name": "simple-two-node-lab-net-1",
      "subnet": "10.10.1.0/24",
      "attached_nodes": ["client-a", "client-b"]
    }
  ],
  "servers": [
    {
      "name": "client-a",
      "type": "host"
    },
    {
      "name": "client-b",
      "type": "host"
    }
  ]
}
```

## Project Structure

```text
backend/
  app/
    main.py
    topology_compiler.py
    schemas.py
cli/
examples/
tests/
```

## Setup

```bash
make install
```

## Providers

CloudNet selects infrastructure with `CLOUDNET_PROVIDER`.

- OpenStack: `CLOUDNET_PROVIDER=openstack`
- Proxmox: `CLOUDNET_PROVIDER=proxmox`
- AWS: `CLOUDNET_PROVIDER=aws`
- Mock: `CLOUDNET_PROVIDER=mock`

If `CLOUDNET_PROVIDER` is unset, CloudNet defaults to OpenStack when
`OPENSTACK_ENABLED=true`; otherwise it uses the mock provider.

## OpenStack Setup

Copy `.env.example` to `.env` and fill in your OpenStack credentials.

```bash
cp .env.example .env
```

Set `OPENSTACK_ENABLED=true` when you want the API to sanity-check an OpenStack connection. Existing OpenStack deploy behavior is still available through the OpenStack provider.

## Proxmox Setup

Install Proxmox VE and make sure the backend can reach its API on port `8006`.
For local development, you can use `root@pam`; for shared environments, create a
dedicated API user with the minimum permissions needed to inspect nodes, VMs, and
network configuration.

Set these values in `.env`:

```bash
CLOUDNET_PROVIDER=proxmox
PROXMOX_HOST=192.168.1.50
PROXMOX_PORT=8006
PROXMOX_USER=root@pam
PROXMOX_PASSWORD=your-password
PROXMOX_VERIFY_SSL=false
PROXMOX_NODE=pve
```

Then start the backend and check the provider:

```bash
curl http://localhost:8010/provider/health
```

Initial Proxmox support is health and list operations only. VM creation is not
implemented yet.

## AWS Setup

AWS is the practical real-infrastructure provider for the MVP. Start with
health and list operations before provisioning resources. The current AWS
provider can create and delete tagged VPC/subnet/EC2 resources, but it does not
create NAT Gateways yet.

Costs can start as soon as AWS resources are created outside CloudNet. Use a
low-cost region and instance type, keep experiments small, and clean up
resources when you are done.

Create or choose an IAM user with EC2 read permissions and choose an AMI ID in
your region. Set these values in `.env`:

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

Then start the backend and check the provider:

```bash
curl http://localhost:8010/provider/health
```

### AWS Safety

CloudNet defaults to `t3.micro` and refuses to create EC2 instances unless
`AWS_ALLOW_CREATE_INSTANCES=true` is set. Keep
`AWS_MAX_INSTANCES_PER_DEPLOY=2` for demos until you intentionally raise it.

Set `AWS_SSH_ALLOWED_CIDR` to your public IP CIDR if you need SSH access. If it
is unset, CloudNet does not add a public SSH ingress rule. CloudNet does not
create NAT Gateways, load balancers, or Elastic IPs.

AWS connectivity validation uses Systems Manager Run Command instead of public
SSH. Instances need an IAM role with `AmazonSSMManagedInstanceCore`, and the AMI
must have SSM Agent installed, such as Amazon Linux 2023.

## Control Plane Behavior

CloudNet now separates desired state from actual state. The desired state is the
stored topology: hosts, links, and subnets. The actual state is the provider
resources recorded in the database, such as AWS VPCs, subnets, security groups,
and EC2 instances.

Use `GET /topologies/{topology_id}/plan` to compile a topology into a deployment
plan without calling AWS or creating resources.

Use `POST /topologies/{topology_id}/reconcile` to compare stored AWS instance
resources with AWS. Reconcile repairs stopped EC2 instances by starting them,
waits until repaired instances are running, then runs the existing validation
flow. It does not create new resources or recreate deleted instances in this
MVP.

Example control-plane loop:

```text
deploy -> PASS
stop node -> FAIL
reconcile -> PASS
```

## Firewall Policy as Topology

Topologies can include `firewall_rules` to describe allowed traffic between
logical nodes. CloudNet compiles those rules into AWS Security Group ingress
rules on the shared `cloudnet-sg` used by CloudNet instances.

For the MVP, `icmp` rules enable ping validation and `tcp` rules may include a
`port`. If no firewall rules are provided, CloudNet preserves the existing
default behavior and allows ICMP between CloudNet instances.

CloudNet only deletes AWS VPCs tagged as CloudNet-managed and refuses to delete
default VPCs. Cleanup is required after demos to terminate CloudNet instances
and remove the VPC/subnet pair. Always confirm the VPC ID before cleanup:

```bash
curl -X DELETE http://localhost:8010/provider/networks/{vpc_id}
```

## Demo: Plan -> Deploy -> Validate -> Fail -> Reconcile

This AWS demo shows CloudNet acting as a control plane: the topology is desired
state, AWS is actual state, and reconcile repairs drift.

EC2 can cost money. Keep `AWS_MAX_INSTANCES_PER_DEPLOY` low, run the demo in a
low-cost region, and clean up the VPC when you are done.

Start the API with AWS configured:

```bash
make dev
make check-api
```

Run the demo:

```bash
make demo-aws-control-plane
```

Or run the script directly against a non-default API URL:

```bash
CLOUDNET_API_BASE_URL=http://127.0.0.1:8010 scripts/demo_aws_control_plane.sh
```

The demo creates a three-node topology:

```text
frontend -> backend -> db
```

It creates two subnets, two ICMP firewall rules, deploys AWS resources, validates
connectivity, stops the `backend` node, validates failure, reconciles, and
validates recovery.

Expected output snippets:

```text
==> Planning topology without deploying
...
==> Deploying topology
"status": "ACTIVE"
...
==> Validating baseline connectivity (expected PASSED)
"status": "PASSED"
...
==> Validating after failure (expected FAILED)
"status": "FAILED"
...
==> Reconciling desired state to actual state
"status": "RECONCILED"
...
==> Validating after reconcile (expected PASSED)
"status": "PASSED"
```

To have the script clean up the AWS VPC at the end:

```bash
CLOUDNET_DEMO_CLEANUP=true make demo-aws-control-plane
```

Manual cleanup uses the VPC ID printed by the demo:

```bash
curl -X DELETE http://127.0.0.1:8010/provider/networks/{vpc_id}
```

## Local Development

Install backend dependencies:

```bash
make install
```

Run the backend on port 8010:

```bash
make run
```

Stop any local `uvicorn app.main:app` process for this project:

```bash
make stop
```

Restart the backend cleanly:

```bash
make dev
```

Run tests:

```bash
make test
```

## Failure Recovery Demo

With OpenStack credentials configured and the backend running, this demo exercises the full control-plane loop:

Deploy -> Validate PASSED -> Inject failure -> Validate FAILED -> Recover -> Validate PASSED

```bash
make dev
scripts/check_api.sh
scripts/demo_failure_recovery.sh
```

You can also run the script through Make:

```bash
make check-api
make demo-failure-recovery
```

If your API is not on the default port, set `CLOUDNET_API_BASE_URL`:

```bash
CLOUDNET_API_BASE_URL=http://127.0.0.1:8020 scripts/demo_failure_recovery.sh
```

If port 8010 is busy, free it:

```bash
make free-port
```

Or run the backend on another port:

```bash
make run-port PORT=8020
```

## Run The API

```bash
make run
```

Health check:

```bash
curl http://127.0.0.1:8010/health
```

API docs:

```text
http://127.0.0.1:8010/docs
```

Compile a topology:

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

Store a topology:

```bash
curl -X POST http://127.0.0.1:8010/topologies \
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

List stored topologies:

```bash
curl http://127.0.0.1:8010/topologies
```

## Run Tests

```bash
make test
```
