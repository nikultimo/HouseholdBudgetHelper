from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from yadisk.sync import YadiskSync


@pytest.fixture
def sync_obj():
    return YadiskSync(
        token="fake-token",
        remote_path="/budget/test.xlsx",
        local_path="/tmp/test.xlsx",
    )


def _make_client_ctx(mock_client: AsyncMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _workbook_with_invalid_alignment() -> bytes:
    """Return a structurally valid XLSX whose style is rejected by openpyxl."""
    source = Path("example/budget_example.xlsx")
    output = BytesIO()
    with ZipFile(source) as original, ZipFile(output, "w", ZIP_DEFLATED) as modified:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "xl/styles.xml":
                content = content.replace(
                    b'vertical="center"',
                    b'vertical="invalid-value"',
                    1,
                )
            modified.writestr(info, content)
    return output.getvalue()


def _valid_workbook() -> bytes:
    return Path("example/budget_example.xlsx").read_bytes()


@pytest.mark.asyncio
async def test_download_calls_httpx(sync_obj, tmp_path):
    sync_obj.local_path = str(tmp_path / "test.xlsx")

    async def _chunks():
        yield _valid_workbook()

    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"href": "https://download-url"}
    mock_get_resp.raise_for_status = MagicMock()

    mock_stream_resp = MagicMock()
    mock_stream_resp.raise_for_status = MagicMock()
    mock_stream_resp.aiter_bytes.return_value = _chunks()

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_resp)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_get_resp)
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    with patch("yadisk.sync.httpx.AsyncClient", return_value=_make_client_ctx(mock_client)):
        await sync_obj.download()

    mock_client.get.assert_awaited_once()
    mock_get_resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_download_rejects_unreadable_workbook_without_replacing_local_file(sync_obj, tmp_path):
    local = tmp_path / "test.xlsx"
    original = b"known-local-content"
    local.write_bytes(original)
    sync_obj.local_path = str(local)

    async def _chunks():
        yield _workbook_with_invalid_alignment()

    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"href": "https://download-url"}
    mock_get_resp.raise_for_status = MagicMock()
    mock_stream_resp = MagicMock()
    mock_stream_resp.raise_for_status = MagicMock()
    mock_stream_resp.aiter_bytes.return_value = _chunks()
    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_resp)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_get_resp)
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    with (
        patch("yadisk.sync.httpx.AsyncClient", return_value=_make_client_ctx(mock_client)),
        pytest.raises(ValueError, match="not a readable Excel workbook"),
    ):
        await sync_obj.download()

    assert local.read_bytes() == original
    assert not (tmp_path / "test.xlsx.tmp").exists()


@pytest.mark.asyncio
async def test_upload_calls_httpx(sync_obj, tmp_path):
    local = tmp_path / "test.xlsx"
    local.write_bytes(b"excel")
    sync_obj.local_path = str(local)

    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"href": "https://upload-url"}
    mock_get_resp.raise_for_status = MagicMock()

    mock_put_resp = MagicMock()
    mock_put_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_get_resp)
    mock_client.put = AsyncMock(return_value=mock_put_resp)

    with patch("yadisk.sync.httpx.AsyncClient", return_value=_make_client_ctx(mock_client)):
        await sync_obj.upload()

    mock_client.put.assert_awaited_once()
    mock_put_resp.raise_for_status.assert_called_once()


@pytest.mark.asyncio
async def test_upload_retries_on_423(sync_obj, tmp_path):
    import httpx as real_httpx
    local = tmp_path / "test.xlsx"
    local.write_bytes(b"excel")
    sync_obj.local_path = str(local)

    mock_get_resp = MagicMock()
    mock_get_resp.json.return_value = {"href": "https://upload-url"}
    mock_get_resp.raise_for_status = MagicMock()

    locked_response = MagicMock()
    locked_response.status_code = 423
    locked_exc = real_httpx.HTTPStatusError(
        "locked", request=MagicMock(), response=locked_response
    )

    success_put = MagicMock()
    success_put.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_get_resp)
    mock_client.put = AsyncMock(side_effect=[locked_exc, success_put])

    with patch("yadisk.sync.httpx.AsyncClient", return_value=_make_client_ctx(mock_client)), \
         patch("yadisk.sync.asyncio.sleep") as mock_sleep:
        await sync_obj.upload(retries=3, retry_delay=1.0)

    mock_sleep.assert_awaited_once_with(1.0)
    assert mock_client.put.await_count == 2


def test_empty_token_raises():
    with pytest.raises(ValueError):
        YadiskSync(token="", remote_path="/p", local_path="/l")
