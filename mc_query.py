"""마인크래프트 자바 서버 조회 모듈.

Query 프로토콜(UDP)로 전체 접속자 닉네임을 가져오고,
Query가 불가능하면 Status Ping(TCP)의 sample 목록으로 폴백한다.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from mcstatus import JavaServer

TIMEOUT = 3.0

# 게임 설정에서 '서버 목록에 표시' 를 끈 플레이어는 이 UUID와 이름으로 내려온다.
ANONYMOUS_UUID = "00000000-0000-0000-0000-000000000000"
ANONYMOUS_NAME = "Anonymous Player"


@dataclass
class ServerInfo:
    online: bool = False
    players_online: int = 0
    players_max: int = 0
    player_names: list[str] = field(default_factory=list)
    anonymous_count: int = 0  # 닉네임을 숨긴 플레이어 수
    motd: str | None = None
    latency_ms: float | None = None
    source: str = "status"  # "query" 이면 닉네임 목록이 완전함
    error: str | None = None


def _clean(names) -> list[str]:
    """None/빈 값을 걸러내고 정렬한다."""
    return sorted({n for n in (names or []) if n}, key=str.lower)


def _split_sample(sample) -> tuple[list[str], int]:
    """Status Ping의 sample을 실명 목록과 익명 인원수로 나눈다.

    익명 플레이어는 모두 이름이 'Anonymous Player'로 같아서, 이름만으로 중복을
    제거하면 여러 명이 한 명으로 합쳐진다. 그래서 개수를 따로 센다.
    """
    names: list[str] = []
    anonymous = 0
    for player in sample or []:
        if str(player.id) == ANONYMOUS_UUID or player.name == ANONYMOUS_NAME:
            anonymous += 1
        elif player.name:
            names.append(player.name)
    return _clean(names), anonymous


async def fetch_server_info(
    host: str, port: int = 25565, query_port: int | None = None
) -> ServerInfo:
    info = ServerInfo()

    # 1) Status Ping — 온라인 여부 / 인원수 / MOTD / 지연시간
    try:
        server = JavaServer(host, port, timeout=TIMEOUT)
        status = await server.async_status()
    except Exception as exc:  # noqa: BLE001 - 봇이 죽지 않도록 전부 흡수
        info.error = f"{type(exc).__name__}: {exc}"
        return info

    info.online = True
    info.players_online = status.players.online
    info.players_max = status.players.max
    info.latency_ms = round(status.latency, 1)
    try:
        info.motd = status.motd.to_plain()
    except Exception:  # noqa: BLE001 - MOTD 파싱 실패는 치명적이지 않음
        info.motd = None

    info.player_names, info.anonymous_count = _split_sample(status.players.sample)
    info.source = "status"

    # 2) Query — 전체 닉네임 목록 (server.properties의 enable-query=true 필요)
    # 결과 해석까지 try 안에서 처리한다. 밖에서 터지면 호출 측 루프가 통째로 멈춘다.
    try:
        q_server = JavaServer(host, query_port or port, timeout=TIMEOUT)
        query = await q_server.async_query()
        # Query는 익명 설정과 무관하게 실제 닉네임을 전부 준다.
        names = _clean(query.players.list)
        online = query.players.online
        maximum = query.players.max
    except Exception:  # noqa: BLE001 - Query 미설정/응답 형식 변화 모두 status로 폴백
        return info

    info.player_names = names
    info.anonymous_count = 0
    info.players_online = online
    info.players_max = maximum
    info.source = "query"
    return info


if __name__ == "__main__":
    import os

    from dotenv import load_dotenv

    load_dotenv()
    _host = os.getenv("MC_HOST")
    if not _host:
        raise SystemExit("MC_HOST가 설정되지 않았습니다. .env 파일을 확인하세요.")
    _port = int(os.getenv("MC_PORT", "25565"))
    _qport = int(os.getenv("MC_QUERY_PORT", str(_port)))

    result = asyncio.run(fetch_server_info(_host, _port, _qport))
    print(f"주소        : {_host}:{_port} (query {_qport})")
    print(f"온라인      : {result.online}")
    if result.error:
        print(f"에러        : {result.error}")
    else:
        _listed = ", ".join(result.player_names)
        if result.anonymous_count:
            _listed = f"{_listed + ', ' if _listed else ''}익명 {result.anonymous_count}명"
        print(f"인원        : {result.players_online} / {result.players_max}")
        print(f"접속자      : {_listed or '(없음)'}")
        print(f"MOTD        : {result.motd}")
        print(f"지연시간    : {result.latency_ms} ms")
        print(f"조회 방식   : {result.source}")
        _shown = len(result.player_names) + result.anonymous_count
        if _shown != result.players_online:
            print(f"  ※ 인원 수({result.players_online})와 목록 수({_shown})가 다릅니다.")
        if result.source != "query":
            print("  ※ Query가 응답하지 않아 닉네임 목록이 불완전할 수 있습니다.")
            print("     server.properties에 enable-query=true 설정 후 UDP 포트를 여세요.")
