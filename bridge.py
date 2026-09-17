"""마인크래프트 서버 채팅·콘솔 로그를 디스코드와 잇는 브리지.

마인크래프트 서버가 돌아가는 PC에서 실행한다.
- 게임 채팅·접속·퇴장·사망·서버 켜짐/꺼짐 → #chat
- 디스코드 #chat 메시지 → 게임 채팅 ([Discord] 닉네임: 메시지)
- 서버 콘솔 로그 전체 → #logs (비공개 채널에서만 전송)

게임 → 디스코드는 logs/latest.log 를 읽어서, 디스코드 → 게임은 같은 PC의 RCON으로 처리한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import socket
import sys
from datetime import datetime, time, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable, Iterable

import discord
from dotenv import load_dotenv

from mc_log import LogEvent, LogParser, LogTailer, mask_ips
from mc_text import build_tellraw
from rcon import Rcon, RconError

load_dotenv()

log = logging.getLogger("mc-bridge")
LOG_FILE = Path(__file__).with_name("bridge.log")

# 두 개가 동시에 돌면 모든 채팅이 두 번씩 올라간다. 이 포트를 잡고 있는 동안은 추가 실행을 막는다.
SINGLE_INSTANCE_PORT = 47651

TOKEN = os.getenv("BRIDGE_DISCORD_TOKEN", "").strip()
CHAT_CHANNEL_ID = os.getenv("BRIDGE_CHAT_CHANNEL_ID", "").strip()
LOGS_CHANNEL_ID = os.getenv("BRIDGE_LOGS_CHANNEL_ID", "").strip()
MC_LOG_PATH = os.getenv("MC_LOG_PATH", "").strip()
RCON_HOST = os.getenv("RCON_HOST", "127.0.0.1").strip()
RCON_PORT = int(os.getenv("RCON_PORT", "25575"))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")
LOG_MASK_IPS = os.getenv("LOG_MASK_IPS", "true").strip().lower() != "false"
LOG_FLUSH_SECONDS = max(1.0, float(os.getenv("LOG_FLUSH_SECONDS", "2")))

WEBHOOK_NAME = "MC Chat Bridge"
# 채팅 웹훅에 쓸 플레이어 얼굴 이미지. {name} 이 플레이어 이름으로 바뀐다.
# mc-heads.net 은 Mojang에 스킨이 정상 등록된 일부 정품 계정도 스티브 얼굴로 돌려줘서 minotar 를 기본으로 쓴다.
AVATAR_URL = os.getenv("AVATAR_URL", "").strip() or "https://minotar.net/helm/{name}/64.png"
# 디스코드 메시지 2000자 제한 - 코드블록 기호 여유분
LOG_MESSAGE_LIMIT = 1900
# Forge는 시작할 때 수천 줄을 쏟아낸다. 이보다 밀리면 버리고 생략 표시를 남긴다.
LOG_QUEUE_LIMIT = 5000
CHAT_QUEUE_LIMIT = 500
# 웹훅 이름에 쓸 수 없는 단어 (디스코드 규칙)
WEBHOOK_FORBIDDEN_WORDS = ("discord", "clyde")
LIST_RE = re.compile(r"players online:\s*(.*)$")
NO_MENTIONS = discord.AllowedMentions.none()

# /채팅날짜 설정을 재시작 후에도 유지하기 위한 파일 (git 제외)
STATE_FILE = Path(__file__).with_name(".bridge_state.json")
DATE_COMMAND = "채팅날짜"
DATE_COMMAND_PAYLOAD = {
    "name": DATE_COMMAND,
    "type": 1,
    "description": "#chat 메시지의 시간에 날짜(년-월-일)를 함께 표시할지 설정합니다",
    # 채널 전체 표시 방식을 바꾸므로 서버 관리 권한이 있는 사람만 보이게 한다.
    "default_member_permissions": str(discord.Permissions(manage_guild=True).value),
    "dm_permission": False,
    "options": [
        {
            "type": 3,
            "name": "표시",
            "description": "날짜 표시 켜기 또는 끄기",
            "required": True,
            "choices": [{"name": "켜기", "value": "on"}, {"name": "끄기", "value": "off"}],
        }
    ],
}


def _check_config() -> None:
    missing = []
    if not TOKEN:
        missing.append("BRIDGE_DISCORD_TOKEN (브리지 전용 봇 토큰)")
    if not CHAT_CHANNEL_ID:
        missing.append("BRIDGE_CHAT_CHANNEL_ID (채팅 채널 ID)")
    if not MC_LOG_PATH:
        missing.append("MC_LOG_PATH (서버 폴더의 logs\\latest.log 경로)")
    if not RCON_PASSWORD:
        missing.append("RCON_PASSWORD (server.properties의 rcon.password)")
    if missing:
        _fatal("[설정 오류] .env 파일에 다음 값이 필요합니다:", *(f"  - {item}" for item in missing))
    if not os.path.isdir(os.path.dirname(os.path.abspath(MC_LOG_PATH))):
        _fatal(
            f"[설정 오류] 로그 폴더가 없습니다: {MC_LOG_PATH}",
            "  마인크래프트 서버 폴더 안의 logs\\latest.log 경로인지 확인하세요.",
        )


def _setup_logging() -> None:
    """실행할 때만 호출한다. 모듈을 불러오기만 해도 설정하면 테스트 기록이 실제 로그 파일에 섞인다.

    창 없이(pythonw) 백그라운드로 돌면 실행 기록을 볼 곳이 없으므로 파일에도 남긴다. (1MB × 4개 순환)
    """
    handlers: list[logging.Handler] = [
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True)
    ]
    if sys.stderr is not None:  # pythonw 로 실행하면 콘솔이 없다
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def _fatal(*lines: str) -> None:
    """오류를 남기고 종료한다. 창 없이 실행 중이면 print 는 보이지 않으므로 로그 파일에 기록한다."""
    for line in lines:
        log.error(line)
    raise SystemExit(1)


def _acquire_single_instance() -> socket.socket:
    """이미 브리지가 실행 중이면 종료한다. 반환된 소켓을 프로그램이 끝날 때까지 들고 있어야 한다."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        lock.close()
        _fatal(
            "[중복 실행] 브리지가 이미 실행 중입니다. 두 개가 돌면 채팅이 두 번씩 올라가서 이번 실행은 종료합니다.",
            "  백그라운드 실행을 멈추려면 작업 스케줄러에서 mc-bridge 작업을 '끝내기' 하세요.",
        )
    return lock


