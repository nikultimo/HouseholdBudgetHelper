from __future__ import annotations
import asyncio
import logging
import os
import httpx

logger = logging.getLogger(__name__)

_API = "https://cloud-api.yandex.net/v1/disk/resources"


class YadiskSync:
    def __init__(self, token: str, remote_path: str, local_path: str) -> None:
        if not token:
            raise ValueError("Yandex Disk token must not be empty")
        self.token = token
        self.remote_path = remote_path
        self.local_path = local_path
        self._headers = {"Authorization": f"OAuth {token}"}

    async def download(self, retries: int = 5, retry_delay: float = 3.0) -> None:
        last_exc: Exception | None = None
        async with httpx.AsyncClient() as client:
            for attempt in range(1, retries + 1):
                tmp_path = f"{self.local_path}.tmp"
                try:
                    r = await client.get(
                        f"{_API}/download",
                        params={"path": self.remote_path},
                        headers=self._headers,
                        timeout=30,
                    )
                    r.raise_for_status()
                    href = r.json()["href"]
                    async with client.stream(
                        "GET",
                        href,
                        timeout=120,
                        follow_redirects=True,
                    ) as resp:
                        resp.raise_for_status()
                        chunks: list[bytes] = []
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                        data = b"".join(chunks)
                        await asyncio.to_thread(
                            lambda d=data: open(tmp_path, "wb").write(d)  # noqa: ASYNC230
                        )
                    os.replace(tmp_path, self.local_path)
                    return
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    last_exc = exc
                    if attempt < retries:
                        logger.warning(
                            "Yandex Disk download failed (%s) (attempt %d/%d), retrying in %.0fs…",
                            type(exc).__name__,
                            attempt,
                            retries,
                            retry_delay,
                        )
                    else:
                        logger.error(
                            "Yandex Disk download failed (%s) (attempt %d/%d).",
                            type(exc).__name__,
                            attempt,
                            retries,
                        )
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    status = exc.response.status_code
                    if 500 <= status < 600 and attempt < retries:
                        logger.warning(
                            "Yandex Disk download failed (HTTP %d) (attempt %d/%d), retrying in %.0fs…",
                            status,
                            attempt,
                            retries,
                            retry_delay,
                        )
                    else:
                        logger.error("Yandex Disk download failed (HTTP %d): %s", status, exc)
                        raise
                except Exception as exc:
                    last_exc = exc
                    logger.error("Yandex Disk download failed (%s): %s", type(exc).__name__, exc)
                    raise
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                    except Exception:
                        pass

                if attempt < retries:
                    await asyncio.sleep(retry_delay)

        logger.error("Yandex Disk download failed after %d attempts: %r", retries, last_exc)
        raise last_exc  # type: ignore[misc]

    async def upload(self, retries: int = 5, retry_delay: float = 3.0) -> None:
        last_exc: Exception | None = None
        async with httpx.AsyncClient() as client:
            for attempt in range(1, retries + 1):
                try:
                    r = await client.get(
                        f"{_API}/upload",
                        params={"path": self.remote_path, "overwrite": "true"},
                        headers=self._headers,
                        timeout=30,
                    )
                    r.raise_for_status()
                    href = r.json()["href"]
                    file_data = await asyncio.to_thread(
                        lambda: open(self.local_path, "rb").read()
                    )
                    resp = await client.put(href, content=file_data, timeout=120)
                    resp.raise_for_status()
                    return
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    last_exc = exc
                    if attempt < retries:
                        logger.warning(
                            "Yandex Disk upload failed (%s) (attempt %d/%d), retrying in %.0fs…",
                            type(exc).__name__, attempt, retries, retry_delay,
                        )
                    else:
                        logger.error(
                            "Yandex Disk upload failed (%s) (attempt %d/%d).",
                            type(exc).__name__, attempt, retries,
                        )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 423 and attempt < retries:
                        logger.warning(
                            "Yandex Disk locked (attempt %d/%d), retrying in %.0fs…",
                            attempt, retries, retry_delay,
                        )
                        last_exc = exc
                    else:
                        logger.error("Yandex Disk upload failed (%s): %s", type(exc).__name__, exc)
                        raise
                except Exception as exc:
                    logger.error("Yandex Disk upload failed (%s): %s", type(exc).__name__, exc)
                    raise

                if attempt < retries:
                    await asyncio.sleep(retry_delay)
        raise last_exc  # type: ignore[misc]
