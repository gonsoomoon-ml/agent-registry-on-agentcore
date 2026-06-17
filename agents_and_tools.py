#!/usr/bin/env python3
"""(1) 도구와 에이전트를 정의한다.

- ``customer_db_search``    : 도구 구현 — mockup 데이터를 돌려주며(in-memory DB 없음) 클라이언트가 실행한다.
- ``CUSTOMER_DB_SEARCH_TOOL``: Harness/레지스트리가 보는 도구 정의
                              (``inline_function`` = 에이전트에 선언, 클라이언트가 실행).
- ``SYSTEM_PROMPT``         : 에이전트의 "두뇌" — churn 루브릭. 자유롭게 수정.
- ``HARNESS_TOOLS`` / ``MODEL_ID`` / ``REGION``: register.py가 쓰는 에이전트 런타임 설정.
- ``dispatch_tool``         : Harness가 호출한 도구 이름 -> 로컬 구현 매핑.
"""
from __future__ import annotations

REGION = "us-east-1"                       # AgentCoreHarnessRole이 us-east-1로 스코프됨
# Sonnet 4.6은 Bedrock에서 cross-region inference profile이 필요하다
# (bare foundation-model id는 on-demand 호출 불가).
MODEL_ID = "us.anthropic.claude-sonnet-4-6"

AGENT_NAME = "churn-predictor"
AGENT_SKILLS = ["predict_churn"]

# --- (1a) 도구: in-memory "DB" 없이 mockup 데이터를 그대로 반환 ----------------

_MOCKUP = {
    "C-1001": {"orders_last_90d": 1, "avg_review_score": 2.3, "late_shipments": 2, "days_since_last_order": 74},
    "C-2002": {"orders_last_90d": 9, "avg_review_score": 4.7, "late_shipments": 0, "days_since_last_order": 5},
    "C-3003": {"orders_last_90d": 3, "avg_review_score": 3.5, "late_shipments": 1, "days_since_last_order": 28},
}


def customer_db_search(customer_id: str) -> dict:
    """고객의 최근 이력을 돌려준다(mockup). 미등록 id는 중립 기본값을 받는다."""
    history = _MOCKUP.get(customer_id, {
        "orders_last_90d": 4, "avg_review_score": 3.8, "late_shipments": 0, "days_since_last_order": 20})
    return {"customer_id": customer_id, **history}


# Harness에 선언되어(그리고 레지스트리에 저장되어) 보이는 도구 정의.
# type=inline_function => 모델이 tool_use를 내보내면 클라이언트가 실행한다.
CUSTOMER_DB_SEARCH_TOOL = {
    "type": "inline_function",
    "name": "customer_db_search",
    "config": {"inlineFunction": {
        "description": "Look up a customer's recent order/review/shipping history by customer_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string", "description": "e.g. C-1001"}},
            "required": ["customer_id"],
        },
    }},
}

HARNESS_TOOLS = [CUSTOMER_DB_SEARCH_TOOL]

# 도구 이름 -> 로컬 구현. Harness가 호출한 도구를 클라이언트가 이 매핑으로 실행한다.
TOOL_IMPLS = {
    "customer_db_search": lambda args: customer_db_search(args["customer_id"]),
}


def dispatch_tool(name: str, args: dict) -> dict:
    impl = TOOL_IMPLS.get(name)
    if impl is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return impl(args)
    except (KeyError, TypeError) as e:
        # 모델이 잘못된 도구 입력을 내보낸 경우 — 클라이언트를 크래시시키는 대신
        # 에이전트가 보고 복구할 수 있는 error를 돌려준다.
        return {"error": f"bad arguments for {name}: {e}"}


# --- (1b) 에이전트의 두뇌: churn 루브릭 — 동작을 바꾸려면 이 프롬프트를 수정 ------
# (주의: SYSTEM_PROMPT는 모델에 전송되는 실제 지시문이라 영어로 둔다 — 번역 시 동작이 달라질 수 있음)

SYSTEM_PROMPT = """You are a customer-churn risk analyst.
Given a customer_id, call the customer_db_search tool to fetch the customer's recent
history, then assess churn risk using this rubric:
- days_since_last_order > 60  -> strong churn signal
- avg_review_score < 3.0      -> dissatisfaction signal
- late_shipments >= 2         -> service-failure signal
- orders_last_90d <= 1        -> disengagement signal
Weigh the signals, then end your answer with a single JSON object summarizing the verdict:
{"customer_id": <id>, "churn_risk": "high"|"medium"|"low", "score": <0.0-1.0>, "reasons": [<short strings>]}
"""
