"""마인크래프트 RCON 비동기 클라이언트 (외부 라이브러리 없이 직접 구현).

패킷 형식 (리틀 엔디언):
    int32 길이 | int32 요청 ID | int32 타입 | 본문(ASCII/UTF-8) | 0x00 0x00
타입: 3 = 로그인, 2 = 명령 실행(응답도 2)

보안상 RCON은 비밀번호를 평문으로 보내므로 127.0.0.1 처럼 같은 PC 안에서만 쓴다.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import struct

log = logging.getLogger("mc-bridge")

TYPE_AUTH = 3
TYPE_COMMAND = 2
AUTH_FAILED_ID = -1


class RconError(Exception):
    """서버에 연결할 수 없거나 응답이 올바르지 않음."""


class RconAuthError(RconError):
    """비밀번호가 틀림. 재시도해도 소용없는 오류."""


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = itertools.count(1)
        # 한 연결에서 요청과 응답 순서가 섞이지 않게 한 번에 하나씩만 보낸다.
        self._lock = asyncio.Lock()

    async def command(self, command: str) -> str:
        """명령을 실행하고 응답 문자열을 돌려준다. 연결이 끊겼으면 한 번 다시 연결한다."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    await self._connect()
                    return await self._request(TYPE_COMMAND, command)
                except RconAuthError:
                    await self._close()
                    raise
                except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError, RconError) as exc:
                    await self._close()
                    if attempt == 2:
                        raise RconError(f"{self.host}:{self.port} RCON 실패: {exc!r}") from exc
        raise AssertionError("unreachable")

    async def close(self) -> None:
        async with self._lock:
            await self._close()

    async def _connect(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout
        )
        try:
            await self._request(TYPE_AUTH, self.password)
        except RconAuthError:
            log.error("RCON 비밀번호가 틀렸습니다. .env의 RCON_PASSWORD와 server.properties를 확인하세요.")
            raise

    async def _close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
        self._reader = self._writer = None

    async def _request(self, kind: int, body: str) -> str:
        assert self._reader is not None and self._writer is not None
        request_id = next(self._ids)
        payload = struct.pack("<ii", request_id, kind) + body.encode("utf-8") + b"\x00\x00"
        self._writer.write(struct.pack("<i", len(payload)) + payload)
        await self._writer.drain()

        response_id, response_body = await asyncio.wait_for(self._read_packet(), self.timeout)
        if kind == TYPE_AUTH and response_id == AUTH_FAILED_ID:
            raise RconAuthError("RCON 인증 실패")
        if response_id != request_id:
            raise RconError(f"응답 ID 불일치 (보냄 {request_id}, 받음 {response_id})")
        return response_body

    async def _read_packet(self) -> tuple[int, str]:
        assert self._reader is not None
        (length,) = struct.unpack("<i", await self._reader.readexactly(4))
        if not 10 <= length <= 4110:
            raise RconError(f"비정상적인 패킷 길이: {length}")
        data = await self._reader.readexactly(length)
        response_id, _kind = struct.unpack("<ii", data[:8])
        return response_id, data[8:-2].decode("utf-8", errors="replace")


if __name__ == "__main__":
    import os
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 2:
        raise SystemExit("사용법: python rcon.py <명령>   예) python rcon.py list")

    async def main() -> None:
        rcon = Rcon(
            os.getenv("RCON_HOST", "127.0.0.1"),
            int(os.getenv("RCON_PORT", "25575")),
            os.getenv("RCON_PASSWORD", ""),
        )
        try:
            print(await rcon.command(" ".join(sys.argv[1:])) or "(응답 없음 — 정상)")
        except RconError as exc:
            raise SystemExit(f"[오류] {exc}")
        finally:
            await rcon.close()

    asyncio.run(main())
