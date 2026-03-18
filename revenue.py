"""
Revenue & Goals tracker.
- Log payments via natural language
- Track progress toward personal goals  
- Generate motivational digest section
- Follow-up tracking
"""

import json
import logging
from datetime import datetime, date, timedelta

import asyncpg
import config

logger = logging.getLogger(__name__)

# ═══ Personal goals — the WHY ═══
GOALS_2026 = [
    {"name": "🎹 Пианино", "target": 1000, "priority": 1},
    {"name": "🛋 Диван", "target": 1000, "priority": 2},
    {"name": "📱 iPhone", "target": 2000, "priority": 3},
    {"name": "💻 MacBook M6", "target": 3500, "priority": 4},
    {"name": "🇨🇳 Китай", "target": 2000, "priority": 5},
]
MONTHLY_TARGET = 10000


# ═══ Tool definitions for Claude ═══
REVENUE_TOOLS = [
    {
        "name": "revenue_log_payment",
        "description": (
            "Log a payment/revenue received. Use when user says things like "
            "'HONOR заплатили $3000', 'получили оплату', 'пришли деньги'. "
            "Extract client name, amount, and description."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "client": {"type": "string", "description": "Client/company name"},
                "amount": {"type": "number", "description": "Amount in USD"},
                "description": {"type": "string", "description": "Brief description", "default": ""},
            },
            "required": ["client", "amount"],
        },
    },
    {
        "name": "revenue_stats",
        "description": (
            "Show revenue stats, goal progress, financial overview. "
            "Use when user asks about money, revenue, goals, progress, P&L."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "revenue_add_follow_up",
        "description": (
            "Add a follow-up reminder for a potential deal. "
            "Use when user mentions needing to follow up with someone about money."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact": {"type": "string", "description": "Contact name"},
                "handle": {"type": "string", "description": "Telegram @handle"},
                "opportunity": {"type": "string", "description": "What the deal is about"},
                "amount": {"type": "number", "description": "Potential amount in USD", "default": 0},
            },
            "required": ["contact", "opportunity"],
        },
    },
    {
        "name": "revenue_follow_ups",
        "description": "Show pending follow-ups that need action today.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "revenue_follow_up_action",
        "description": (
            "Update a follow-up: mark as done, skip, reject, or paid. "
            "done = contacted, will follow up in 3 days. "
            "skip = not now, push to next week. "
            "reject = not relevant, don't show again. "
            "paid = deal closed, log the payment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "follow_up_id": {"type": "integer"},
                "action": {"type": "string", "enum": ["done", "skip", "reject", "paid"]},
                "notes": {"type": "string", "default": ""},
            },
            "required": ["follow_up_id", "action"],
        },
    },
]


# ═══ DB helpers ═══

async def _conn():
    url = config.DATABASE_URL
    if not url:
        return None
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return await asyncpg.connect(url, timeout=10)


async def init_tables():
    conn = await _conn()
    if not conn:
        return
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS revenue_log (
                id SERIAL PRIMARY KEY,
                client TEXT NOT NULL,
                amount FLOAT NOT NULL,
                currency TEXT DEFAULT 'USD',
                description TEXT,
                date DATE DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS follow_up_actions (
                id SERIAL PRIMARY KEY,
                contact TEXT NOT NULL,
                contact_handle TEXT,
                opportunity TEXT,
                potential_amount FLOAT DEFAULT 0,
                last_interaction DATE,
                next_follow_up DATE,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                times_skipped INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        logger.info("Revenue tables ready")
    finally:
        await conn.close()


# ═══ Tool executors ═══

async def execute_tool(name: str, inp: dict) -> str:
    try:
        if name == "revenue_log_payment":
            return await _log_payment(inp)
        elif name == "revenue_stats":
            return await _get_stats()
        elif name == "revenue_add_follow_up":
            return await _add_follow_up(inp)
        elif name == "revenue_follow_ups":
            return await _get_follow_ups()
        elif name == "revenue_follow_up_action":
            return await _follow_up_action(inp)
        return json.dumps({"error": f"Unknown: {name}"})
    except Exception as e:
        logger.error(f"Revenue tool error: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


async def _log_payment(inp: dict) -> str:
    conn = await _conn()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    try:
        await conn.execute(
            "INSERT INTO revenue_log (client, amount, description) VALUES ($1, $2, $3)",
            inp["client"], inp["amount"], inp.get("description", "")
        )
        month_start = date.today().replace(day=1)
        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue_log WHERE date >= $1",
            month_start
        )
        monthly = float(row["total"])

        # Find next goal
        cumulative = 0
        year_start = date.today().replace(month=1, day=1)
        row2 = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue_log WHERE date >= $1",
            year_start
        )
        yearly = float(row2["total"])
        savings = yearly * 0.3

        next_goal = None
        for g in sorted(GOALS_2026, key=lambda x: x["priority"]):
            cumulative += g["target"]
            if savings < cumulative:
                next_goal = g
                remaining = cumulative - savings
                break

        result = {
            "logged": True,
            "client": inp["client"],
            "amount": inp["amount"],
            "monthly_total": monthly,
            "monthly_target": MONTHLY_TARGET,
            "yearly_total": yearly,
        }
        if next_goal:
            result["next_goal"] = next_goal["name"]
            result["goal_remaining"] = round(remaining)

        return json.dumps(result, ensure_ascii=False, default=str)
    finally:
        await conn.close()


async def _get_stats() -> str:
    conn = await _conn()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    try:
        today = date.today()
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue_log WHERE date >= $1", month_start
        )
        monthly = float(row["total"])

        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as total FROM revenue_log WHERE date >= $1", year_start
        )
        yearly = float(row["total"])

        recent = await conn.fetch(
            "SELECT client, amount, description, date FROM revenue_log ORDER BY date DESC LIMIT 5"
        )

        # Goals
        savings = yearly * 0.3
        cumulative = 0
        goals = []
        for g in sorted(GOALS_2026, key=lambda x: x["priority"]):
            cumulative += g["target"]
            goals.append({
                "name": g["name"],
                "target": g["target"],
                "achieved": savings >= cumulative,
            })

        # Pending follow-ups
        fus = await conn.fetch("""
            SELECT id, contact, contact_handle, opportunity, potential_amount
            FROM follow_up_actions 
            WHERE status = 'pending' AND next_follow_up <= CURRENT_DATE
            ORDER BY potential_amount DESC NULLS LAST LIMIT 5
        """)

        return json.dumps({
            "month": today.strftime("%B %Y"),
            "monthly_revenue": monthly,
            "monthly_target": MONTHLY_TARGET,
            "yearly_revenue": yearly,
            "savings_estimate": round(savings),
            "recent_payments": [dict(r) for r in recent],
            "goals": goals,
            "pending_follow_ups": len(fus),
        }, ensure_ascii=False, default=str)
    finally:
        await conn.close()


