"""봇의 채널 권한을 진단하는 스크립트.

python diag.py 로 실행하면 어느 단계에서 권한이 막히는지 보여준다.
"""

import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.getenv("STATUS_CHANNEL_ID"))

CHECKS = [
    ("채널 보기", "view_channel"),
    ("메시지 보내기", "send_messages"),
    ("링크 첨부(임베드)", "embed_links"),
    ("메시지 기록 보기", "read_message_history"),
]


def mark(value):
    return "O" if value else "X"


def show_overwrite(label, overwrite):
    bits = []
    for name, attr in CHECKS:
        val = getattr(overwrite, attr)
        if val is not None:
            bits.append(f"{name}={'허용' if val else '거부'}")
    print(f"    {label}: {', '.join(bits) if bits else '(지정 없음)'}")


class Diag(discord.Client):
    async def on_ready(self):
        try:
            channel = self.get_channel(CHANNEL_ID) or await self.fetch_channel(CHANNEL_ID)
        except Exception as exc:  # noqa: BLE001
            print(f"채널 조회 실패: {type(exc).__name__}: {exc}")
            await self.close()
            return

        guild = channel.guild
        me = guild.me
        print(f"서버     : {guild.name}")
        print(f"채널     : #{channel.name} ({type(channel).__name__})")
        print(f"카테고리 : {channel.category.name if channel.category else '(없음)'}")
        print(f"봇 역할  : {', '.join(r.name for r in me.roles)}")
        print(f"서버 소유자 여부 : {me.id == guild.owner_id}")

        print("\n[최종 권한]")
        perms = channel.permissions_for(me)
        for name, attr in CHECKS:
            print(f"  {mark(getattr(perms, attr))}  {name}")

        print("\n[역할 자체 권한 (서버 전체)]")
        for role in me.roles:
            bits = [f"{n}={mark(getattr(role.permissions, a))}" for n, a in CHECKS]
            print(f"  {role.name}: {', '.join(bits)}")

        if channel.category:
            print(f"\n[카테고리 '{channel.category.name}' 권한 덮어쓰기]")
            found = False
            for target, ow in channel.category.overwrites.items():
                if target == guild.default_role or target == me or target in me.roles:
                    show_overwrite(getattr(target, "name", str(target)), ow)
                    found = True
            if not found:
                print("    (봇에 해당하는 항목 없음)")

        print(f"\n[채널 '#{channel.name}' 권한 덮어쓰기]")
        if not channel.overwrites:
            print("    (설정된 덮어쓰기 없음 — 카테고리/역할 권한을 그대로 따름)")
        for target, ow in channel.overwrites.items():
            relevant = target == guild.default_role or target == me or target in me.roles
            label = getattr(target, "name", str(target))
            show_overwrite(f"{label}{' ← 봇에 적용' if relevant else ''}", ow)

        await self.close()


asyncio.run(Diag(intents=discord.Intents.default()).start(TOKEN))
