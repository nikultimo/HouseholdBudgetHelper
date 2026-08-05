import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_download_and_transcribe(tmp_path):
    mock_voice = MagicMock()
    mock_voice.file_id = "abc123"
    mock_voice.file_size = 1000

    mock_bot = MagicMock()
    mock_file = MagicMock()
    mock_file.download_to_drive = AsyncMock()
    mock_bot.get_file = AsyncMock(return_value=mock_file)

    mock_llm = MagicMock()
    mock_llm.transcribe = AsyncMock(return_value="потратил 300 рублей на кофе")

    with patch("handlers.transaction.tempfile") as mock_tmp:
        mock_tmp.mktemp.return_value = str(tmp_path / "voice.oga")
        (tmp_path / "voice.oga").write_bytes(b"fake")

        from handlers.transaction import download_and_transcribe
        text = await download_and_transcribe(
            voice=mock_voice,
            bot=mock_bot,
            llm_client=mock_llm,
        )

    assert text == "потратил 300 рублей на кофе"
    mock_llm.transcribe.assert_called_once()


@pytest.mark.asyncio
async def test_on_voice_replies_when_whisper_disabled():
    from types import SimpleNamespace
    import bot as bot_mod

    bot_mod.cfg = SimpleNamespace(allowed_users=[123], whisper_api_key="")

    update = MagicMock()
    update.effective_user.id = 123
    update.message.reply_text = AsyncMock()

    context = MagicMock()

    await bot_mod.on_voice(update, context)
    update.message.reply_text.assert_awaited()
