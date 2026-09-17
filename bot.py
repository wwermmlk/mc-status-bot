"""마인크래프트 서버 상태를 디스코드 채널에 실시간 표시하는 봇.

지정 채널에 상태 메시지 하나를 올려두고, 일정 주기마다 그 메시지를 '수정'해서 갱신한다.
새 메시지를 계속 보내지 않으므로 채널이 지저분해지지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from mc_query import ServerInfo, fetch_server_info
from site_info import HeroTextCache

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mc-status-bot")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
MC_HOST = os.getenv("MC_HOST", "").strip()
MC_PORT = int(os.getenv("MC_PORT", "25565"))
MC_QUERY_PORT = int(os.getenv("MC_QUERY_PORT", str(MC_PORT)))
GUILD_ID = os.getenv("GUILD_ID", "").strip()
STATUS_CHANNEL_ID = os.getenv("STATUS_CHANNEL_ID", "").strip()
UPDATE_INTERVAL = max(15, int(os.getenv("UPDATE_INTERVAL", "30")))
PRESENCE_UPDATE = os.getenv("PRESENCE_UPDATE", "true").strip().lower() != "false"
# 봇 프로필에 표시되는 앱 설명. 비워두면 건드리지 않는다.
BOT_DESCRIPTION = os.getenv("BOT_DESCRIPTION", "").strip().replace("\\n", "\n")
SITE_URL = os.getenv("SITE_URL", "").strip()
SITE_CACHE_SECONDS = float(os.getenv("SITE_CACHE_SECONDS", "600"))
SITE_LINK_TEXT = os.getenv("SITE_LINK_TEXT", "🏡 서버 홈페이지").strip()
# 봇이 돌고 있는 위치. 핑이 어디서 잰 값인지 밝히는 데 쓴다.
BOT_LOCATION = os.getenv("BOT_LOCATION", "오사카").strip()

# 홈페이지 대표 문구. 임베드 설명으로 쓰고, 못 가져오면 서버 MOTD로 돌아간다.
hero_text = HeroTextCache(SITE_URL, SITE_CACHE_SECONDS)

# 디스코드 임베드 필드 1개는 1024자 제한이 있으므로 표시 인원을 제한한다.
MAX_SHOWN_PLAYERS = 40
# 갱신할 메시지 ID를 저장해 재시작해도 같은 메시지를 계속 쓴다.
STATE_FILE = Path(__file__).with_name(".bot_state.json")


def _check_config() -> None:
    missing = []
    if not TOKEN:
        missing.append("DISCORD_TOKEN (디스코드 봇 토큰)")
    if not MC_HOST:
        missing.append("MC_HOST (마인크래프트 서버 주소)")
    if not STATUS_CHANNEL_ID:
        missing.append("STATUS_CHANNEL_ID (상태를 표시할 채널 ID)")
    if missing:
        print("[설정 오류] .env 파일에 다음 값이 필요합니다:", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        print("\n.env.example 을 복사해 .env 로 만든 뒤 값을 채워주세요.", file=sys.stderr)
        raise SystemExit(1)


def load_message_id() -> int | None:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # 채널이 바뀌었으면 기존 메시지는 버리고 새로 올린다.
    if str(data.get("channel_id")) != STATUS_CHANNEL_ID:
        return None
    return data.get("message_id")


def save_message_id(message_id: int) -> None:
    STATE_FILE.write_text(
        json.dumps({"channel_id": STATUS_CHANNEL_ID, "message_id": message_id}),
        encoding="utf-8",
    )


def _describe(headline: str | None, fallback: str | None) -> str | None:
    """임베드 설명을 만든다. 홈페이지가 설정돼 있으면 링크 줄을 덧붙인다.

    디스코드는 제목과 푸터에서는 마크다운 링크를 무시하므로 설명에 넣어야 한다.
    """
    lines = [line for line in (headline or fallback, ) if line]
    if SITE_URL and SITE_LINK_TEXT:
        lines.append(f"[{SITE_LINK_TEXT}]({SITE_URL})")
    return "\n".join(lines) or None


def build_embed(info: ServerInfo, headline: str | None = None) -> discord.Embed:
    """headline 이 있으면 설명으로 쓰고, 없으면 서버 MOTD로 돌아간다."""
    address = f"{MC_HOST}:{MC_PORT}" if MC_PORT != 25565 else MC_HOST
    now = datetime.now(timezone.utc)
    # 디스코드가 클라이언트에서 "N초 전"으로 계속 세어주므로 갱신 사이에도 살아있게 보인다.
    updated = f"<t:{int(now.timestamp())}:R>"

    if not info.online:
        embed = discord.Embed(
            title=f"🔴 {address}",
            description=_describe(
                None, "서버에 연결할 수 없습니다. 서버가 꺼져 있거나 주소/포트가 잘못되었을 수 있습니다."
            ),
            color=discord.Color.red(),
            timestamp=now,
        )
        embed.add_field(name="상태", value="🔴 오프라인", inline=True)
        embed.add_field(name="마지막 확인", value=updated, inline=True)
        if info.error:
            embed.add_field(name="원인", value=f"```{info.error[:200]}```", inline=False)
        embed.set_footer(text=f"{UPDATE_INTERVAL}초마다 자동 갱신")
        return embed

    embed = discord.Embed(
        title=f"🟢 {address}",
        description=_describe(headline, info.motd),
        color=discord.Color.green(),
        timestamp=now,
    )
    embed.add_field(
        name="접속 인원",
        value=f"**{info.players_online}** / {info.players_max} 명",
        inline=True,
    )
    # 이 값은 서버 성능(TPS)이 아니라 봇에서 서버까지의 왕복 시간이다.
    # 오해하기 쉬워서 이름과 푸터로 분명히 밝힌다.
    embed.add_field(name="핑", value=f"{info.latency_ms} ms", inline=True)
    embed.add_field(name="마지막 갱신", value=updated, inline=True)

    names = info.player_names
    shown = names[:MAX_SHOWN_PLAYERS]
    parts = [f"`{n}`" for n in shown]
    hidden = len(names) - len(shown)
    if hidden > 0:
        parts.append(f"외 {hidden}명")
    if info.anonymous_count:
        # 닉네임 공개를 끈 플레이어. 이름이 모두 같아 목록으로는 셀 수 없으므로 수로 표시한다.
        parts.append(f"_익명 {info.anonymous_count}명_")
    value = ", ".join(parts) if parts else "_아무도 접속해 있지 않습니다_"
    embed.add_field(name="접속자", value=value, inline=False)

    footer = f"{UPDATE_INTERVAL}초마다 자동 갱신 · 핑 = {BOT_LOCATION} 봇 서버 ↔ 마크 서버 왕복 시간"
    if info.anonymous_count:
        footer += " · 익명은 게임 설정에서 서버 목록 표시를 끈 사람"
    if info.source != "query":
        footer += " · Query 설정 시 전체 닉네임 표시"
    embed.set_footer(text=footer)
    return embed


class SharedCommandTree(app_commands.CommandTree):
    """같은 봇 계정을 쓰는 다른 프로그램(채팅 브리지)의 명령어를 오류로 기록하지 않는 트리."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandNotFound):
            return  # 다른 프로그램이 처리하는 명령어
        await super().on_error(interaction, error)


class StatusBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.tree = SharedCommandTree(self)
        self.board_message: discord.Message | None = None

    async def setup_hook(self) -> None:
        # tree.sync() 는 봇의 명령어 목록을 이 프로그램의 것으로 '통째로 덮어써서',
        # 같은 봇 계정으로 채팅 브리지가 등록한 /채팅날짜 를 지워버린다.
        # 그래서 이 프로그램의 명령어만 하나씩 추가·갱신(upsert)한다.
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            for command in self.tree.get_commands(guild=guild):
                await self.http.upsert_guild_command(self.application_id, guild.id, command.to_dict(self.tree))
            log.info("길드(%s)에 슬래시 커맨드 등록 완료", GUILD_ID)
        else:
            for command in self.tree.get_commands():
                await self.http.upsert_global_command(self.application_id, command.to_dict(self.tree))
            log.info("글로벌 슬래시 커맨드 등록 완료 (반영까지 최대 1시간)")

    async def on_ready(self) -> None:
        log.info("로그인: %s (id=%s)", self.user, self.user.id)
        log.info("대상 서버: %s:%s (query %s)", MC_HOST, MC_PORT, MC_QUERY_PORT)
        await self.sync_description()
        if not live_board.is_running():
            live_board.start()

    async def sync_description(self) -> None:
        """봇 프로필 설명을 .env 값과 맞춘다 (달라졌을 때만 수정)."""
        if not BOT_DESCRIPTION:
            return
        try:
            app = await self.application_info()
            if (app.description or "") == BOT_DESCRIPTION:
                return
            await app.edit(description=BOT_DESCRIPTION)
            log.info("봇 프로필 설명을 갱신했습니다.")
        except discord.HTTPException as exc:
            log.warning("봇 프로필 설명 갱신 실패: %s", exc)

    async def resolve_board_message(self) -> discord.Message | None:
        """갱신할 상태 메시지를 찾거나 새로 만든다."""
        if self.board_message is not None:
            return self.board_message

        channel = self.get_channel(int(STATUS_CHANNEL_ID))
        if channel is None:
            try:
                channel = await self.fetch_channel(int(STATUS_CHANNEL_ID))
            except (discord.NotFound, discord.Forbidden) as exc:
                log.error(
                    "채널(%s)에 접근할 수 없습니다: %s — 채널 ID와 봇 권한을 확인하세요.",
                    STATUS_CHANNEL_ID,
                    exc,
                )
                return None

        message_id = load_message_id()
        if message_id:
            try:
                self.board_message = await channel.fetch_message(message_id)
                log.info("기존 상태 메시지(%s)를 이어서 갱신합니다.", message_id)
                return self.board_message
            except discord.NotFound:
                log.info("기존 상태 메시지가 삭제되어 새로 올립니다.")
            except discord.Forbidden:
                log.warning(
                    "메시지를 읽을 권한이 없습니다. 봇에 '메시지 기록 보기' 권한을 주세요. 새로 올립니다."
                )

        try:
            self.board_message = await channel.send(
                embed=build_embed(ServerInfo(error="첫 조회 준비 중"))
            )
        except discord.Forbidden:
            log.error("채널에 메시지를 보낼 권한이 없습니다. 봇 권한을 확인하세요.")
            return None
        save_message_id(self.board_message.id)
        log.info("상태 메시지를 새로 올렸습니다: %s", self.board_message.id)
        return self.board_message


