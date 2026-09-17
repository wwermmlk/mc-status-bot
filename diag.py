"""봇의 채널 권한을 진단하는 스크립트.

    python diag.py          상태 봇 (STATUS_CHANNEL_ID)
    python diag.py bridge   채팅 브리지 (#chat, #logs)

어느 단계에서 권한이 막히는지, #logs 가 방장 전용인지 보여준다.
"""

import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

load_dotenv()

LABELS = {
    "view_channel": "채널 보기",
    "send_messages": "메시지 보내기",
    "embed_links": "링크 첨부(임베드)",
    "read_message_history": "메시지 기록 보기",
    "add_reactions": "반응 추가",
    "manage_webhooks": "웹훅 관리",
}


def mark(value):
    return "O" if value else "X"


def show_overwrite(label, overwrite, attrs):
    bits = []
    for attr in attrs:
        val = getattr(overwrite, attr)
        if val is not None:
            bits.append(f"{LABELS[attr]}={'허용' if val else '거부'}")
    print(f"    {label}: {', '.join(bits) if bits else '(지정 없음)'}")


async def inspect(client: discord.Client, channel_id: int, title: str, attrs: list[str]) -> discord.TextChannel | None:
    print(f"\n━━━━━━━━ {title} ━━━━━━━━")
    try:
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
    except Exception as exc:  # noqa: BLE001
        print(f"채널 조회 실패: {type(exc).__name__}: {exc}")
        return None

    guild = channel.guild
    me = guild.me
    print(f"서버     : {guild.name}")
    print(f"채널     : #{channel.name} ({type(channel).__name__})")
    print(f"카테고리 : {channel.category.name if channel.category else '(없음)'}")
    print(f"봇 역할  : {', '.join(r.name for r in me.roles)}")

    print("\n[최종 권한]")
    perms = channel.permissions_for(me)
    for attr in attrs:
        print(f"  {mark(getattr(perms, attr))}  {LABELS[attr]}")

    print(f"\n[채널 '#{channel.name}' 권한 덮어쓰기]")
    if not channel.overwrites:
        print("    (설정된 덮어쓰기 없음 — 카테고리/역할 권한을 그대로 따름)")
    for target, ow in channel.overwrites.items():
        relevant = target == guild.default_role or target == me or target in me.roles
        label = getattr(target, "name", str(target))
        show_overwrite(f"{label}{' ← 봇에 적용' if relevant else ''}", ow, attrs)
    return channel


class Diag(discord.Client):
    def __init__(self, mode: str) -> None:
        super().__init__(intents=discord.Intents.default())
        self.mode = mode

    async def on_ready(self):
        try:
            if self.mode == "bridge":
                await self.run_bridge()
            else:
                await inspect(
                    self,
                    int(os.getenv("STATUS_CHANNEL_ID")),
                    "상태 보드 채널",
                    ["view_channel", "send_messages", "embed_links", "read_message_history"],
                )
        finally:
            await self.close()

    async def run_bridge(self):
        # bridge.py 와 같은 판정 로직을 그대로 사용한다.
        from bridge import check_logs_privacy

        await inspect(
            self,
            int(os.getenv("BRIDGE_CHAT_CHANNEL_ID")),
            "채팅 채널",
            ["view_channel", "send_messages", "read_message_history", "add_reactions", "manage_webhooks"],
        )
        logs_id = os.getenv("BRIDGE_LOGS_CHANNEL_ID", "").strip()
        if not logs_id:
            print("\n(BRIDGE_LOGS_CHANNEL_ID 가 비어 있어 로그 채널 점검을 건너뜁니다)")
            return
        logs = await inspect(
            self, int(logs_id), "로그 채널", ["view_channel", "send_messages", "read_message_history"]
        )
        if logs is None:
            return
        private, warnings = check_logs_privacy(logs)
        print("\n[방장 전용 여부]")
        print(f"  {mark(private)}  @everyone 차단" + ("" if private else "  ← 이 상태에선 브리지가 로그를 보내지 않습니다"))
        for warning in warnings:
            print(f"  !  {warning}")
        if private and not warnings:
            print("  O  방장(서버 소유자)과 관리자 권한 역할 외에는 볼 수 없습니다")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    token_key = "BRIDGE_DISCORD_TOKEN" if mode == "bridge" else "DISCORD_TOKEN"
    token = os.getenv(token_key, "").strip()
    if not token:
        raise SystemExit(f".env 에 {token_key} 가 없습니다.")
    asyncio.run(Diag(mode).start(token))
