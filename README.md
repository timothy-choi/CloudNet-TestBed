# CloudNet-TestBed

CloudNet Testbed is an OpenStack-backed network testing platform that lets users define virtual network topologies, deploy real application nodes into those topologies, run connectivity tests, and inject failures to observe how network behavior affects applications.

## Core workflow

Define topology → Compile deployment plan → Provision OpenStack resources → Run connectivity tests → Inject failures → Observe results

## MVP

The first MVP supports:

- YAML topology definitions
- topology-to-deployment-plan compiler
- FastAPI control plane
- OpenStack Nova/Neutron provisioning
- Go-based connectivity test runner
- Bash demo and setup scripts

## Languages

- Python: FastAPI control plane, topology compiler, OpenStack orchestration
- Go: network test runner
- Bash: setup, smoke tests, demo automation