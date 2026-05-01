# Topology Support Matrix

CloudNet does not support arbitrary topology graphs. The current compiler and mock/provider lifecycle support a deliberately small set of topology classes, with warnings for one partial case and hard failures for unsupported graph shapes.

## Supported

| Class | Contract | Example |
|-------|----------|---------|
| Single link | Two `host` nodes joined by one link/subnet. | `examples/topologies/valid-two-node.yaml` |
| Chain topology | A linear `frontend -> backend -> db` path. Middle hosts may appear in more than one link and emit the multi-homed warning described below. | `examples/topologies/valid-three-tier.yaml` |
| Multi-subnet chain | A longer linear chain where each link creates a distinct subnet. | `examples/topologies/valid-multi-subnet-chain.yaml` |
| Firewall rules with ICMP/TCP | `firewall_rules` support `protocol: icmp` and `protocol: tcp`; TCP rules may include `port`. | `examples/topologies/valid-firewall-icmp.yaml` |
| Scenario lifecycle | Supported shapes are covered by mock scenario runs that create topology, plan, deploy, validate, inject `node_down` where applicable, detect drift, reconcile, and validate again. | `tests/test_topology_supported_scenario.py` |

## Partially Supported

| Class | Current behavior | Example |
|-------|------------------|---------|
| Multi-homed node | A `host` that appears in multiple links is accepted, but deploy attaches that instance to the first subnet only and emits a warning. CloudNet does not create multiple ENIs for it. | `examples/topologies/partial-multihomed-warning.yaml` |

## Unsupported

| Class | Behavior |
|-------|----------|
| Arbitrary mesh requiring multiple ENIs | Rejected when a node requires more than the documented partial two-link multi-home case; CloudNet does not model multiple network interfaces per host. |
| Cycles/rings | Rejected by the compiler as unsupported topology classes. |
| Cross-region topology | Not modeled in the topology schema. A deployment uses the configured provider region/session. |
| Overlapping CIDRs | Rejected by the compiler. |
| Unsupported node types unless explicitly enabled | Rejected unless the type is present in `SUPPORTED_NODE_TYPES` and provider code implements it. |
| NAT Gateway/ALB by default | Not part of the default topology language and rejected as unsupported node types. |

## Test Contract

`make topology-test` runs validator, golden plan, and mock lifecycle coverage for the support matrix. The golden plan tests assert VPC count, subnet count, instance count, firewall rule count, and warnings for every accepted example.
