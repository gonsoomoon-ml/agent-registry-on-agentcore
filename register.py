#!/usr/bin/env python3
"""(2) 에이전트와 도구를 레지스트리에 등록한다.

에이전트를 컨테이너 없는 관리형 **AgentCore Harness**(model + system prompt + tool)로 만들고,
계속 유지되는 **Agent Registry**와 레코드 2개를 생성한다. 에이전트 레코드가 harnessArn을 담아,
나중에 client가 그 런타임을 찾아 호출할 수 있게 한다. 생성한 ID는 STATE_FILE에 기록해 client.py가 읽는다.

    python3 register.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import boto3
from botocore.exceptions import ClientError

import agents_and_tools as at

STATE_FILE = ".agentcore_state.json"
HARNESS_ROLE_NAME = "AgentCoreHarnessRole"   # us-east-1로 스코프된 기존 역할 재사용
READY = {"READY", "ACTIVE", "AVAILABLE"}
WAIT_TIMEOUT_S = 300
POLL_S = 5


def log(step: str, msg: str = "") -> None:
    print(f"[{step:<12}] {msg}")


def _wait(get_status, *, what: str) -> str:
    """status가 READY가 될 때까지 폴링한다. FAIL이면 즉시 예외, 시간 초과면 TimeoutError."""
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


def create_harness(ctl, iam) -> tuple[str, str]:
    """에이전트를 AgentCore Harness로 생성한다. (harnessId, harnessArn)을 돌려준다."""
    role_arn = iam.get_role(RoleName=HARNESS_ROLE_NAME)["Role"]["Arn"]
    # harnessName은 [a-zA-Z][a-zA-Z0-9_]{0,39} 패턴 — 하이픈 불가
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


def create_registry(ctl) -> str:
    """계속 유지되는 레지스트리를 생성하고 READY까지 기다린 뒤 registryId를 돌려준다."""
    arn = ctl.create_registry(
        name=f"agent-catalog-{uuid.uuid4().hex[:8]}",
        description="Agent + tool catalog (churn demo)",
        authorizerType="AWS_IAM",
        # autoApproval=True여도 라이브에서 레코드는 DRAFT로 남는다(관측됨). client는
        # 상태와 무관하게 GetRegistryRecord로 읽으므로 동작에는 영향이 없다.
        approvalConfiguration={"autoApproval": True},
    )["registryArn"]
    registry_id = arn.split(":registry/")[1].split("/")[0]
    _wait(lambda: ctl.get_registry(registryId=registry_id)["status"], what="registry")
    log("registry", f"ready {registry_id}")
    return registry_id


def add_record(ctl, registry_id: str, name: str, payload: dict) -> str:
    """CUSTOM 레코드를 만들고, 응답 ARN에서 recordId를 파싱해 돌려준다.

    (list/search는 eventual-consistent라 방금 만든 레코드를 못 찾을 수 있으므로
    ID는 항상 생성 응답의 recordArn에서 바로 뽑는다.)
    """
    arn = ctl.create_registry_record(
        registryId=registry_id, name=name, descriptorType="CUSTOM", recordVersion="1.0.0",
        descriptors={"custom": {"inlineContent": json.dumps(payload)}},
    )["recordArn"]
    return arn.split("/record/")[-1]


def main() -> int:
    # import 시 부수효과가 없도록 클라이언트는 여기서 생성한다.
    ctl = boto3.client("bedrock-agentcore-control", region_name=at.REGION)
    iam = boto3.client("iam")

    # 재실행 가드: state 파일이 이미 있으면 기존 리소스가 고아가 되므로 막는다.
    if os.path.exists(STATE_FILE):
        log("abort", f"{STATE_FILE} 이미 존재 — 기존 리소스가 고아가 된다. "
                     f"먼저 `python3 client.py --cleanup` 후 다시 실행할 것.")
        return 1

    created: dict = {}   # 부분 실패 시 정리할 수 있도록 만든 리소스를 추적한다.
    try:
        harness_id, harness_arn = create_harness(ctl, iam)
        created["harnessId"] = harness_id
        registry_id = create_registry(ctl)
        created["registryId"] = registry_id

        agent_record = add_record(ctl, registry_id, f"{at.AGENT_NAME}-agent", {
            "kind": "agentcore-harness",
            "agentName": at.AGENT_NAME,
            "harnessArn": harness_arn,          # <- client가 런타임을 찾는 경로
            "skills": at.AGENT_SKILLS,
        })
        created["agentRecordId"] = agent_record
        tool_record = add_record(ctl, registry_id, at.CUSTOMER_DB_SEARCH_TOOL["name"], {
            "kind": "tool",
            **at.CUSTOMER_DB_SEARCH_TOOL,
        })
        created["toolRecordId"] = tool_record
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
        log("done", f"state -> {STATE_FILE}  (이제 실행: python3 client.py)")
        return 0
    except (ClientError, RuntimeError, TimeoutError) as e:
        # ClientError뿐 아니라 _wait의 RuntimeError/TimeoutError까지 깔끔히 잡는다.
        msg = e.response["Error"]["Message"][:200] if isinstance(e, ClientError) else str(e)
        log("error", msg)
        if created:
            # state 파일이 안 써졌을 수 있으니, 고아 리소스 ID를 직접 출력해 수동 정리를 돕는다.
            log("orphaned", f"수동 정리 필요: {created}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
