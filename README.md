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

## OpenStack Setup

Copy `.env.example` to `.env` and fill in your OpenStack credentials.

```bash
cp .env.example .env
```

Set `OPENSTACK_ENABLED=true` when you want the API to sanity-check an OpenStack connection. The current OpenStack support only checks connectivity and lists existing images, flavors, and networks. It does not provision VMs or create networks yet.

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