client = StatusBot()


@tasks.loop(seconds=UPDATE_INTERVAL)
async def live_board() -> None:
    """서버를 조회해 상태 메시지와 봇 프로필을 갱신한다.

    예상 못 한 예외가 밖으로 새어나가면 tasks 루프가 통째로 멈추고 보드가 얼어붙는다.
    그래서 전체를 감싸 어떤 오류가 나도 다음 주기에 다시 시도하게 한다.
    """
    try:
        await _update_once()
    except Exception:  # noqa: BLE001 - 루프를 절대 죽이지 않는다
        log.exception("갱신 중 예기치 못한 오류 (다음 주기에 재시도합니다)")


async def _update_once() -> None:
    info = await fetch_server_info(MC_HOST, MC_PORT, MC_QUERY_PORT)
    headline = await hero_text.get()

    message = await client.resolve_board_message()
    if message is not None:
        try:
            await message.edit(embed=build_embed(info, headline))
        except discord.NotFound:
            # 메시지가 지워졌으면 다음 주기에 새로 만든다.
            client.board_message = None
            log.info("상태 메시지가 삭제되었습니다. 다음 갱신에 새로 올립니다.")
        except discord.HTTPException as exc:
            log.warning("상태 메시지 갱신 실패: %s", exc)

    if PRESENCE_UPDATE:
        if info.online:
            text = f"{info.players_online}명 온라인"
            presence = discord.Status.online
        else:
            text = "서버 오프라인"
            presence = discord.Status.dnd
        try:
            # discord.Game 은 "플레이 중 ~" 으로 표시된다.
            await client.change_presence(status=presence, activity=discord.Game(name=text))
        except discord.HTTPException as exc:
            log.warning("프로필 상태 갱신 실패: %s", exc)


@live_board.before_loop
async def _before_board() -> None:
    await client.wait_until_ready()


@client.tree.command(name="status", description="마인크래프트 서버의 현재 접속 현황을 확인합니다.")
async def status_command(interaction: discord.Interaction) -> None:
    """상태 보드를 기다리지 않고 바로 확인하고 싶을 때 쓰는 보조 명령어."""
    await interaction.response.defer(ephemeral=True)
    info = await fetch_server_info(MC_HOST, MC_PORT, MC_QUERY_PORT)
    headline = await hero_text.get()
    await interaction.followup.send(embed=build_embed(info, headline), ephemeral=True)


if __name__ == "__main__":
    _check_config()
    try:
        client.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        print("[오류] 디스코드 토큰이 올바르지 않습니다. .env의 DISCORD_TOKEN을 확인하세요.", file=sys.stderr)
        raise SystemExit(1)
