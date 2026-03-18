"""
Revenue tracker — logs payments, tracks goals, generates progress bars.
No AI guessing. Only real data you enter.

Usage in Telegram:
  "HONOR заплатили $3000"
  "ЖК Башкент наличка 5M сум"
  "/money" — текущий прогресс
  "/goals" — цели и прогресс-бары
"""

import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, Float, Date, DateTime, Boolean,
    select, func, extract
)
from sqlalchemy.ext.asyncio import AsyncSession

import database as db

logger = logging.getLogger(__name__)


# ──────────────────── DB Models ───────────────────────

class Revenue(db.Base):
    __tablename__ = "revenue_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    amount_usd = Column(Float, nullable=False)
    client = Column(String(200), nullable=False)
    description = Column(Text, default="")
    date = Column(Date, default=date.today)
    created_at = Column(DateTime, default=datetime.utcnow)


class PersonalGoal(db.Base):
    __tablename__ = "personal_goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    emoji = Column(String(10), default="🎯")
    target_usd = Column(Float, nullable=False)
    priority = Column(Integer, default=1)  # 1 = first to fill
    is_achieved = Column(Boolean, default=False)


# ──────────────────── Constants ───────────────────────

# Robert's 2026 goals — seeded on first run
DEFAULT_GOALS = [
    {"name": "MacBook Pro M6", "emoji": "💻", "target_usd": 3500, "priority": 1},
    {"name": "iPhone", "emoji": "📱", "target_usd": 2000, "priority": 2},
    {"name": "Пианино", "emoji": "🎹", "target_usd": 1000, "priority": 3},
    {"name": "Диван", "emoji": "🛋", "target_usd": 1000, "priority": 4},
]

SUM_RATE = 12000  # 12,000 сум = $1
MONTHLY_EXPENSES = 1500  # $1,500/мес


# ──────────────────── Init ────────────────────────────

async def init_revenue_tables():
    """Create tables and seed goals if needed."""
    async with db.engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)

    # Seed goals if empty
    async with db.SessionLocal() as session:
        result = await session.execute(select(func.count(PersonalGoal.id)))
        count = result.scalar()
        if count == 0:
            for g in DEFAULT_GOALS:
                session.add(PersonalGoal(**g))
            await session.commit()
            logger.info("Seeded personal goals")


# ──────────────────── Revenue CRUD ────────────────────

async def log_revenue(amount_usd: float, client: str, description: str = "") -> Revenue:
    """Log a payment."""
    async with db.SessionLocal() as session:
        entry = Revenue(
            amount_usd=amount_usd,
            client=client,
            description=description,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


async def get_monthly_revenue(year: int = None, month: int = None) -> float:
    """Get total revenue for a month."""
    if not year:
        year = date.today().year
    if not month:
        month = date.today().month

    async with db.SessionLocal() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(Revenue.amount_usd), 0))
            .where(extract("year", Revenue.date) == year)
            .where(extract("month", Revenue.date) == month)
        )
        return result.scalar() or 0.0


async def get_year_revenue(year: int = None) -> float:
    """Get total revenue for a year."""
    if not year:
        year = date.today().year

    async with db.SessionLocal() as session:
        result = await session.execute(
            select(func.coalesce(func.sum(Revenue.amount_usd), 0))
            .where(extract("year", Revenue.date) == year)
        )
        return result.scalar() or 0.0


async def get_recent_entries(limit: int = 10) -> list[Revenue]:
    """Get recent revenue entries."""
    async with db.SessionLocal() as session:
        result = await session.execute(
            select(Revenue)
            .order_by(Revenue.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())


# ──────────────────── Goals ───────────────────────────

async def get_goals() -> list[PersonalGoal]:
    """Get all active goals sorted by priority."""
    async with db.SessionLocal() as session:
        result = await session.execute(
            select(PersonalGoal)
            .where(PersonalGoal.is_achieved == False)
            .order_by(PersonalGoal.priority)
        )
        return list(result.scalars())


# ──────────────────── Progress Bars ───────────────────

def progress_bar(current: float, target: float, width: int = 12) -> str:
    """Generate a visual progress bar."""
    if target <= 0:
        return "?" * width
    ratio = min(current / target, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    pct = int(ratio * 100)
    return f"{bar} {pct}%"


async def build_goals_message() -> str:
    """Build the motivational goals display."""
    year_revenue = await get_year_revenue()
    month_revenue = await get_monthly_revenue()
    goals = await get_goals()

    today = date.today()
    months_left = 12 - today.month + 1
    year_expenses = MONTHLY_EXPENSES * (12 - today.month + 1)

    # Available for goals = revenue - expenses
    # For simplicity: track revenue directly toward goals
    # Goals are filled in priority order
    remaining = year_revenue
    lines = []

    for g in goals:
        if remaining >= g.target_usd:
            filled = g.target_usd
            remaining -= g.target_usd
        else:
            filled = max(remaining, 0)
            remaining = 0

        bar = progress_bar(filled, g.target_usd)
        lines.append(f"{g.emoji} {g.name}: {bar} (${filled:,.0f}/${g.target_usd:,.0f})")

    # Monthly pace
    if today.month > 1:
        avg_monthly = year_revenue / today.month
    else:
        avg_monthly = month_revenue

    pace_yearly = avg_monthly * 12
    target_yearly = sum(g.target_usd for g in goals) + (MONTHLY_EXPENSES * 12)

    text = f"📊 Выручка {today.year}\n\n"
    text += f"Этот месяц: ${month_revenue:,.0f}\n"
    text += f"За год: ${year_revenue:,.0f}\n"
    text += f"Темп: ${avg_monthly:,.0f}/мес\n\n"
    text += "🎯 Цели:\n"
    text += "\n".join(lines)

    if year_revenue == 0:
        text += "\n\n→ Залогай первую оплату: напиши 'HONOR $3000' или 'Pepsi $2000'"
    elif avg_monthly < 5000:
        needed = (target_yearly - year_revenue) / max(months_left, 1)
        text += f"\n\nДо цели нужно ${needed:,.0f}/мес"

    return text


async def build_digest_section() -> str:
    """
    Compact revenue section for the morning digest.
    One line with progress + who needs follow-up.
    """
    month_revenue = await get_monthly_revenue()
    goals = await get_goals()
    next_goal = goals[0] if goals else None
    year_revenue = await get_year_revenue()

    # Build mini progress toward next goal
    if next_goal:
        bar = progress_bar(min(year_revenue, next_goal.target_usd), next_goal.target_usd, width=8)
        goal_text = f"{next_goal.emoji} {next_goal.name}: {bar}"
    else:
        goal_text = "Все цели достигнуты! 🎉"

    text = f"💰 Март: ${month_revenue:,.0f} | {goal_text}"
    return text
