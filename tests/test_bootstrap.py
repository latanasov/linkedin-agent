import asyncio

from linkedin_agent.bootstrap import build_app
from linkedin_agent.config import Settings


async def test_close_does_not_wait_forever_for_the_browser(tmp_path, monkeypatch):
    """A process on its way out must get out: the pool's shutdown goes through the
    event bus that hangs, and the exit sat behind it with no heartbeat, seen live."""
    import linkedin_agent.bootstrap as bs

    app = await build_app(Settings(home=tmp_path, openrouter_api_key="k"), need_llm=False)

    async def never():
        await asyncio.sleep(3600)

    monkeypatch.setattr(app.deps.pool, "shutdown", never)
    monkeypatch.setattr(bs, "with_deadline", _quick_deadline)
    await asyncio.wait_for(app.close(), 5)


async def _quick_deadline(coro, seconds, what):
    from linkedin_agent.core.prompts import with_deadline

    return await with_deadline(coro, 0.05, what)
