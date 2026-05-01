import re
from typing import Any

from app.models import Topology
from app.services.deployment_service import compile_deployment_plan


def export_terraform(topology: Topology) -> dict[str, Any]:
    if topology.id is None:
        raise ValueError("topology must be saved before Terraform export")

    plan = compile_deployment_plan(topology)
    files = {
        "main.tf": _render_main_tf(plan),
        "variables.tf": _render_variables_tf(),
        "outputs.tf": _render_outputs_tf(plan),
    }
    return {
        "topology_id": topology.id,
        "provider": "aws",
        "files": files,
    }


def _render_main_tf(plan: dict[str, Any]) -> str:
    lines = [
        'provider "aws" {',
        "  region = var.aws_region",
        "}",
        "",
        'resource "aws_vpc" "cloudnet" {',
        '  cidr_block           = "10.0.0.0/16"',
        "  enable_dns_hostnames = true",
        "  enable_dns_support   = true",
        "",
        "  tags = {",
        f'    Name      = "{_hcl_string(plan["topology_name"])}"',
        '    Project   = "CloudNet"',
        '    ManagedBy = "CloudNet"',
        "  }",
        "}",
        "",
        'resource "aws_internet_gateway" "cloudnet" {',
        "  vpc_id = aws_vpc.cloudnet.id",
        "",
        "  tags = {",
        f'    Name      = "{_hcl_string(plan["topology_name"])}-igw"',
        '    Project   = "CloudNet"',
        '    ManagedBy = "CloudNet"',
        "  }",
        "}",
        "",
        'resource "aws_route_table" "cloudnet_public" {',
        "  vpc_id = aws_vpc.cloudnet.id",
        "",
        "  route {",
        '    cidr_block = "0.0.0.0/0"',
        "    gateway_id = aws_internet_gateway.cloudnet.id",
        "  }",
        "",
        "  tags = {",
        f'    Name      = "{_hcl_string(plan["topology_name"])}-public-rt"',
        '    Project   = "CloudNet"',
        '    ManagedBy = "CloudNet"',
        "  }",
        "}",
        "",
    ]

    for index, network in enumerate(plan["networks"], start=1):
        subnet_name = _resource_name(network["name"])
        lines.extend(
            [
                f'resource "aws_subnet" "{subnet_name}" {{',
                "  vpc_id                  = aws_vpc.cloudnet.id",
                f'  cidr_block              = "{_hcl_string(network["subnet"])}"',
                "  map_public_ip_on_launch = true",
                "",
                "  tags = {",
                f'    Name      = "{_hcl_string(network["name"])}-subnet"',
                '    Project   = "CloudNet"',
                '    ManagedBy = "CloudNet"',
                "  }",
                "}",
                "",
                f'resource "aws_route_table_association" "{subnet_name}" {{',
                f"  subnet_id      = aws_subnet.{subnet_name}.id",
                "  route_table_id = aws_route_table.cloudnet_public.id",
                "}",
                "",
            ]
        )

    lines.extend(
        [
            'resource "aws_security_group" "cloudnet" {',
            '  name        = "cloudnet-sg"',
            '  description = "CloudNet TestBed security group"',
            "  vpc_id      = aws_vpc.cloudnet.id",
            "",
            "  egress {",
            "    from_port   = 0",
            "    to_port     = 0",
            '    protocol    = "-1"',
            '    cidr_blocks = ["0.0.0.0/0"]',
            "  }",
            "",
            "  tags = {",
            '    Name      = "cloudnet-sg"',
            '    Project   = "CloudNet"',
            '    ManagedBy = "CloudNet"',
            "  }",
            "}",
            "",
        ]
    )
    lines.extend(_render_security_group_rules(plan))
    lines.extend(_render_instances(plan))
    return "\n".join(lines).rstrip() + "\n"


def _render_security_group_rules(plan: dict[str, Any]) -> list[str]:
    firewall_rules = plan["firewall_rules"] or [
        {"name": "allow-cloudnet-icmp", "protocol": "icmp", "from": "*", "to": "*"}
    ]
    lines: list[str] = []
    for rule in firewall_rules:
        rule_name = _resource_name(rule["name"])
        protocol = rule["protocol"]
        from_port = -1
        to_port = -1
        if protocol == "tcp":
            from_port = int(rule.get("port") or 0)
            to_port = int(rule.get("port") or 65535)
        lines.extend(
            [
                f'resource "aws_security_group_rule" "{rule_name}" {{',
                '  type              = "ingress"',
                f'  protocol          = "{protocol}"',
                f"  from_port         = {from_port}",
                f"  to_port           = {to_port}",
                "  security_group_id = aws_security_group.cloudnet.id",
                "  self              = true",
                "}",
                "",
            ]
        )
    return lines


def _render_instances(plan: dict[str, Any]) -> list[str]:
    first_subnet_by_node: dict[str, str] = {}
    for network in plan["networks"]:
        subnet_name = _resource_name(network["name"])
        for node_name in network["attached_nodes"]:
            first_subnet_by_node.setdefault(node_name, subnet_name)

    lines: list[str] = []
    for server in plan["servers"]:
        if server["type"] != "host":
            continue
        instance_name = _resource_name(server["name"])
        subnet_name = first_subnet_by_node[server["name"]]
        lines.extend(
            [
                f'resource "aws_instance" "{instance_name}" {{',
                "  ami                    = var.aws_ami_id",
                "  instance_type          = var.aws_instance_type",
                f"  subnet_id              = aws_subnet.{subnet_name}.id",
                "  vpc_security_group_ids = [aws_security_group.cloudnet.id]",
                "",
                "  tags = {",
                f'    Name      = "{_hcl_string(server["name"])}"',
                '    Project   = "CloudNet"',
                '    ManagedBy = "CloudNet"',
                "  }",
                "}",
                "",
            ]
        )
    return lines


def _render_variables_tf() -> str:
    return """variable "aws_region" {
  description = "AWS region for CloudNet resources"
  type        = string
}

variable "aws_ami_id" {
  description = "AMI ID for CloudNet EC2 instances"
  type        = string
}

variable "aws_instance_type" {
  description = "EC2 instance type for CloudNet hosts"
  type        = string
  default     = "t3.micro"
}
"""


def _render_outputs_tf(plan: dict[str, Any]) -> str:
    host_names = [
        _resource_name(server["name"])
        for server in plan["servers"]
        if server["type"] == "host"
    ]
    lines = [
        'output "vpc_id" {',
        "  value = aws_vpc.cloudnet.id",
        "}",
        "",
        'output "subnet_ids" {',
        "  value = [",
    ]
    for network in plan["networks"]:
        lines.append(f"    aws_subnet.{_resource_name(network['name'])}.id,")
    lines.extend(["  ]", "}", "", 'output "instance_ids" {', "  value = {"])
    for host_name in host_names:
        lines.append(f"    {host_name} = aws_instance.{host_name}.id")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def _resource_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", value.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return "cloudnet"
    if normalized[0].isdigit():
        return f"cloudnet_{normalized}"
    return normalized


def _hcl_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
