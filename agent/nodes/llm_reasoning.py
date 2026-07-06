"""
agent/nodes/llm_reasoning.py — Node 5: LLM Reasoning

Constructs a structured prompt from enriched state and calls GPT-4o.
The model returns a JSON object with suspicion score, red flags, SAR narrative,
recommendation, and reasoning.

Failure handling:
    If the LLM response cannot be parsed as valid JSON, the raw output is
    preserved in llm_reasoning["raw_output"] and llm_parse_error is set to True.
    The decision router will route this case to human review.
"""

import json
import os
from datetime import datetime
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from agent.state import AMLState
from agent.prompts.reasoning_prompt import SYSTEM_PROMPT, build_reasoning_prompt

load_dotenv()

REQUIRED_FIELDS = {"suspicion_score", "red_flags", "sar_narrative", "recommendation", "reasoning"}
VALID_SCORES = {"low", "medium", "high"}
VALID_RECOMMENDATIONS = {"auto_clear", "monitor", "file_sar"}


def _get_llm() -> ChatOpenAI:
    model = os.getenv("LLM_MODEL", "gpt-4o")
    return ChatOpenAI(
        model=model,
        temperature=0,       # Deterministic — same input → same output
        response_format={"type": "json_object"},
    )


def _parse_response(content: str) -> tuple[dict, bool]:
    """
    Parse LLM response content into a structured dict.

    Returns:
        (parsed_dict, parse_error) — if parse_error is True, parsed_dict
        contains {"raw_output": content} and the case routes to human review.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"raw_output": content}, True

    # Validate required fields
    missing = REQUIRED_FIELDS - set(parsed.keys())
    if missing:
        parsed["raw_output"] = content
        parsed["parse_error_reason"] = f"Missing fields: {missing}"
        return parsed, True

    # Validate field values
    if parsed.get("suspicion_score") not in VALID_SCORES:
        parsed["raw_output"] = content
        parsed["parse_error_reason"] = f"Invalid suspicion_score: {parsed.get('suspicion_score')}"
        return parsed, True

    if parsed.get("recommendation") not in VALID_RECOMMENDATIONS:
        parsed["raw_output"] = content
        parsed["parse_error_reason"] = f"Invalid recommendation: {parsed.get('recommendation')}"
        return parsed, True

    # Ensure red_flags is a list
    if not isinstance(parsed.get("red_flags"), list):
        parsed["red_flags"] = [str(parsed.get("red_flags", ""))]

    return parsed, False


def llm_reasoning(state: AMLState) -> dict:
    prompt = build_reasoning_prompt(
        alert=state["alert"],
        transaction_history=state.get("transaction_history", []),
        velocity_metrics=state.get("velocity_metrics", {}),
        cdd_profile=state.get("cdd_profile", {}),
        edd_required=state.get("edd_required", False),
        watchlist_result=state.get("watchlist_result", {}),
        watchlist_failed=state.get("watchlist_failed", False),
    )

    llm = _get_llm()
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    content = response.content

    reasoning, parse_error = _parse_response(content)

    if parse_error:
        summary = (
            f"LLM response could not be parsed. "
            f"Reason: {reasoning.get('parse_error_reason', 'JSON decode error')}. "
            f"Routing to human review."
        )
    else:
        summary = (
            f"LLM reasoning complete. "
            f"Score: {reasoning.get('suspicion_score')}. "
            f"Recommendation: {reasoning.get('recommendation')}. "
            f"Red flags: {len(reasoning.get('red_flags', []))} identified."
        )

    entry = {
        "node": "llm_reasoning",
        "timestamp": datetime.now().isoformat(),
        "summary": summary,
    }

    return {
        "llm_reasoning": reasoning,
        "llm_parse_error": parse_error,
        "audit_trail": [entry],
    }
