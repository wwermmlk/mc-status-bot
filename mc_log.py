"""마인크래프트 서버 로그(latest.log)를 실시간으로 읽고 이벤트로 분류한다.

- LogTailer: 파일 끝을 따라가며 새 줄을 넘겨준다. 서버 재시작으로 로그가 교체되면 다시 연다.
- LogParser: 한 줄을 채팅/접속/퇴장/사망/서버 켜짐·꺼짐/기타로 분류한다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import AsyncIterator

log = logging.getLogger("mc-bridge")

# Forge : [17Sep2026 12:34:56.789] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: <Steve> hi
# 바닐라: [12:34:56] [Server thread/INFO]: <Steve> hi
LINE_RE = re.compile(
    r"^\[(?P<time>[^\]]+)\] \[(?P<thread>[^\]/]+)/(?P<level>\w+)\]"
    r"(?: \[(?P<logger>[^\]]*)\])?: (?P<msg>.*)$"
)
CLOCK_RE = re.compile(r"\d{2}:\d{2}:\d{2}")

NAME = r"(?P<name>\w{3,16})"
# 1.19+ 에서 서명되지 않은 채팅은 앞에 [Not Secure] 가 붙는다.
CHAT_RE = re.compile(r"^(?:\[Not Secure\] )?<(?P<name>[^>]+)> (?P<text>.*)$")
# 채팅 서식을 바꾸는 모드팩: " Steve » 안녕" 처럼 이름 뒤에 기호 하나가 붙는다.
# 서버가 로그를 cp949로 쓰면 cp949에 없는 기호(» 등)는 "?" 로 저장된다.
# 형식이 느슨하므로 MinecraftServer 로거가 남긴 줄에만 적용한다.
STYLED_CHAT_RE = re.compile(rf"^\s*{NAME} [^\w\s] (?P<text>.*)$")
CHAT_LOGGER = "MinecraftServer"
JOIN_RE = re.compile(rf"^{NAME} joined the game$")
LEAVE_RE = re.compile(rf"^{NAME} left the game$")
DEATH_NAME_RE = re.compile(rf"^{NAME} ")
START_RE = re.compile(r"^Done \([\d.]+s\)! For help")
STOP_RE = re.compile(r"^Stopping server$")

# 바닐라 1.20 사망 메시지(death.*)에서 이름 뒤에 오는 문구.
DEATH_PHRASES = (
    " was slain by", " was shot by", " was fireballed by", " was pummeled by",
    # " was killed" 단독은 /kill 로 죽었을 때 (by·trying to hurt 변형도 함께 포함)
    " was killed", " was blown up by", " blew up",
    " was squashed by", " was squished too much", " was pricked to death",
    " walked into a cactus", " was poked to death by a sweet berry bush",
    " drowned", " experienced kinetic energy", " hit the ground too hard",
    " fell from a high place", " fell off a ladder", " fell off some",
    " fell off scaffolding", " fell while climbing", " fell out of the water",
    " was doomed to fall", " fell too far and was finished by",
    " fell out of the world", " left the confines of this world",
    " was struck by lightning", " went up in flames", " walked into fire",
    " burned to death", " was burnt to a crisp", " tried to swim in lava",
    " discovered the floor was lava", " walked into danger zone",
    " froze to death", " was frozen to death by", " was stung to death",
    " was obliterated by a sonically-charged shriek", " was impaled",
    " was skewered by a falling stalactite", " starved to death",
    " suffocated in a wall", " withered away", " was roasted in dragon breath",
    " didn't want to live in the same world as", " died",
)

# 접속 로그: Steve[/1.2.3.4:51234] logged in with entity id ...
IP_PORT_RE = re.compile(r"/(?:\d{1,3}\.){3}\d{1,3}:\d+")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


@dataclass
class LogEvent:
    kind: str  # chat, join, leave, death, start, stop, other
    time: str = ""  # "HH:MM:SS" (로그에 적힌 서버 시각)
    name: str = ""
    text: str = ""
    raw: str = ""


def mask_ips(line: str) -> str:
    """플레이어 IP를 가린다. 비공개 채널이라도 디스코드에 남기지 않기 위함."""
    line = IP_PORT_RE.sub("/[IP 숨김]", line)
    return IPV4_RE.sub("[IP 숨김]", line)


class LogParser:
    """로그 줄을 이벤트로 분류한다.

    사망 메시지는 형식이 제각각이라, 오탐을 줄이려고 '지금 접속 중인 플레이어 이름으로
    시작하는 줄' 에만 사망 문구를 대조한다. 그래서 접속자 목록을 함께 관리한다.
    """

    def __init__(self) -> None:
        self.online: set[str] = set()

    def parse(self, line: str) -> LogEvent:
        match = LINE_RE.match(line)
        if not match:
            return LogEvent("other", raw=line)

        clock = CLOCK_RE.search(match["time"])
        time = clock[0] if clock else ""
        thread, msg = match["thread"], match["msg"]

        if chat := self._match_chat(thread, match["logger"] or "", msg):
            # 채팅을 쳤다면 접속 중인 게 확실하다. 봇 시작 전부터 있던 사람도 여기서 채워진다.
            self.online.add(chat["name"])
            return LogEvent("chat", time, chat["name"], chat["text"], line)

        # 채팅 외 이벤트는 게임 로직을 처리하는 Server thread 에서만 나온다.
        if thread != "Server thread":
            return LogEvent("other", time, raw=line)
        if m := JOIN_RE.match(msg):
            self.online.add(m["name"])
            return LogEvent("join", time, m["name"], raw=line)
        if m := LEAVE_RE.match(msg):
            self.online.discard(m["name"])
            return LogEvent("leave", time, m["name"], raw=line)
        if START_RE.match(msg):
            self.online.clear()
            return LogEvent("start", time, raw=line)
        if STOP_RE.match(msg):
            self.online.clear()
            return LogEvent("stop", time, raw=line)
        if (m := DEATH_NAME_RE.match(msg)) and m["name"] in self.online:
            rest = msg[len(m["name"]):]
            if rest.startswith(DEATH_PHRASES):
                return LogEvent("death", time, m["name"], msg, line)
        return LogEvent("other", time, raw=line)

    @staticmethod
    def _match_chat(thread: str, logger: str, msg: str) -> re.Match | None:
        # 채팅은 서버/모드에 따라 Server thread 가 아닌 비동기 스레드에서 기록되기도 한다.
        if not (thread == "Server thread" or thread.startswith(("ForkJoinPool", "Async Chat"))):
            return None
        if m := CHAT_RE.match(msg):
            return m
        if CHAT_LOGGER in logger:
            return STYLED_CHAT_RE.match(msg)
        return None


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # 한국어 Windows의 Java 17은 기본 문자셋이 MS949인 경우가 있다.
        return raw.decode("cp949", errors="replace")


class LogTailer:
    """latest.log 를 `tail -f` 처럼 따라 읽는다.

    파일을 계속 열어두지 않고 매번 열고 닫는다. Windows에서는 열린 파일의 이름을
    바꿀 수 없어서, 붙잡고 있으면 서버가 재시작할 때 로그 보관(rename)이 실패한다.
    """

    READ_CHUNK = 1 << 20
    # 서버가 꺼져 있으면 로그 파일이 잠깐 사라질 수 있다. 이보다 오래 없으면 경로 문제로 보고 알린다.
    MISSING_WARN_SECONDS = 60.0

    def __init__(self, path: str, poll_seconds: float = 0.5) -> None:
        self.path = path
        self.poll_seconds = poll_seconds

    async def lines(self) -> AsyncIterator[str]:
        position = 0
        file_id: int | None = None
        pending = b""
        # 봇 시작 시점에 이미 있던 로그는 건너뛴다(과거 로그 폭주 방지).
        # 반대로 시작 시점에 파일이 없었다면, 새로 생긴 파일은 처음부터 읽는다.
        skip_existing = True
        missing_since: float | None = None
        warned_missing = False

        while True:
            try:
                stat = os.stat(self.path)
            except FileNotFoundError:
                skip_existing = False
                now = asyncio.get_running_loop().time()
                missing_since = now if missing_since is None else missing_since
                if not warned_missing and now - missing_since >= self.MISSING_WARN_SECONDS:
                    # 경로가 틀리면 조용히 기다리기만 해서 원인을 못 찾는다.
                    warned_missing = True
                    log.error(
                        "로그 파일이 %.0f초째 없습니다: %s — 서버가 꺼져 있거나 .env의 MC_LOG_PATH 가 틀렸습니다",
                        self.MISSING_WARN_SECONDS,
                        self.path,
                    )
                await asyncio.sleep(self.poll_seconds)
                continue

            if warned_missing:
                log.info("로그 파일을 다시 찾았습니다: %s", self.path)
            missing_since, warned_missing = None, False

            if file_id is None:
                file_id = stat.st_ino
                position = stat.st_size if skip_existing else 0
            elif stat.st_ino != file_id or stat.st_size < position:
                log.info("로그 파일이 교체되었습니다. 처음부터 다시 읽습니다.")
                file_id, position, pending = stat.st_ino, 0, b""

            data = b""
            if stat.st_size > position:
                try:
                    with open(self.path, "rb") as f:
                        f.seek(position)
                        data = f.read(self.READ_CHUNK)
                except OSError as exc:
                    log.warning("로그 파일을 읽지 못했습니다: %s", exc)
                position += len(data)

            if data:
                pending += data
                *complete, pending = pending.split(b"\n")
                for raw in complete:
                    yield _decode(raw.rstrip(b"\r"))

            # 읽을 게 더 남았으면 쉬지 않고 이어서 읽는다.
            if len(data) < self.READ_CHUNK:
                await asyncio.sleep(self.poll_seconds)
