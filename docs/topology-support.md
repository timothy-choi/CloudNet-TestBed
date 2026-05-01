# Topology support matrix

CloudNet compiles declarative **nodes**, **links** (each link implies a subnet segment), and **firewall_rules** into a provider plan. This document states what the **compiler and deploy path** support today and what is explicitly out of scope.

## Supported

| Shape | Notes |
|-------|--------|
| **Two hosts, one subnet** | Single link between two `host` nodes with one IPv4 or IPv6 CIDR. |
| **Three-tier chain** | e.g. `frontend → backend → db` as consecutive links; distinct subnets per hop. The **middle** host sits on **two** links (multi-homed) — CloudNet **warns** and attaches that instance to the **first** subnet only at deploy. |
| **Multi-subnet topology** | Multiple links ⇒ multiple subnets in one logical VPC (AWS) or equivalent networks (mock/OpenStack). |
| **Firewall rules with ICMP** | Rules with `protocol: icmp` between nodes (and TCP where implemented). |
| **Node-down failure** | Scenario **`fail`** step targets a **`host`** node that exists in the topology. |
| **Drift + reconcile** | **`drift`** / **`reconcile`** scenario steps against mock or provider-backed state. |

The golden tests under **`examples/topologies/`** lock in compile-time counts (VPC = 1 lab VPC, subnet = link count, instances = `host` count, firewall rules = rule count) and multi-home **warnings**.

## Partially supported

| Topic | Behavior |
|-------|----------|
| **Multi-homed nodes** | A **host** appearing on **more than one link** is warned; deploy attaches the instance to the **first** subnet only (`multi_homed_warnings` in **`deployment_service`**). |
| **`load_balancer` (or similar) node types** | Not a supported **`type`** unless implemented under **`SUPPORTED_NODE_TYPES`** in **`topology_compiler.py`**. |
| **Custom Terraform import/deploy** | Export exists for inspection; treating Terraform as the source of truth for deploy is **future** work. |

## Unsupported (by design / MVP)

| Topic | Reason |
|-------|--------|
| **Arbitrary mesh with multiple ENIs per host** | Single attachment per host instance in the current AWS adapter; multi-home is warn-only. |
| **NAT Gateway** | Not modeled in the topology schema; no dedicated resource type in the MVP compiler. |
| **Application Load Balancer by default** | No ELB/ALB abstraction in the topology language unless added explicitly later. |
| **Cross-region topologies** | Single region per provider session / deployment. |

## Validation tooling

- **`cloudnet validate-topology <file.yaml>`** — compile + quota + overlap checks; **no** deploy, **no** AWS credentials required.
- **`GET /topologies/{id}/plan`** — same compile model after a topology is stored (requires API).

See README **Supported Topologies** for links to examples and CI behavior.
