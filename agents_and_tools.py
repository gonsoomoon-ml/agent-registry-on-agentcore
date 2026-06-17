#!/usr/bin/env python3
"""(1) Define the TOOL and the AGENT.

- ``customer_db_search``   : the tool's implementation — returns *mockup* data
                             (no in-memory DB), executed client-side.
- ``CUSTOMER_DB_SEARCH_TOOL``: the tool as the Harness/registry sees it
                             (``inline_function`` = declared to the agent,
                             executed by the client).
- ``SYSTEM_PROMPT``        : the agent's "brain" — the churn rubric. Edit freely.
- ``HARNESS_TOOLS`` / ``MODEL_ID`` / ``REGION``: agent-runtime config for register.py.
- ``dispatch_tool``        : maps a tool name the Harness calls -> local impl.
"""
from __future__ import annotations

REGION = "us-east-1"                       # AgentCoreHarnessRole is scoped to us-east-1
# Sonnet 4.6 on Bedrock requires a cross-region inference profile (the bare
# foundation-model id is not invocable on-demand).
MODEL_ID = "us.anthropic.claude-sonnet-4-6"

AGENT_NAME = "churn-predictor"
AGENT_SKILLS = ["predict_churn"]

# --- (1a) the TOOL: mockup data returned directly (no in-memory "DB") ---------

_MOCKUP = {
    "C-1001": {"orders_last_90d": 1, "avg_review_score": 2.3, "late_shipments": 2, "days_since_last_order": 74},
    "C-2002": {"orders_last_90d": 9, "avg_review_score": 4.7, "late_shipments": 0, "days_since_last_order": 5},
    "C-3003": {"orders_last_90d": 3, "avg_review_score": 3.5, "late_shipments": 1, "days_since_last_order": 28},
}


def customer_db_search(customer_id: str) -> dict:
    """Return a customer's recent history (mockup). Unknown ids get a neutral default."""
    history = _MOCKUP.get(customer_id, {
        "orders_last_90d": 4, "avg_review_score": 3.8, "late_shipments": 0, "days_since_last_order": 20})
    return {"customer_id": customer_id, **history}


# the tool as declared to the Harness (and stored in the registry).
# type=inline_function => the model emits a tool_use, the CLIENT executes it.
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

# name -> local implementation, so the client can run whatever the Harness calls
TOOL_IMPLS = {
    "customer_db_search": lambda args: customer_db_search(args["customer_id"]),
}


def dispatch_tool(name: str, args: dict) -> dict:
    impl = TOOL_IMPLS.get(name)
    return impl(args) if impl else {"error": f"unknown tool: {name}"}


# --- (1b) the AGENT's brain: churn rubric — EDIT THIS to change behavior -------

SYSTEM_PROMPT = """You are a customer-churn risk analyst.
Given a customer_id, call the customer_db_search tool to fetch the customer's recent
history, then assess churn risk using this rubric:
- days_since_last_order > 60  -> strong churn signal
- avg_review_score < 3.0      -> dissatisfaction signal
- late_shipments >= 2         -> service-failure signal
- orders_last_90d <= 1        -> disengagement signal
Weigh the signals and respond with ONLY a compact JSON object, no prose:
{"customer_id": <id>, "churn_risk": "high"|"medium"|"low", "score": <0.0-1.0>, "reasons": [<short strings>]}
"""
