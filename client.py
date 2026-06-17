#!/usr/bin/env python3
"""(3) Discover the agent + tool from the registry, then call the agent for a result.

Reads STATE_FILE for the registry/record ids, RETRIEVES the agent's descriptor
from the registry (-> harnessArn) and the tool's descriptor, then runs the
InvokeHarness agent loop: the managed Harness (Sonnet 4.6) reasons and emits
tool_use; this client executes the tool locally and feeds the result back, until
the agent returns its final churn assessment.

    python3 client.py                 # discover + run (default customer C-1001)
    python3 client.py C-2002          # a specific customer
    python3 client.py --cleanup       # delete harness + registry + records
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
    """Retrieve a record's descriptor payload FROM the registry (strong-consistent)."""
    rec = ctl.get_registry_record(registryId=registry_id, recordId=record_id)
    return json.loads(rec["descriptors"]["custom"]["inlineContent"])


# --- the agent loop over the InvokeHarness Converse-style event stream ---------

def parse_stream(stream) -> tuple[list, list, str | None]:
    """Collapse the event stream into (assistant_content, tool_uses, stop_reason)."""
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
            args = json.loads(b["input"] or "{}")
            content.append({"toolUse": {"name": b["name"], "toolUseId": b["id"],
                                        "input": args, "type": "tool_use"}})
            tool_uses.append({"name": b["name"], "toolUseId": b["id"], "input": args})
    return content, tool_uses, stop


def run_agent(dp, harness_arn: str, user_text: str) -> str:
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
                out = at.dispatch_tool(tu["name"], tu["input"])           # <- client executes the tool
                log("tool", f"{tu['name']}({tu['input']}) -> {out}")
                results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                               "content": [{"text": json.dumps(out)}], "status": "success"}})
            messages.append({"role": "user", "content": results})
            continue
        return "".join(c.get("text", "") for c in content)
    return "(max turns reached without a final answer)"


def cleanup() -> int:
    state = load_state()
    ctl, _ = clients(state["region"])
    for key in ("agentRecordId", "toolRecordId"):
        try:
            ctl.delete_registry_record(registryId=state["registryId"], recordId=state[key])
            log("cleanup", f"record {state[key]} deleted")
        except ClientError as e:
            log("cleanup", f"record {state[key]}: {e.response['Error']['Code']}")
    deadline = time.monotonic() + 120
    while True:
        try:
            ctl.delete_registry(registryId=state["registryId"])
            log("cleanup", "registry deleted")
            break
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException" and time.monotonic() < deadline:
                time.sleep(5)
                continue
            log("cleanup", f"registry: {e.response['Error']['Code']}")
            break
    try:
        ctl.delete_harness(harnessId=state["harnessId"])
        log("cleanup", "harness deleted")
    except ClientError as e:
        log("cleanup", f"harness: {e.response['Error']['Code']}")
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
        log("error", f"{STATE_FILE} not found — run `python3 register.py` first")
        return 1
    ctl, dp = clients(state["region"])
    try:
        agent = retrieve(ctl, state["registryId"], state["agentRecordId"])     # (3) discover agent
        tool = retrieve(ctl, state["registryId"], state["toolRecordId"])       #     discover tool
        log("discover", f"agent={agent['agentName']}  tool={tool['name']}  harness=...{agent['harnessArn'][-20:]}")
        answer = run_agent(dp, agent["harnessArn"], f"Assess churn risk for customer {customer}.")  # (3) call
        print("\n=== RESULT ===")
        print(answer)
        return 0
    except ClientError as e:
        log("error", f"{e.response['Error']['Code']}: {e.response['Error']['Message'][:200]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
