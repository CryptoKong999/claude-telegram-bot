"""
Revenue tools for Claude Tool Use.
Allows logging payments and checking progress via natural language.
"""

import json
import logging

import revenue

logger = logging.getLogger(__name__)


REVENUE_TOOLS = [
    {
        "name": "revenue_log_payment",
        "description": (
            "Log a payment/revenue entry. Use when the user says something like "
            "'HONOR заплатили $3000', 'Pepsi оплата $2000', 'получили 5M сум от ЖК Башкент'. "
            "Extract the client name, amount in USD (convert from сум at 12000 сум/$1 if needed), "
            "and a brief description. ALWAYS confirm with the user before logging."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "amount_usd": {
                    "type": "number",
                    "description": "Amount in USD. Convert from сум if needed (12000 сум = $1).",
                },
                "client": {
                    "type": "string",
                    "description": "Client/company name.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of the payment.",
                    "default": "",
                },
            },
            "required": ["amount_usd", "client"],
        },
    },
    {
        "name": "revenue_get_progress",
        "description": (
            "Get current revenue progress and goal status. Use when user asks about "
            "money progress, goals, revenue, earnings, or income."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "revenue_get_recent",
        "description": "Get recent payment entries. Use when user asks about recent payments or transaction history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 10},
            },
            "required": [],
        },
    },
]


async def execute_tool(tool_name: str, tool_input: dict) -> str:
    try:
        if tool_name == "revenue_log_payment":
            entry = await revenue.log_revenue(
                amount_usd=tool_input["amount_usd"],
                client=tool_input["client"],
                description=tool_input.get("description", ""),
            )
            # Return progress after logging
            progress = await revenue.build_goals_message()
            return json.dumps({
                "success": True,
                "logged": {
                    "id": entry.id,
                    "amount_usd": entry.amount_usd,
                    "client": entry.client,
                    "date": str(entry.date),
                },
                "current_progress": progress,
            }, ensure_ascii=False, default=str)

        elif tool_name == "revenue_get_progress":
            text = await revenue.build_goals_message()
            return text

        elif tool_name == "revenue_get_recent":
            entries = await revenue.get_recent_entries(tool_input.get("limit", 10))
            result = [{
                "date": str(e.date),
                "client": e.client,
                "amount_usd": e.amount_usd,
                "description": e.description,
            } for e in entries]
            return json.dumps(result, ensure_ascii=False, default=str)

        else:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except Exception as e:
        logger.error(f"Revenue tool error: {e}", exc_info=True)
        return json.dumps({"error": str(e)})
