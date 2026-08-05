from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from telegram.ext import ApplicationHandlerStop


@pytest.mark.asyncio
async def test_access_guard_allows_configured_owner():
    import bot

    original_cfg = bot.cfg
    bot.cfg = SimpleNamespace(allowed_users=[123])
    update = MagicMock()
    update.effective_user.id = 123
    try:
        await bot.enforce_access(update, MagicMock())
    finally:
        bot.cfg = original_cfg


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [456, None])
async def test_access_guard_silently_stops_every_unauthorized_update(user_id):
    import bot

    original_cfg = bot.cfg
    bot.cfg = SimpleNamespace(allowed_users=[123])
    update = MagicMock()
    update.effective_user = None if user_id is None else SimpleNamespace(id=user_id)
    try:
        with pytest.raises(ApplicationHandlerStop):
            await bot.enforce_access(update, MagicMock())
    finally:
        bot.cfg = original_cfg
