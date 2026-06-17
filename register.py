#!/usr/bin/env python3
"""(2) Register the agent and tool into the registry.

Creates the agent as a real managed **AgentCore Harness** (model + system prompt
+ tool, no container), a persistent **Agent Registry**, and two records — the
agent record carries the harnessArn so a client can later discover and invoke it.
Ids are written to STATE_FILE for client.py.

    python3 register.py
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

import agents_and_tools as at

STATE_FILE = ".agentcore_state.json"
HARNESS_ROLE_NAME = "AgentCoreHarnessRole"   # scoped to us-east-1 (reused)
READY = {"READY", "ACTIVE", "AVAILABLE"}
WAIT_TIMEOUT_S = 300
POLL_S = 5

ctl = boto3.client("bedrock-agentcore-control", region_name=at.REGION)
iam = boto3.client("iam")


def log(step: str, msg: str = "") -> None:
    print(f"[{step:<12}] {msg}")


def _wait(get_status, *, what: str) -> str:
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    status = None
    while time.monotonic() < deadline:
        status = get_status()
        if status in READY:
            return status
        if status and "FAIL" in status:
            raise RuntimeError(f"{what} failed: status={status}")
        time.sleep(POLL_S)
    raise TimeoutError(f"{what} not ready after {WAIT_TIMEOUT_S}s (last={status})")


def create_harness() -> tuple[str, str]:
    """Create the agent as an AgentCore Harness. Returns (harnessId, harnessArn)."""
    role_arn = iam.get_role(RoleName=HARNESS_ROLE_NAME)["Role"]["Arn"]
    # harnessName must match [a-zA-Z][a-zA-Z0-9_]{0,39} (no hyphens)
    harness = ctl.create_harness(
        harnessName=f"{at.AGENT_NAME.replace('-', '_')}_{uuid.uuid4().hex[:8]}",
        executionRoleArn=role_arn,
        model={"bedrockModelConfig": {"modelId": at.MODEL_ID, "apiFormat": "converse_stream"}},
        systemPrompt=[{"text": at.SYSTEM_PROMPT}],
        tools=at.HARNESS_TOOLS,
    )["harness"]
    harness_id, harness_arn = harness["harnessId"], harness["arn"]
    log("harness", f"created {harness_id} status={harness['status']}")
    status = _wait(lambda: ctl.get_harness(harnessId=harness_id)["harness"]["status"], what="harness")
    log("harness", f"status={status} arn={harness_arn}")
    return harness_id, harness_arn


def create_registry() -> str:
    arn = ctl.create_registry(
        name=f"agent-catalog-{uuid.uuid4().hex[:8]}",
        description="Agent + tool catalog (churn demo)",
        authorizerType="AWS_IAM",
        approvalConfiguration={"autoApproval": True},
    )["registryArn"]
    registry_id = arn.split(":registry/")[1].split("/")[0]
    _wait(lambda: ctl.get_registry(registryId=registry_id)["status"], what="registry")
    log("registry", f"ready {registry_id}")
    return registry_id


def add_record(registry_id: str, name: str, payload: dict) -> str:
    arn = ctl.create_registry_record(
        registryId=registry_id, name=name, descriptorType="CUSTOM", recordVersion="1.0.0",
        descriptors={"custom": {"inlineContent": json.dumps(payload)}},
    )["recordArn"]
    return arn.split("/record/")[-1]


def main() -> int:
    try:
        harness_id, harness_arn = create_harness()
        registry_id = create_registry()

        agent_record = add_record(registry_id, f"{at.AGENT_NAME}-agent", {
            "kind": "agentcore-harness",
            "agentName": at.AGENT_NAME,
            "harnessArn": harness_arn,          # <- how the client finds the runtime
            "skills": at.AGENT_SKILLS,
        })
        tool_record = add_record(registry_id, at.CUSTOMER_DB_SEARCH_TOOL["name"], {
            "kind": "tool",
            **at.CUSTOMER_DB_SEARCH_TOOL,
        })
        log("registry", f"agent record={agent_record}  tool record={tool_record}")

        state = {
            "region": at.REGION,
            "registryId": registry_id,
            "harnessId": harness_id,
            "agentRecordId": agent_record,
            "toolRecordId": tool_record,
        }
        with open(STATE_FILE, "w") as fh:
            json.dump(state, fh, indent=2)
        log("done", f"state -> {STATE_FILE}  (now run: python3 client.py)")
        return 0
    except ClientError as e:
        log("error", f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
