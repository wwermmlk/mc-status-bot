"""서버 TPS(초당 틱 수)를 RCON으로 조회한다.

TPS는 Status Ping이나 Query 프로토콜에 없다. 콘솔 명령으로만 얻을 수 있어서 RCON을 쓴다.
서버 종류마다 명령과 출력이 다르므로 Forge → 바닐라/Paper 순으로 시도한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rcon import Rcon, RconError

log = logging.getLogger("mc-status-bot")

# 마인크래프트 색 코드(§a 등)와 제어문자를 걷어낸다. Paper 계열은 색을 섞어 출력한다.
COLOR_RE = re.compile(r"§.")
# Forge: "Overall: Mean tick time: 4.031 ms. Mean TPS: 20.000"
FORGE_RE = re.compile(r"Mean tick time:\s*([\d.]+)\s*ms\.?\s*Mean TPS:\s*([\d.]+)")
# Paper/Spigot: "TPS from last 1m, 5m, 15m: 20.0, 19.9, 20.0"
PAPER_RE = re.compile(r"TPS from last[^:]*:\s*([\d.]+)")
# 서버가 20틱/초로 도는 게 정상. 그 이상 값은 반올림 오차로 본다.
MAX_TPS = 20.0
COMMANDS = ("forge tps", "tps")


@dataclass
class TpsInfo:
    tps: float
    tick_ms: float | None = None


def parse_tps(response: str) -> TpsInfo | None:
    """콘솔 응답에서 TPS를 뽑는다. 못 읽으면 None."""
    text = COLOR_RE.sub("", response or "")
    # Forge는 차원별로 여러 줄을 출력한다. 전체(Overall) 줄이 있으면 그것을 쓴다.
    overall = next((line for line in text.splitlines() if line.strip().startswith("Overall")), None)
    for candidate in (overall, text):
        if candidate and (match := FORGE_RE.search(candidate)):
            return TpsInfo(min(float(match[2]), MAX_TPS), float(match[1]))
    if match := PAPER_RE.search(text):
        return TpsInfo(min(float(match[1]), MAX_TPS))
    return None


async def fetch_tps(rcon: Rcon) -> TpsInfo | None:
    """서버 종류에 맞는 명령을 찾아 TPS를 조회한다. 실패하면 None."""
    for command in COMMANDS:
        try:
            info = parse_tps(await rcon.command(command))
        except RconError as exc:
            log.debug("TPS 조회 실패(%s): %s", command, exc)
            return None
        if info is not None:
            return info
    return None


if __name__ == "__main__":
    import asyncio
    import os

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    async def main() -> None:
        rcon = Rcon(
            os.getenv("RCON_HOST", "127.0.0.1"),
            int(os.getenv("RCON_PORT", "25575")),
            os.getenv("RCON_PASSWORD", ""),
        )
        try:
            for command in COMMANDS:
                try:
                    print(f"--- {command} ---")
                    print(await rcon.command(command) or "(응답 없음)")
                except RconError as exc:
                    print(f"실패: {exc}")
                    break
            info = await fetch_tps(rcon)
            print("\n해석 결과:", info or "(TPS를 읽지 못함)")
        finally:
            await rcon.close()

    asyncio.run(main())