async def _add_follow_up(inp: dict) -> str:
    conn = await _conn()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    try:
        next_fu = date.today() + timedelta(days=3)
        await conn.execute(
            """INSERT INTO follow_up_actions 
               (contact, contact_handle, opportunity, potential_amount, last_interaction, next_follow_up)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            inp["contact"], inp.get("handle", ""), inp["opportunity"],
            inp.get("amount", 0), date.today(), next_fu
        )
        return json.dumps({"added": True, "next_follow_up": str(next_fu)}, default=str)
    finally:
        await conn.close()


async def _get_follow_ups() -> str:
    conn = await _conn()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    try:
        rows = await conn.fetch("""
            SELECT id, contact, contact_handle, opportunity, potential_amount,
                   last_interaction, next_follow_up, times_skipped
            FROM follow_up_actions 
            WHERE status = 'pending' AND next_follow_up <= CURRENT_DATE
            ORDER BY potential_amount DESC NULLS LAST LIMIT 5
        """)
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str)
    finally:
        await conn.close()


async def _follow_up_action(inp: dict) -> str:
    conn = await _conn()
    if not conn:
        return json.dumps({"error": "DB unavailable"})
    try:
        fid = inp["follow_up_id"]
        action = inp["action"]
        notes = inp.get("notes", "")

        if action == "done":
            await conn.execute(
                """UPDATE follow_up_actions SET last_interaction = CURRENT_DATE,
                   next_follow_up = CURRENT_DATE + 3, notes = COALESCE(notes||E'\n','')||$2
                   WHERE id = $1""", fid, notes
            )
        elif action == "skip":
            await conn.execute(
                """UPDATE follow_up_actions SET next_follow_up = CURRENT_DATE + 7,
                   times_skipped = times_skipped + 1 WHERE id = $1""", fid
            )
        elif action == "reject":
            await conn.execute(
                "UPDATE follow_up_actions SET status = 'rejected' WHERE id = $1", fid
            )
        elif action == "paid":
            await conn.execute(
                "UPDATE follow_up_actions SET status = 'paid' WHERE id = $1", fid
            )

        return json.dumps({"success": True, "action": action})
    finally:
        await conn.close()


# ═══ Digest section ═══

def _bar(current: float, target: float, w: int = 10) -> str:
    ratio = min(current / target, 1.0) if target > 0 else 0
    filled = int(ratio * w)
    return "█" * filled + "░" * (w - filled)


async def generate_digest_section() -> str:
    """Generate 💰 section for morning digest."""
    conn = await _conn()
    if not conn:
        return ""
    try:
        today = date.today()
        month_start = today.replace(day=1)
        year_start = today.replace(month=1, day=1)

        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as t FROM revenue_log WHERE date >= $1", month_start
        )
        monthly = float(row["t"])

        row = await conn.fetchrow(
            "SELECT COALESCE(SUM(amount), 0) as t FROM revenue_log WHERE date >= $1", year_start
        )
        yearly = float(row["t"])

        pct = int(monthly / MONTHLY_TARGET * 100) if MONTHLY_TARGET > 0 else 0
        bar = _bar(monthly, MONTHLY_TARGET)
        month_name = today.strftime("%B")

        lines = [
            f"💰 {month_name}: ${monthly:,.0f} / ${MONTHLY_TARGET:,.0f}",
            f"   {bar} {pct}%",
        ]

        # Goals progress
        savings = yearly * 0.3
        cumulative = 0
        for g in sorted(GOALS_2026, key=lambda x: x["priority"]):
            cumulative += g["target"]
            if savings >= cumulative:
                lines.append(f"   {g['name']} — ✅")
            else:
                lines.append(f"   {g['name']} — ...")
                break

        # Follow-ups
        fus = await conn.fetch("""
            SELECT contact_handle, opportunity, potential_amount
            FROM follow_up_actions 
            WHERE status = 'pending' AND next_follow_up <= CURRENT_DATE
            ORDER BY potential_amount DESC NULLS LAST LIMIT 3
        """)
        if fus:
            lines.append("")
            lines.append("   Ждут ответа:")
            for fu in fus:
                h = fu["contact_handle"] or "?"
                h = f"@{h}" if h and not h.startswith("@") else h
                opp = (fu["opportunity"] or "")[:25]
                amt = f" ${fu['potential_amount']:,.0f}" if fu["potential_amount"] else ""
                lines.append(f"   ▸ {h} ({opp}){amt}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Digest section error: {e}")
        return ""
    finally:
        await conn.close()
