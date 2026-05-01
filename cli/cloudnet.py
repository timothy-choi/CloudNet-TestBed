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


def _fmt_step_duration(ms: int) -> str:
    if ms >= 1000:
        return f"{ms / 1000:.1f}s"
    return f"{ms}ms"


def _display_validate_token(val: str | None) -> str:
    if val == "PASSED":
        return "pass"
    if val == "FAILED":
        return "fail"
    return val or "—"


def _provider_display(provider_action: str | None) -> str:
    if provider_action == "stop_server":
        return "stop_instance"
    return provider_action or "—"


def _print_scenario_report(body: dict) -> None:
    steps = body.get("steps") or []
    for i, step in enumerate(steps, start=1):
        ok = step.get("status") == "PASSED"
        sym = "✔" if ok else "✖"
        name = step.get("name", "")
        action = step.get("action", "")
        print(f"Step {i}: {name}")

        if action == "validate":
            print(f"  expected: {_display_validate_token(step.get('expected'))}")
            print(f"  actual: {_display_validate_token(step.get('actual'))}")
        elif action == "fail":
            print(f"  action: {_provider_display(step.get('provider_action'))}")
        elif action == "drift":
            print(f"  expected: {(step.get('expected') or '').lower()}")
            print(f"  actual: {(step.get('actual') or '').lower()}")
        elif action == "reconcile":
            print(f"  expected: repaired")
            print(f"  actual: {(step.get('actual') or '').lower()}")

        ms = int(step.get("duration_ms") or 0)
        print(f"  duration: {_fmt_step_duration(ms)}")
        if step.get("message"):
            print(f"  note: {step['message']}")
        print(f"  result: {sym}")
        print()

    overall = body.get("status", "")
    total_ms = int(body.get("duration_ms") or 0)
    print(f"Scenario {overall}")
    print(f"Total duration: {_fmt_step_duration(total_ms)}")
    rid = body.get("scenario_run_id")
    if rid is not None:
        base = api_base_url()
        print(f"Report: GET {base}/scenarios/{rid}/results")


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
        _print_scenario_report(data)
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
        help="Print raw JSON instead of experiment report",
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
