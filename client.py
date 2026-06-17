#!/usr/bin/env python3
"""(3) 레지스트리에서 에이전트+도구를 찾아, 에이전트를 호출해 결과를 받는다.

STATE_FILE에서 registry/record id를 읽고, 레지스트리에서 에이전트 디스크립터(-> harnessArn)와
도구 디스크립터를 가져온 뒤, InvokeHarness 에이전트 루프를 돈다: 관리형 Harness(Sonnet 4.6)가
추론하며 tool_use를 내보내면, 이 client가 도구를 로컬로 실행해 결과를 돌려주고, 에이전트가
최종 이탈 판단을 낼 때까지 반복한다.

    python3 client.py                 # 조회 + 실행 (기본 고객 C-1001)
    python3 client.py C-2002          # 특정 고객
    python3 client.py --cleanup       # harness + registry + records 삭제
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
MAX_TURNS = 6


def log(step: str, msg: str = "") -> None:
    print(f"[{step:<12}] {msg}")


def load_state() -> dict:
    with open(STATE_FILE) as fh:
        return json.load(fh)


def clients(region: str):
    return (boto3.client("bedrock-agentcore-control", region_name=region),
            boto3.client("bedrock-agentcore", region_name=region))


def retrieve(ctl, registry_id: str, record_id: str) -> dict:
    """레지스트리에서 레코드의 디스크립터 payload를 가져온다(강한 일관성)."""
    rec = ctl.get_registry_record(registryId=registry_id, recordId=record_id)
    return json.loads(rec["descriptors"]["custom"]["inlineContent"])


# --- InvokeHarness Converse-style 이벤트 스트림 위에서 도는 에이전트 루프 ---------

def parse_stream(stream) -> tuple[list, list, str | None]:
    """이벤트 스트림을 (assistant_content, tool_uses, stop_reason)으로 접는다."""
    blocks: dict[int, dict] = {}
    order: list[int] = []
    stop = None
    for ev in stream:
        if "contentBlockStart" in ev:
            cb = ev["contentBlockStart"]
            i = cb["contentBlockIndex"]
            tu = cb.get("start", {}).get("toolUse")
            blocks[i] = ({"kind": "toolUse", "id": tu["toolUseId"], "name": tu["name"], "input": ""}
                         if tu else {"kind": "text", "text": ""})
            order.append(i)
        elif "contentBlockDelta" in ev:
            cb = ev["contentBlockDelta"]
            i = cb["contentBlockIndex"]
            delta = cb["delta"]
            b = blocks.setdefault(i, {"kind": "text", "text": ""})
            if i not in order:
                order.append(i)
            if delta.get("text"):
                b["text"] = b.get("text", "") + delta["text"]
            if delta.get("toolUse", {}).get("input"):
                b["input"] = b.get("input", "") + delta["toolUse"]["input"]
        elif "messageStop" in ev:
            stop = ev["messageStop"]["stopReason"]
        elif "validationException" in ev:
            raise RuntimeError(f"validation: {ev['validationException']}")
        elif "internalServerException" in ev or "runtimeClientError" in ev:
            raise RuntimeError(str(ev))

    content, tool_uses = [], []
    for i in order:
        b = blocks[i]
        if b["kind"] == "text" and b.get("text"):
            content.append({"text": b["text"]})
        elif b["kind"] == "toolUse":
            try:
                args = json.loads(b["input"] or "{}")
            except json.JSONDecodeError:
                # 모델이 깨진/불완전한 JSON을 스트리밍한 경우 — 빈 입력으로 넘겨
                # 도구가 error를 돌려주게 하고, 루프가 복구하도록 둔다(크래시 대신).
                args = {}
            content.append({"toolUse": {"name": b["name"], "toolUseId": b["id"],
                                        "input": args, "type": "tool_use"}})
            tool_uses.append({"name": b["name"], "toolUseId": b["id"], "input": args})
    return content, tool_uses, stop


def run_agent(dp, harness_arn: str, user_text: str) -> str:
    """Harness를 호출하고, tool_use가 나오면 도구를 로컬 실행해 결과를 회신하는 루프."""
    session = f"churn-{uuid.uuid4().hex}"
    messages = [{"role": "user", "content": [{"text": user_text}]}]
    for _ in range(MAX_TURNS):
        resp = dp.invoke_harness(harnessArn=harness_arn, runtimeSessionId=session, messages=messages)
        content, tool_uses, stop = parse_stream(resp["stream"])
        if content:
            messages.append({"role": "assistant", "content": content})
        if stop == "tool_use" and tool_uses:
            results = []
            for tu in tool_uses:
                out = at.dispatch_tool(tu["name"], tu["input"])           # client가 도구를 로컬 실행
                log("tool", f"{tu['name']}({tu['input']}) -> {out}")
                results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                               "content": [{"text": json.dumps(out)}], "status": "success"}})
            messages.append({"role": "user", "content": results})
            continue
        return "".join(c.get("text", "") for c in content)
    return "(max turns reached without a final answer)"


def _delete_with_retry(call, what: str, timeout: int = 120) -> None:
    """삭제를 시도하되 ConflictException(아직 CREATING이거나 비동기 삭제 중)이면 시한까지 재시도한다."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            call()
            log("cleanup", f"{what} deleted")
            return
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                return  # 이미 없음 — 정상
            if code == "ConflictException" and time.monotonic() < deadline:
                time.sleep(5)
                continue
            log("cleanup", f"{what}: {code}")
            return


def cleanup() -> int:
    try:
        state = load_state()
    except FileNotFoundError:
        log("cleanup", f"{STATE_FILE} 없음 — 정리할 리소스가 없다.")
        return 0
    ctl, _ = clients(state["region"])
    # 레코드 먼저, 그다음 레지스트리, 마지막으로 harness. 모두 충돌 시 재시도한다.
    for key in ("agentRecordId", "toolRecordId"):
        rid = state.get(key)
        if rid:
            _delete_with_retry(
                lambda rid=rid: ctl.delete_registry_record(registryId=state["registryId"], recordId=rid),
                f"record {rid}")
    if state.get("registryId"):
        _delete_with_retry(
            lambda: ctl.delete_registry(registryId=state["registryId"]),
            f"registry {state['registryId']}")
    if state.get("harnessId"):
        _delete_with_retry(
            lambda: ctl.delete_harness(harnessId=state["harnessId"]),
            f"harness {state['harnessId']}")
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass
    return 0


def main() -> int:
    if "--cleanup" in sys.argv:
        return cleanup()
    customer = next((a for a in sys.argv[1:] if not a.startswith("-")), "C-1001")
    try:
        state = load_state()
    except FileNotFoundError:
        log("error", f"{STATE_FILE} 없음 — 먼저 `python3 register.py`를 실행할 것")
        return 1
    ctl, dp = clients(state["region"])
    try:
        agent = retrieve(ctl, state["registryId"], state["agentRecordId"])     # (3) 에이전트 조회
        tool = retrieve(ctl, state["registryId"], state["toolRecordId"])       #     도구 조회
        log("discover", f"agent={agent['agentName']}  tool={tool['name']}  harness=...{agent['harnessArn'][-20:]}")
        answer = run_agent(dp, agent["harnessArn"], f"Assess churn risk for customer {customer}.")  # (3) 호출
        print("\n=== RESULT ===")
        print(answer)
        return 0
    except ClientError as e:
        log("error", f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