# ───────────────────────── 메시지 형식 (순수 함수) ─────────────────────────


def make_stamp(log_time: str, show_date: bool, now: datetime | None = None) -> str:
    """메시지 앞에 붙일 시각 표시. 예) `[00:18:37]` 또는 `[2026-09-18 00:18:37]`

    로그에는 시각만 믿을 만하게 남아 있어서(날짜는 OS 언어에 따라 '189월2026' 처럼 제각각)
    날짜는 서버 PC의 오늘 날짜를 쓴다. 자정 직후에 전날 23시대 로그를 처리하는 경우는 하루를 뺀다.
    """
    if not log_time:
        return ""
    if not show_date:
        return f"`[{log_time}]` "
    now = now or datetime.now()
    stamp = datetime.combine(now.date(), time.fromisoformat(log_time))
    if stamp - now > timedelta(hours=1):
        stamp -= timedelta(days=1)
    return f"`[{stamp:%Y-%m-%d %H:%M:%S}]` "


def load_show_date() -> bool:
    try:
        return bool(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("show_date", False))
    except (OSError, ValueError):
        return False


def save_show_date(value: bool) -> None:
    STATE_FILE.write_text(json.dumps({"show_date": value}), encoding="utf-8")


def format_event(event: LogEvent, stamp: str = "") -> str | None:
    """채팅 외 이벤트를 디스코드 메시지로 만든다. 보낼 필요 없는 이벤트는 None."""
    clock = stamp
    name = discord.utils.escape_markdown(event.name)
    if event.kind == "join":
        return f"{clock}➕ **{name}**님이 접속했습니다"
    if event.kind == "leave":
        return f"{clock}➖ **{name}**님이 퇴장했습니다"
    if event.kind == "death":
        return f"{clock}☠️ {discord.utils.escape_markdown(event.text)}"
    if event.kind == "start":
        return f"{clock}✅ 서버가 열렸습니다"
    if event.kind == "stop":
        return f"{clock}⛔ 서버가 닫혔습니다"
    return None


def webhook_name_allowed(name: str) -> bool:
    lowered = name.lower()
    return 1 <= len(name) <= 80 and not any(word in lowered for word in WEBHOOK_FORBIDDEN_WORDS)


