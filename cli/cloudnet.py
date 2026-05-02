#!/usr/bin/env python3
"""CloudNet CLI — HTTP client for the control plane API."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx
import yaml


def api_base_url() -> str:
    return os.environ.get("CLOUDNET_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")


def http_client() -> httpx.Client:
    return httpx.Client(base_url=api_base_url(), timeout=120.0)


def templates_dir() -> Path:
    """Directory containing built-in scenario templates (repo-root ``templates/``)."""
    return Path(__file__).resolve().parent.parent / "templates"


def cmd_templates_list(client: httpx.Client, args: argparse.Namespace) -> int:
    directory = templates_dir()
    if not directory.is_dir():
        print(f"templates directory missing: {directory}", file=sys.stderr)
        return 1
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        print("(no templates)", file=sys.stderr)
        return 0
    for path in paths:
        label = path.stem
        try:
            data = yaml.safe_load(path.read_text())
            scen = data.get("scenario") if isinstance(data, dict) else None
            name = scen.get("name") if isinstance(scen, dict) else None
        except (OSError, yaml.YAMLError):
            name = None
        if name:
            print(f"{label}\t{name}")
        else:
            print(label)
    return 0


def cmd_templates_run(client: httpx.Client, args: argparse.Namespace) -> int:
    raw = args.template.strip()
    if raw.endswith(".yaml"):
        raw = raw[:-5]
    src = templates_dir() / f"{raw}.yaml"
    if not src.is_file():
        print(
            f"unknown template {raw!r}; try: cloudnet templates list",
            file=sys.stderr,
        )
        return 1
    fd, tmp_name = tempfile.mkstemp(prefix="cloudnet-template-", suffix=".yaml")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy(src, tmp_path)
        run_args = argparse.Namespace(
            file=str(tmp_path),
            json=getattr(args, "json", False),
            cleanup=getattr(args, "cleanup", False),
        )
        return cmd_run(client, run_args)
    finally:
        tmp_path.unlink(missing_ok=True)


def cmd_state_show(client: httpx.Client, args: argparse.Namespace) -> int:
    """Print persisted deployment snapshot JSON (``state.json``)."""
    del args  # unused
    from app.services.local_state_store import get_state_path, load_state

    state = load_state()
    print(json.dumps(state, indent=2))
    print(f"# CLOUDNET_STATE_FILE={get_state_path()}", file=sys.stderr)
    return 0


def cmd_state_clear(client: httpx.Client, args: argparse.Namespace) -> int:
    """Clear local ``state.json`` (does not delete provider resources)."""
    del args  # unused
    from app.services.local_state_store import clear_all_local_state

    clear_all_local_state()
    return 0


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


def _fmt_total_time_ms(duration_ms: int) -> str:
    if duration_ms >= 1000:
        return f"{duration_ms / 1000.0:.1f}s"
    return f"{duration_ms}ms"


def _scenario_step_one_liner(index: int, step: dict[str, object]) -> str:
    ok = step.get("status") == "PASSED"
    mark = "✔" if ok else "✖"
    action = str(step.get("action") or "")
    actual = step.get("actual")
    expected = step.get("expected")
    name = str(step.get("name") or "")

    if action == "deploy":
        return f"[{index}] Deploy topology {mark}"

    if action == "validate":
        if ok:
            if actual == "PASSED":
                return f"[{index}] Validate {mark} PASSED"
            if actual == "FAILED" and expected == "FAILED":
                return f"[{index}] Validate {mark} FAILED (expected)"
            return f"[{index}] Validate {mark} {actual}"
        return f"[{index}] Validate {mark} {actual or 'FAILED'}"

    if action == "fail":
        node = name.removeprefix("fail ").strip() if name.startswith("fail ") else name
        return f"[{index}] Fail {node} {mark}"

    if action == "drift":
        if ok and actual in ("DETECTED", "CLEAN"):
            return f"[{index}] Drift {mark} {str(actual).lower()}"
        return f"[{index}] Drift {mark} {actual or 'FAILED'}"

    if action == "reconcile":
        if ok and actual == "RECONCILED":
            return f"[{index}] Reconcile {mark} repaired"
        return f"[{index}] Reconcile {mark} {actual or 'FAILED'}"

    if action == "cleanup":
        if ok and actual:
            return f"[{index}] Cleanup {mark} {str(actual).lower()}"
        return f"[{index}] Cleanup {mark} {actual or 'FAILED'}"

    return f"[{index}] {name or action} {mark}"


def _print_requirements(body: dict) -> None:
    req = body.get("requirements")
    if not req:
        return
    print()
    print("Requirements:")
    print()
    for category, payload in req.items():
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "")
        mark = "✔" if status == "PASSED" else "✖"
        line = f"{mark} {category}: {status}"
        detail = ""
        if category == "availability":
            sr = payload.get("success_rate")
            th = payload.get("threshold")
            detail = f" ({sr} >= {th})"
        elif category == "latency":
            parts: list[str] = []
            if payload.get("max_avg_ms") is not None:
                parts.append(
                    f"avg {payload.get('avg_ms')}ms <= {payload['max_avg_ms']}ms"
                )
            if payload.get("max_p95_ms") is not None:
                parts.append(
                    f"p95 {payload.get('p95_ms')}ms <= {payload['max_p95_ms']}ms"
                )
            if parts:
                detail = " (" + ", ".join(parts) + ")"
        elif category == "recovery":
            rs = payload.get("recovery_seconds")
            mx = payload.get("max_recovery_seconds")
            if rs is not None and mx is not None:
                detail = f" ({rs}s <= {mx}s)"
            else:
                detail = " (not measured)"
        print(f"{line}{detail}")


def _print_scenario_report(body: dict) -> None:
    scenario_name = body.get("scenario", "scenario")
    print(f"Running scenario: {scenario_name}")
    if body.get("scenario_run_id") is not None:
        print(f"scenario_run_id={body['scenario_run_id']}  topology_id={body.get('topology_id')}")
    print()
    steps = body.get("steps") or []
    for i, step in enumerate(steps, start=1):
        print(_scenario_step_one_liner(i, step))
    _print_requirements(body)
    print()
    overall = body.get("status", "")
    print(f"Scenario {overall}")
    total_ms = int(body.get("duration_ms") or 0)
    print(f"Total time: {_fmt_total_time_ms(total_ms)}")


def cmd_run(client: httpx.Client, args: argparse.Namespace) -> int:
    path = Path(args.file)
    body = load_scenario_yaml(path)
    if getattr(args, "cleanup", False):
        body["cleanup"] = True
    response = client.post("/scenarios/run", json=body)
    if response.status_code >= 400:
        print(
            f"scenario run failed: {response.status_code} {response.text}",
            file=sys.stderr,
        )
        return 1
    data = response.json()
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _print_scenario_report(data)
    return 0 if data.get("status") == "PASSED" else 1


def cmd_validate_topology(client: httpx.Client, args: argparse.Namespace) -> int:
    """Validate topology YAML locally (compiler + quotas); no HTTP deploy."""
    path = Path(args.file)
    try:
        raw_text = path.read_text()
    except OSError as exc:
        print(f"failed to read file: {exc}", file=sys.stderr)
        return 1
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        print(f"invalid YAML: {exc}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("topology file must contain a YAML mapping", file=sys.stderr)
        return 1
    try:
        from pydantic import ValidationError

        from app.services.scenario_service import ScenarioError
        from app.services.topology_validate_service import validate_topology_yaml_dict

        result = validate_topology_yaml_dict(data)
    except ValidationError as exc:
        print(f"topology schema invalid: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"topology invalid: {exc}", file=sys.stderr)
        return 1
    except ScenarioError as exc:
        print(f"topology invalid: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudnet",
        description="CloudNet — reliability testing platform for cloud topologies",
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
    p_run.add_argument(
        "--cleanup",
        action="store_true",
        help="Request deployment cleanup after the scenario run (API body cleanup: true)",
    )
    p_run.set_defaults(func=cmd_run)

    p_validate_topo = sub.add_parser(
        "validate-topology",
        help="Validate topology YAML (compile + quotas + warnings); does not deploy",
    )
    p_validate_topo.add_argument("file", help="Path to topology YAML")
    p_validate_topo.set_defaults(func=cmd_validate_topology)

    p_templates = sub.add_parser(
        "templates",
        help="Built-in scenario templates (copy to temp file, then run)",
    )
    tpl_sub = p_templates.add_subparsers(
        dest="tpl_action",
        required=True,
        metavar="ACTION",
    )

    p_tpl_list = tpl_sub.add_parser(
        "list",
        help="List built-in scenario templates",
    )
    p_tpl_list.set_defaults(func=cmd_templates_list)

    p_tpl_run = tpl_sub.add_parser(
        "run",
        help="Run a built-in template (same as cloudnet run on a temp copy)",
    )
    p_tpl_run.add_argument(
        "template",
        help="Template name (e.g. backend-failure or backend-failure.yaml)",
    )
    p_tpl_run.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of experiment report",
    )
    p_tpl_run.add_argument(
        "--cleanup",
        action="store_true",
        help="Request deployment cleanup after the scenario run",
    )
    p_tpl_run.set_defaults(func=cmd_templates_run)

    p_state = sub.add_parser(
        "state",
        help="Local deployment snapshot file (state.json)",
    )
    state_sub = p_state.add_subparsers(
        dest="state_action",
        required=True,
        metavar="ACTION",
    )
    p_state_show = state_sub.add_parser(
        "show",
        help="Print JSON from CLOUDNET_STATE_FILE (default repo state.json)",
    )
    p_state_show.set_defaults(func=cmd_state_show)
    p_state_clear = state_sub.add_parser(
        "clear",
        help="Reset local state file (empty deployments object)",
    )
    p_state_clear.set_defaults(func=cmd_state_clear)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    with http_client() as client:
        rc = args.func(client, args)
    if isinstance(rc, int):
        sys.exit(rc)


if __name__ == "__main__":
    main()
