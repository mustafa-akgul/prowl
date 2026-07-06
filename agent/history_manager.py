"""History Manager — persists review results across sessions.
Now uses SQLAlchemy Async DB tracking.
"""

import asyncio

import nest_asyncio

from agent.db import AsyncSessionLocal, ReviewRecord, init_db

nest_asyncio.apply()

_db_initialized = False


async def _ensure_db():
    global _db_initialized
    if not _db_initialized:
        await init_db()
        _db_initialized = True


def _run_sync(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # with nest_asyncio, we can use run_until_complete inside running loop indirectly or directly
            return asyncio.run(coro)
        else:
            return loop.run_until_complete(coro)
    except Exception:
        return asyncio.run(coro)


def save_review(pr_number: int, mode: str, review: str, usage_stats: dict | None = None) -> dict:
    stats = usage_stats or {"prompt_tokens": 0, "completion_tokens": 0}

    async def _save():
        await _ensure_db()
        async with AsyncSessionLocal() as session:
            record = ReviewRecord(
                pr_number=pr_number,
                mode=mode,
                review=review,
                prompt_tokens=stats.get("prompt_tokens", 0),
                completion_tokens=stats.get("completion_tokens", 0),
            )
            session.add(record)
            await session.commit()
            return {
                "pr_number": pr_number,
                "mode": mode,
                "review": review,
                "timestamp": record.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "usage_stats": stats,
            }

    return _run_sync(_save())


def load_history(limit: int = 50) -> list[dict]:
    from sqlalchemy import select

    async def _load():
        await _ensure_db()
        async with AsyncSessionLocal() as session:
            stmt = select(ReviewRecord).order_by(ReviewRecord.timestamp.desc()).limit(limit)
            result = await session.execute(stmt)
            records = result.scalars().all()
            return [
                {
                    "pr_number": r.pr_number,
                    "mode": r.mode,
                    "review": r.review,
                    "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "usage_stats": {
                        "prompt_tokens": r.prompt_tokens or 0,
                        "completion_tokens": r.completion_tokens or 0,
                    },
                }
                for r in records
            ]

    return _run_sync(_load())