def chunk_log_lines(lines: Iterable[str], limit: int = LOG_MESSAGE_LIMIT) -> list[str]:
    """로그 줄들을 디스코드 메시지 크기에 맞춰 코드블록 여러 개로 나눈다."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        # 로그 안의 ``` 가 코드블록을 끝내버리지 않게 폭 없는 공백을 끼운다.
        line = line.replace("```", "`​``")
        if len(line) > limit:
            line = line[: limit - 1] + "…"
        if current and size + len(line) + 1 > limit:
            chunks.append(current)
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(current)
    return ["```\n" + "\n".join(chunk) + "\n```" for chunk in chunks]


def check_logs_privacy(channel: discord.TextChannel) -> tuple[bool, list[str]]:
    """#logs 가 방장 전용인지 확인한다.

    반환: (@everyone 이 못 보면 True, 그 외 볼 수 있는 대상에 대한 경고 목록)
    관리자 권한 역할과 서버 소유자는 디스코드 구조상 막을 수 없으므로 경고하지 않는다.
    """
    guild = channel.guild
    if channel.permissions_for(guild.default_role).view_channel:
        return False, ["@everyone 이 이 채널을 볼 수 있습니다"]

    me = guild.me
    warnings = []
    for role in guild.roles:
        if role.is_default() or role in me.roles or role.permissions.administrator:
            continue
        if channel.permissions_for(role).view_channel:
            warnings.append(f"역할 '{role.name}' 이(가) 이 채널을 볼 수 있습니다")
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role) or target.id in (me.id, guild.owner_id):
            continue
        if overwrite.view_channel:
            label = getattr(target, "display_name", None) or f"ID {target.id}"
            warnings.append(f"멤버 '{label}' 에게 개별 보기 권한이 있습니다")
    return True, warnings


# ───────────────────────── 봇 ─────────────────────────


class BridgeBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # 디스코드 메시지 내용을 읽으려면 필요. 개발자 포털에서도 켜야 한다.
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)

        self.rcon = Rcon(RCON_HOST, RCON_PORT, RCON_PASSWORD)
        self.parser = LogParser()
        self.chat_channel: discord.TextChannel | None = None
        self.logs_channel: discord.TextChannel | None = None
        self.webhook: discord.Webhook | None = None

        self.chat_queue: asyncio.Queue[LogEvent] = asyncio.Queue(CHAT_QUEUE_LIMIT)
        self.log_queue: asyncio.Queue[str] = asyncio.Queue(LOG_QUEUE_LIMIT)
        self.dropped_logs = 0
        self.logs_blocked = False
        self.show_date = load_show_date()

        self._started = False
        self._tasks: list[asyncio.Task] = []

    # ── 시작 ──

    async def on_ready(self) -> None:
        log.info("로그인: %s (id=%s)", self.user, self.user.id)
        # 재연결할 때도 on_ready 가 다시 불리므로 한 번만 초기화한다.
        if self._started:
            return
        self._started = True

        self.chat_channel = await self._resolve_channel(CHAT_CHANNEL_ID, "채팅")
        if self.chat_channel is not None:
            self.webhook = await self._resolve_webhook(self.chat_channel)
            await self._register_date_command(self.chat_channel.guild)
        if LOGS_CHANNEL_ID:
            self.logs_channel = await self._resolve_channel(LOGS_CHANNEL_ID, "로그")
            if self.logs_channel is not None:
                self._report_logs_privacy()
        await self._seed_online_players()

        for name, factory in (
            ("로그 감시", self._tail_loop),
            ("채팅 전송", self._chat_sender),
            ("로그 전송", self._log_sender),
        ):
            self._tasks.append(asyncio.create_task(self._supervise(name, factory)))

    async def _supervise(self, name: str, factory: Callable[[], Awaitable[None]]) -> None:
        """작업이 예외로 죽어도 로그를 남기고 다시 시작한다. 브리지가 조용히 멈추는 일을 막는다."""
        while True:
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("%s 작업이 오류로 멈춰 5초 뒤 다시 시작합니다", name)
            await asyncio.sleep(5)

    async def _resolve_channel(self, channel_id: str, label: str) -> discord.TextChannel | None:
        try:
            channel = self.get_channel(int(channel_id)) or await self.fetch_channel(int(channel_id))
        except (discord.NotFound, discord.Forbidden, ValueError) as exc:
            log.error("%s 채널(%s)에 접근할 수 없습니다: %s", label, channel_id, exc)
            return None
        if not isinstance(channel, discord.TextChannel):
            log.error("%s 채널(%s)이 텍스트 채널이 아닙니다", label, channel_id)
            return None
        log.info("%s 채널: #%s", label, channel.name)
        return channel

    async def _resolve_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        """플레이어 이름·스킨으로 채팅을 보낼 웹훅. 있으면 재사용하고 없으면 만든다."""
        try:
            for hook in await channel.webhooks():
                if hook.name == WEBHOOK_NAME and hook.token:
                    return hook
            return await channel.create_webhook(name=WEBHOOK_NAME)
        except discord.Forbidden:
            log.warning("웹훅 관리 권한이 없어 채팅을 봇 이름으로 보냅니다 (플레이어 아바타 없음)")
            return None

    async def _register_date_command(self, guild: discord.Guild) -> None:
        """/채팅날짜 를 등록한다.

        상태 봇과 같은 봇 계정을 쓸 수 있으므로 명령어 목록을 통째로 덮어쓰는 sync 대신
        이 명령어 하나만 추가·갱신(upsert)한다. 덮어쓰면 상대편 명령어(/status)가 지워진다.
        """
        try:
            await self.http.upsert_guild_command(self.application_id, guild.id, DATE_COMMAND_PAYLOAD)
            state = "켜짐" if self.show_date else "꺼짐"
            log.info("/%s 명령어 등록 (현재 날짜 표시: %s)", DATE_COMMAND, state)
        except discord.HTTPException as exc:
            log.warning("/%s 명령어를 등록하지 못했습니다: %s", DATE_COMMAND, exc)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # 같은 봇 계정의 다른 프로그램(상태 봇) 명령어는 그쪽에서 응답하므로 건드리지 않는다.
        if interaction.type is not discord.InteractionType.application_command:
            return
        data = interaction.data or {}
        if data.get("name") != DATE_COMMAND:
            return

        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is None or not perms.manage_guild:
            await interaction.response.send_message("서버 관리 권한이 있어야 바꿀 수 있습니다.", ephemeral=True)
            return

        value = next((opt.get("value") for opt in data.get("options", []) if opt.get("name") == "표시"), None)
        self.show_date = value == "on"
        save_show_date(self.show_date)
        example = make_stamp(datetime.now().strftime("%H:%M:%S"), self.show_date).strip()
        state = "켰습니다" if self.show_date else "껐습니다"
        log.info("%s 님이 날짜 표시를 %s", interaction.user, state)
        await interaction.response.send_message(
            f"#chat 날짜 표시를 {state}. 이제 이렇게 표시됩니다: {example}", ephemeral=True
        )

    def _report_logs_privacy(self) -> None:
        assert self.logs_channel is not None
        private, warnings = check_logs_privacy(self.logs_channel)
        if not private:
            log.error("#%s 채널이 공개 상태라 로그 전송을 막습니다. @everyone 의 채널 보기를 거부하세요.", self.logs_channel.name)
        for warning in warnings:
            log.warning("#%s: %s", self.logs_channel.name, warning)

    async def _seed_online_players(self) -> None:
        """브리지를 켜기 전부터 접속해 있던 사람도 사망 메시지를 인식하도록 접속자를 채운다."""
        try:
            response = await self.rcon.command("list")
        except RconError as exc:
            log.warning("RCON에 연결하지 못했습니다 (서버가 꺼져 있을 수 있음): %s", exc)
            return
        match = LIST_RE.search(response)
        if match:
            names = [n.strip() for n in match[1].split(",") if n.strip()]
            self.parser.online.update(names)
            log.info("RCON 연결 확인. 현재 접속자 %d명", len(names))

    # ── 게임 → 디스코드 ──

    async def _tail_loop(self) -> None:
        log.info("로그 파일 감시 시작: %s", MC_LOG_PATH)
        async for line in LogTailer(MC_LOG_PATH).lines():
            if self.logs_channel is not None:
                try:
                    self.log_queue.put_nowait(mask_ips(line) if LOG_MASK_IPS else line)
                except asyncio.QueueFull:
                    self.dropped_logs += 1

            event = self.parser.parse(line)
            if event.kind != "other" and self.chat_channel is not None:
                try:
                    self.chat_queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("채팅 전송이 밀려 메시지 하나를 건너뜁니다: %s", event.kind)

    async def _chat_sender(self) -> None:
        # 전송이 디스코드 속도 제한으로 느려져도 로그 읽기는 멈추지 않도록 큐로 분리했다.
        while True:
            event = await self.chat_queue.get()
            try:
                await self._send_chat_event(event)
            except discord.HTTPException as exc:
                log.warning("#chat 전송 실패: %s", exc)

    async def _send_chat_event(self, event: LogEvent) -> None:
        assert self.chat_channel is not None
        stamp = make_stamp(event.time, self.show_date)
        if event.kind != "chat":
            content = format_event(event, stamp)
            if content:
                await self.chat_channel.send(content, allowed_mentions=NO_MENTIONS)
            return

        text = discord.utils.escape_markdown(event.text)
        if self.webhook is not None and webhook_name_allowed(event.name):
            try:
                await self.webhook.send(
                    f"{stamp}{text}",
                    username=event.name,
                    avatar_url=AVATAR_URL.format(name=event.name),
                    allowed_mentions=NO_MENTIONS,
                    # wait=False(기본값)면 디스코드가 메시지를 만들기 전에 응답해서,
                    # 바로 뒤에 보낸 봇 메시지(접속·사망 알림)가 먼저 게시되는 순서 역전이 생긴다.
                    wait=True,
                )
                return
            except discord.NotFound:
                log.warning("웹훅이 삭제되어 다시 만듭니다")
                self.webhook = await self._resolve_webhook(self.chat_channel)
        name = discord.utils.escape_markdown(event.name)
        await self.chat_channel.send(f"{stamp}**{name}**: {text}", allowed_mentions=NO_MENTIONS)

    async def _log_sender(self) -> None:
        while True:
            await asyncio.sleep(LOG_FLUSH_SECONDS)
            if self.logs_channel is None or (self.log_queue.empty() and not self.dropped_logs):
                continue

            lines = []
            while not self.log_queue.empty():
                lines.append(self.log_queue.get_nowait())

            # 채널 권한은 운영 중에도 바뀔 수 있으니 보낼 때마다 확인한다.
            private, _ = check_logs_privacy(self.logs_channel)
            if not private:
                if not self.logs_blocked:
                    log.error("#%s 채널이 공개 상태로 바뀌어 로그 전송을 멈춥니다", self.logs_channel.name)
                    self.logs_blocked = True
                self.dropped_logs = 0
                continue
            if self.logs_blocked:
                log.info("#%s 채널이 다시 비공개로 확인되어 로그 전송을 재개합니다", self.logs_channel.name)
                self.logs_blocked = False

            if self.dropped_logs:
                lines.append(f"… 로그가 너무 많아 {self.dropped_logs}줄을 생략했습니다 …")
                self.dropped_logs = 0

            for chunk in chunk_log_lines(lines):
                try:
                    await self.logs_channel.send(chunk, allowed_mentions=NO_MENTIONS)
                except discord.HTTPException as exc:
                    log.warning("#logs 전송 실패: %s", exc)

    # ── 디스코드 → 게임 ──

    async def on_message(self, message: discord.Message) -> None:
        if self.chat_channel is None or message.channel.id != self.chat_channel.id:
            return
        # 봇·웹훅 메시지(브리지가 올린 게임 채팅 포함)를 되돌려 보내면 무한 반복된다.
        if message.author.bot or message.webhook_id:
            return

        parts = [message.clean_content]
        for attachment in message.attachments:
            is_image = (attachment.content_type or "").startswith("image/")
            parts.append("[이미지]" if is_image else "[파일]")
        if message.stickers:
            parts.append("[스티커]")
        text = " ".join(part for part in parts if part.strip())
        if not text:
            return

        try:
            await self.rcon.command(build_tellraw(message.author.display_name, text))
        except RconError as exc:
            log.warning("게임으로 메시지를 전달하지 못했습니다: %s", exc)
            try:
                await message.add_reaction("❌")
            except discord.HTTPException:
                pass


if __name__ == "__main__":
    _setup_logging()
    _check_config()
    _instance_lock = _acquire_single_instance()
    try:
        BridgeBot().run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        _fatal("[오류] 브리지 봇 토큰이 올바르지 않습니다. .env의 BRIDGE_DISCORD_TOKEN을 확인하세요.")
    except discord.PrivilegedIntentsRequired:
        _fatal(
            "[오류] 디스코드 개발자 포털 > Bot 에서 'MESSAGE CONTENT INTENT' 를 켜주세요.",
            "       이 권한이 없으면 디스코드 메시지 내용을 읽을 수 없습니다.",
        )
    except Exception:
        log.exception("브리지가 예기치 못한 오류로 종료됩니다")
        raise
