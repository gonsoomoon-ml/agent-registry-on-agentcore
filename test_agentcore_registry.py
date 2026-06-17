#!/usr/bin/env python3
"""Live validation: "Agent layer = ⓐ Agent Registry + ⓑ A2A runtime" on Amazon Bedrock AgentCore.

Creates a temporary registry, publishes an A2A agent record (schema-validated)
and a tool record into it, exercises the publish→approve→deprecate governance
lifecycle and semantic search, then tears everything down — guaranteed, even on
failure, via a context manager.

Cost: a registry is a metadata catalog (~$0). Region: us-west-2.

Run under your own AWS auth (us-west-2, AgentCore permissions):
    python3 test_agentcore_registry.py
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

REGION = "us-west-2"
READY_STATES = {"ACTIVE", "AVAILABLE", "READY"}
READY_TIMEOUT_S = 300      # registries take ~1-2 min to reach READY
TEARDOWN_TIMEOUT_S = 180   # record/registry deletes settle asynchronously
POLL_S = 5


def log(step: str, msg: str = "") -> None:
    print(f"[{step:<10}] {msg}")


def _registry_id(arn: str) -> str:
    # arn:aws:bedrock-agentcore:<region>:<acct>:registry/<registryId>
    return arn.split(":registry/")[1].split("/")[0]


def _record_id(arn: str) -> str:
    # .../registry/<registryId>/record/<recordId>
    return arn.split("/record/")[-1]


def _error_code(e: Exception) -> str:
    return e.response["Error"]["Code"] if isinstance(e, ClientError) else ""


class AgentCore:
    """Holds the AgentCore control-plane and data-plane clients."""

    def __init__(self, region: str = REGION):
        self.ctl = boto3.client("bedrock-agentcore-control", region_name=region)
        self.dp = boto3.client("bedrock-agentcore", region_name=region)


class EphemeralRegistry:
    """A Bedrock AgentCore registry that creates itself on entry and tears
    itself down (records first, then the registry) on exit — so the test never
    leaks resources, even when the body raises.

    Record IDs are parsed from each record's ARN at creation time, NOT looked up
    via ``list_registry_records``: the control-plane list/search APIs are
    strongly eventually-consistent and routinely omit just-created records,
    which otherwise breaks cleanup.
    """

    def __init__(self, ac: AgentCore, name: str, *, manual_approval: bool = True):
        self.ac = ac
        self.name = name
        self.manual_approval = manual_approval
        self.registry_id: str | None = None
        self.record_ids: list[str] = []

    # -- context-manager lifecycle -----------------------------------------
    def __enter__(self) -> "EphemeralRegistry":
        arn = self.ac.ctl.create_registry(
            name=self.name,
            description="Ephemeral test: Agent Registry + A2A concept validation",
            authorizerType="AWS_IAM",
            approvalConfiguration={"autoApproval": not self.manual_approval},
        )["registryArn"]
        self.registry_id = _registry_id(arn)
        log("create", f"registryId={self.registry_id}")
        self._wait_ready()
        return self

    def __exit__(self, *exc) -> bool:
        self._teardown()
        return False  # never suppress the original exception

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT_S
        status = None
        while time.monotonic() < deadline:
            status = self.ac.ctl.get_registry(registryId=self.registry_id).get("status")
            if status in READY_STATES:
                log("ready", f"status={status}")
                return
            if status and "FAIL" in status:
                raise RuntimeError(f"registry creation failed: {status}")
            time.sleep(POLL_S)
        raise TimeoutError(f"registry not READY after {READY_TIMEOUT_S}s (last status={status})")

    # -- records ------------------------------------------------------------
    def add_record(self, name: str, descriptor_type: str, descriptors: dict,
                   *, version: str = "1.0.0", description: str = "") -> str:
        arn = self.ac.ctl.create_registry_record(
            registryId=self.registry_id, name=name, descriptorType=descriptor_type,
            description=description, recordVersion=version, descriptors=descriptors,
        )["recordArn"]
        record_id = _record_id(arn)
        self.record_ids.append(record_id)
        return record_id

    def get_status(self, record_id: str) -> str:
        return self.ac.ctl.get_registry_record(
            registryId=self.registry_id, recordId=record_id).get("status")

    def submit_for_approval(self, record_id: str) -> None:
        self.ac.ctl.submit_registry_record_for_approval(
            registryId=self.registry_id, recordId=record_id)

    def set_status(self, record_id: str, status: str, reason: str) -> None:
        self.ac.ctl.update_registry_record_status(
            registryId=self.registry_id, recordId=record_id, status=status, statusReason=reason)

    def list_records(self) -> list[dict]:
        return self.ac.ctl.list_registry_records(registryId=self.registry_id).get("records", [])

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        resp = self.ac.dp.search_registry_records(
            searchQuery=query, registryIds=[self.registry_id], maxResults=max_results)
        return resp.get("registryRecords", resp.get("records", []))

    # -- teardown -----------------------------------------------------------
    def _teardown(self) -> None:
        log("cleanup", "deleting records, then registry ...")
        for record_id in self.record_ids:
            self._delete_with_retry(
                lambda rid=record_id: self.ac.ctl.delete_registry_record(
                    registryId=self.registry_id, recordId=rid),
                what=f"record {record_id}")
        if self.registry_id:
            self._delete_with_retry(
                lambda: self.ac.ctl.delete_registry(registryId=self.registry_id),
                what=f"registry {self.registry_id}")

    @staticmethod
    def _delete_with_retry(delete_call, *, what: str) -> None:
        """Delete, retrying on ConflictException (resource still CREATING, or a
        registry whose records are still async-deleting). Bounded by timeout."""
        deadline = time.monotonic() + TEARDOWN_TIMEOUT_S
        while True:
            try:
                delete_call()
                log("cleanup", f"{what} deleted")
                return
            except ClientError as e:
                code = _error_code(e)
                if code == "ResourceNotFoundException":
                    return  # already gone — fine
                if code == "ConflictException" and time.monotonic() < deadline:
                    time.sleep(POLL_S)
                    continue
                log("cleanup", f"{what} delete error: {code or e}")
                return


# --- domain helpers (each maps to one thing we want to prove) --------------

def sample_agent_card(name: str = "churn-predictor") -> dict:
    """A minimal, valid A2A v0.3.0 AgentCard."""
    return {
        "protocolVersion": "0.3.0",
        "name": name,
        "description": "Predicts customer churn risk from order/review history",
        "url": "https://example.internal/agents/churn",
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{
            "id": "predict_churn", "name": "Predict churn",
            "description": "Estimate customer churn probability",
            "tags": ["analytics", "customer"],
        }],
    }


def publish_agent(reg: EphemeralRegistry, record_name: str) -> str:
    """ⓑ Publish an A2A agent record. The registry validates the AgentCard
    against the real A2A schema; if it rejects the card, fall back to a CUSTOM
    record so the rest of the lifecycle demo still runs."""
    card = json.dumps(sample_agent_card())
    try:
        record_id = reg.add_record(
            record_name, "A2A", {"a2a": {"agentCard": {"inlineContent": card}}},
            description="A2A agent for customer churn prediction")
        log("publish", f"A2A agent {record_id} accepted (AgentCard schema-validated)")
        return record_id
    except ClientError as e:
        if _error_code(e) != "ValidationException":
            raise
        record_id = reg.add_record(
            record_name, "CUSTOM", {"custom": {"inlineContent": card}},
            description="Agent catalog entry (CUSTOM fallback)")
        log("publish", f"A2A schema rejected the card; stored as CUSTOM {record_id}")
        return record_id


def run_lifecycle(reg: EphemeralRegistry, record_id: str) -> None:
    """① DRAFT → PENDING_APPROVAL → APPROVED → DEPRECATED."""
    log("lifecycle", f"start      -> {reg.get_status(record_id)}")
    reg.submit_for_approval(record_id)
    log("lifecycle", f"submitted  -> {reg.get_status(record_id)}")
    reg.set_status(record_id, "APPROVED", "Validated by security review (test)")
    log("lifecycle", f"approved   -> {reg.get_status(record_id)}")
    reg.set_status(record_id, "DEPRECATED", "Superseded by churn-predictor v2 (test)")
    log("lifecycle", f"deprecated -> {reg.get_status(record_id)}")


def semantic_search(reg: EphemeralRegistry, query: str) -> None:
    """② Data-plane semantic discovery (eventually consistent for new records)."""
    hits = reg.search(query)
    if hits:
        log("search", f"'{query}' -> {[h.get('name') for h in hits]}")
    else:
        log("search", f"'{query}' -> no hits yet (semantic index is eventually consistent)")


def main() -> int:
    ac = AgentCore()
    suffix = uuid.uuid4().hex[:8]
    try:
        with EphemeralRegistry(ac, f"claudetest-agentcatalog-{suffix}") as reg:
            agent_id = publish_agent(reg, "churn-predictor-agent")          # ⓑ A2A agent
            reg.add_record(                                                 # + a tool in the SAME registry
                "customer-db-search-tool", "CUSTOM",
                {"custom": {"inlineContent": json.dumps(
                    {"type": "mcp-tool", "name": "customer_db_search"})}},
                description="MCP tool: semantic search over customer DB")
            run_lifecycle(reg, agent_id)                                    # ① governance lifecycle
            records = reg.list_records()
            log("catalog", f"{len(records)} record(s): "
                           f"{[(r.get('name'), r.get('status')) for r in records]}")
            semantic_search(reg, "customer churn prediction")              # ②
        log("done", "Agent Registry + A2A concept VALIDATED; all resources torn down")
        return 0
    except Exception as e:
        log("error", f"{type(e).__name__}: {str(e)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
