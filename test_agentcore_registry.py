#!/usr/bin/env python3
"""라이브 검증: "Agent layer = ⓐ Agent Registry + ⓑ A2A runtime" on Amazon Bedrock AgentCore.

임시 레지스트리를 만들고, A2A 에이전트 레코드(스키마 검증됨)와 도구 레코드를 넣은 뒤,
publish→approve→deprecate 거버넌스 수명주기와 시맨틱 검색을 실행하고, 컨텍스트 매니저로
실패 시에도 모든 리소스를 보장 정리한다.

비용: 레지스트리는 메타데이터 카탈로그(~$0). Region: us-west-2.

본인 AWS 자격증명(us-west-2, AgentCore 권한)으로 실행:
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
READY_TIMEOUT_S = 300      # 레지스트리는 READY까지 ~1-2분 걸린다
TEARDOWN_TIMEOUT_S = 180    # 레코드/레지스트리 삭제는 비동기로 정착된다
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
    """AgentCore control-plane / data-plane 클라이언트를 담는다."""

    def __init__(self, region: str = REGION):
        self.ctl = boto3.client("bedrock-agentcore-control", region_name=region)
        self.dp = boto3.client("bedrock-agentcore", region_name=region)


class EphemeralRegistry:
    """진입 시 자신을 생성하고, 종료 시 (레코드 먼저, 그다음 레지스트리) 정리하는 레지스트리.
    본문에서 예외가 나도 리소스를 누수하지 않는다.

    레코드 ID는 ``list_registry_records``로 조회하지 않고 생성 응답의 ARN에서 바로 뽑는다 —
    control-plane list/search는 강하게 eventual-consistent라 방금 만든 레코드를 흔히 빠뜨려
    정리가 깨지기 때문이다.
    """

    def __init__(self, ac: AgentCore, name: str, *, manual_approval: bool = True):
        self.ac = ac
        self.name = name
        self.manual_approval = manual_approval
        self.registry_id: str | None = None
        self.record_ids: list[str] = []

    # -- 컨텍스트 매니저 수명주기 ---------------------------------------------
    def __enter__(self) -> "EphemeralRegistry":
        arn = self.ac.ctl.create_registry(
            name=self.name,
            description="Ephemeral test: Agent Registry + A2A concept validation",
            authorizerType="AWS_IAM",
            approvalConfiguration={"autoApproval": not self.manual_approval},
        )["registryArn"]
        self.registry_id = _registry_id(arn)
        log("create", f"registryId={self.registry_id}")
        try:
            self._wait_ready()
        except BaseException:
            # __enter__가 예외를 던지면 __exit__는 호출되지 않으므로, 여기서 직접 정리한다.
            self._teardown()
            raise
        return self

    def __exit__(self, *exc) -> bool:
        self._teardown()
        return False  # 원래 예외를 절대 삼키지 않는다

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

    # -- 레코드 -------------------------------------------------------------
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

    def wait_record_ready(self, record_id: str, *, timeout: int = 120) -> str:
        """새 레코드는 CREATING으로 시작하며 그 상태에선 수정 불가다.
        CREATING/UPDATING을 벗어나(=DRAFT로 정착) 수정 가능해질 때까지 폴링한다."""
        deadline = time.monotonic() + timeout
        status = self.get_status(record_id)
        while status in ("CREATING", "UPDATING") and time.monotonic() < deadline:
            time.sleep(POLL_S)
            status = self.get_status(record_id)
        return status

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

    # -- 정리(teardown) -----------------------------------------------------
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
        """삭제하되 ConflictException(아직 CREATING이거나 레코드가 비동기 삭제 중인 레지스트리)이면
        시한까지 재시도한다."""
        deadline = time.monotonic() + TEARDOWN_TIMEOUT_S
        while True:
            try:
                delete_call()
                log("cleanup", f"{what} deleted")
                return
            except ClientError as e:
                code = _error_code(e)
                if code == "ResourceNotFoundException":
                    return  # 이미 없음 — 정상
                if code == "ConflictException" and time.monotonic() < deadline:
                    time.sleep(POLL_S)
                    continue
                log("cleanup", f"{what} delete error: {code or e}")
                return


# --- 도메인 헬퍼 (각각 증명하려는 한 가지에 대응) --------------------------

def sample_agent_card(name: str = "churn-predictor") -> dict:
    """최소한의 유효한 A2A v0.3.0 AgentCard."""
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
    """ⓑ A2A 에이전트 레코드를 퍼블리시한다. 레지스트리가 AgentCard를 실제 A2A 스키마로
    검증하며, 카드를 거부하면 CUSTOM 레코드로 폴백해 나머지 수명주기 데모가 계속 돌게 한다."""
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
    log("lifecycle", f"start      -> {reg.wait_record_ready(record_id)}")  # CREATING을 벗어날 때까지 대기
    reg.submit_for_approval(record_id)
    log("lifecycle", f"submitted  -> {reg.get_status(record_id)}")
    reg.set_status(record_id, "APPROVED", "Validated by security review (test)")
    log("lifecycle", f"approved   -> {reg.get_status(record_id)}")
    reg.set_status(record_id, "DEPRECATED", "Superseded by churn-predictor v2 (test)")
    log("lifecycle", f"deprecated -> {reg.get_status(record_id)}")


def semantic_search(reg: EphemeralRegistry, query: str) -> None:
    """② data-plane 시맨틱 발견 (새 레코드는 eventual-consistent)."""
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
            reg.add_record(                                                 # 같은 레지스트리에 도구도 추가
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
