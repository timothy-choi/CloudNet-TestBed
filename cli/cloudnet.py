#!/usr/bin/env python3
"""CloudNet CLI — HTTP client for the control plane API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
import yaml


def api_base_url() -> str:
    return os.environ.get("CLOUDNET_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")


def http_client() -> httpx.Client:
    return httpx.Client(base_url=api_base_url(), timeout=120.0)


def load_topology_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "name" not in data:
        sys.exit(f"Invalid topology file {path}: expected mapping with 'name'")
    return data


def resolve_topology_id(client: httpx.Client, topology_name: str) -> int:
    response = client.get("/topologies")
    response.raise_for_status()
    rows = response.json()
    matches = [t["id"] for t in rows if t.get("name") == topology_name]
    if not matches:
        sys.exit(
            f"No stored topology named {topology_name!r}. "
            "Create one first: cloudnet apply <file> [--deploy]"
        )
    return max(matches)


def cmd_apply(client: httpx.Client, args: argparse.Namespace) -> None:
    path = Path(args.file)
    body = load_topology_yaml(path)
    response = client.post("/topologies", json=body)
    if response.status_code >= 400:
        sys.exit(f"create topology failed: {response.status_code} {response.text}")
    created = response.json()
    tid = created["id"]
    print(f"Created topology id={tid} name={created['name']!r}")
    if args.deploy:
        dr = client.post(f"/topologies/{tid}/deploy")
        if dr.status_code >= 400:
            sys.exit(f"deploy failed: {dr.status_code} {dr.text}")
        print(json.dumps(dr.json(), indent=2))


def cmd_access(client: httpx.Client, args: argparse.Namespace) -> None:
    path = Path(args.file)
    name = load_topology_yaml(path)["name"]
    tid = resolve_topology_id(client, name)
    response = client.get(f"/topologies/{tid}/access")
    if response.status_code >= 400:
        sys.exit(f"access failed: {response.status_code} {response.text}")
    print(json.dumps(response.json(), indent=2))


def cmd_exec(client: httpx.Client, args: argparse.Namespace) -> None:
    path = Path(args.file)
    name = load_topology_yaml(path)["name"]
    tid = resolve_topology_id(client, name)
    response = client.post(
        f"/topologies/{tid}/nodes/{args.node}/exec",
        json={"command": args.command},
    )
    if response.status_code >= 400:
        sys.exit(f"exec failed: {response.status_code} {response.text}")
    print(json.dumps(response.json(), indent=2))


def cmd_workload(client: httpx.Client, args: argparse.Namespace) -> None:
    if args.demo_type != "http-demo":
        sys.exit(f"Unsupported workload {args.demo_type!r}")
    path = Path(args.file)
    topo_name = load_topology_yaml(path)["name"]
    tid = resolve_topology_id(client, topo_name)
    response = client.post(
        f"/topologies/{tid}/workloads/http-demo",
        json={"node": args.node},
    )
    if response.status_code >= 400:
        sys.exit(f"workload failed: {response.status_code} {response.text}")
    print(json.dumps(response.json(), indent=2))


def load_scenario_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        sys.exit(f"Invalid scenario file {path}: expected a mapping")
    for key in ("scenario", "topology", "steps"):
        if key not in data:
            sys.exit(f"Scenario file {path} must contain top-level key {key!r}")
    return data


def _format_scenario_step_line(step: dict) -> str:
    label = step["step"]
    result = step["result"]
    ok = step.get("step_passed", False)

    if label.startswith("fail "):
        node = label.removeprefix("fail ").strip()
        if result == "SUCCESS":
            return f"fail {node}: injected"
        return f"fail {node}: {result}"

    if label == "reconcile":
        if result == "RECONCILED":
            return "reconcile: repaired"
        return f"reconcile: {result}"

    if label == "drift":
        if result in ("DETECTED", "CLEAN"):
            return f"drift: {result.lower()}"
        return f"drift: {result}"

    if label == "validate":
        exp = step.get("expect")
        if exp == "fail" and result == "FAILED" and ok:
            return "validate: FAILED (expected)"
        return f"validate: {result}"

    return f"{label}: {result}"


def _print_scenario_result(body: dict) -> None:
    for step in body.get("steps", []):
        sym = "✔" if step.get("step_passed") else "✖"
        line = _format_scenario_step_line(step)
        print(f"{sym} {line}")
    print()
    print(f"Scenario {body.get('scenario')!r}: {body.get('status')}")


def cmd_run(client: httpx.Client, args: argparse.Namespace) -> None:
    path = Path(args.file)
    body = load_scenario_yaml(path)
    response = client.post("/scenarios/run", json=body)
    if response.status_code >= 400:
        sys.exit(f"scenario run failed: {response.status_code} {response.text}")
    data = response.json()
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _print_scenario_result(data)
    sys.exit(0 if data.get("status") == "PASSED" else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudnet",
        description="CloudNet Testbed CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_apply = sub.add_parser("apply", help="Create topology from YAML file")
    p_apply.add_argument("file", help="Path to topology YAML")
    p_apply.add_argument(
        "--deploy",
        action="store_true",
        help="Also POST /deploy after create",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_access = sub.add_parser("access", help="Show access summary for deployed topology")
    p_access.add_argument("file", help="Topology YAML (matched by name)")
    p_access.set_defaults(func=cmd_access)

    p_exec = sub.add_parser("exec", help="Run shell command on a node via SSM")
    p_exec.add_argument("file", help="Topology YAML (matched by name)")
    p_exec.add_argument("node", help="Logical node name")
    p_exec.add_argument(
        "command",
        help='Shell command string (quote if it contains spaces)',
    )
    p_exec.set_defaults(func=cmd_exec)

    p_wl = sub.add_parser("workload", help="Deploy simple demo workloads")
    p_wl.add_argument(
        "demo_type",
        choices=["http-demo"],
        help="Workload type",
    )
    p_wl.add_argument("file", help="Topology YAML (matched by name)")
    p_wl.add_argument("--node", required=True, help="Target host node")
    p_wl.set_defaults(func=cmd_workload)

    p_run = sub.add_parser(
        "run",
        help="Create topology, deploy, and execute a scenario file",
    )
    p_run.add_argument("file", help="Scenario YAML (scenario, topology, steps)")
    p_run.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of step summary lines",
    )
    p_run.set_defaults(func=cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    with http_client() as client:
        args.func(client, args)


if __name__ == "__main__":
    main()
