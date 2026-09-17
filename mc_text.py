"""디스코드 메시지를 마인크래프트 tellraw 명령으로 안전하게 바꾼다.

사용자가 입력한 문자열이 RCON으로 서버 명령에 들어가므로, 명령 구조를 깨뜨리거나
다른 명령을 끼워 넣을 수 없어야 한다. 그래서 문자열을 이어 붙이지 않고
JSON 직렬화(json.dumps)로만 텍스트 컴포넌트를 만든다.
"""

from __future__ import annotations

import json
import re

DISCORD_COLOR = "#5865F2"
# 마인크래프트 RCON이 받는 명령은 최대 1446바이트. 여유를 둔다.
MAX_COMMAND_BYTES = 1400

CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):\d+>")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SPACES_RE = re.compile(r" {2,}")


def clean_text(text: str) -> str:
    """게임 채팅에 넣을 수 있게 정리한다."""
    text = CUSTOM_EMOJI_RE.sub(r":\1:", text)
    # § 는 마인크래프트 색상 코드 접두사. 디스코드에서 게임 채팅을 꾸미거나 위조하지 못하게 한다.
    text = text.replace("§", "")
    # 줄바꿈 등 제어문자는 공백으로. RCON 명령은 한 줄이어야 한다.
    text = CONTROL_RE.sub(" ", text)
    return SPACES_RE.sub(" ", text).strip()


def _render(name: str, message: str) -> str:
    components = [
        "",
        {"text": "[Discord] ", "color": DISCORD_COLOR},
        {"text": name, "color": "white"},
        {"text": f": {message}", "color": "white"},
    ]
    # ensure_ascii=False: 한글을 \uXXXX(6바이트) 대신 UTF-8(3바이트)로 보내 길이 제한을 아낀다.
    return "tellraw @a " + json.dumps(components, ensure_ascii=False)


def build_tellraw(name: str, message: str) -> str:
    """`[Discord] 이름: 메시지` 를 모든 플레이어에게 보내는 명령을 만든다.

    바이트 수 제한을 넘으면 메시지를 잘라 끝에 … 를 붙인다.
    """
    name = clean_text(name) or "알 수 없음"
    message = clean_text(message)

    command = _render(name, message)
    if len(command.encode("utf-8")) <= MAX_COMMAND_BYTES:
        return command

    # 한글·영문이 섞이면 글자당 바이트가 달라서, 들어가는 최대 길이를 이분 탐색으로 찾는다.
    low, high = 0, len(message)
    while low < high:
        mid = (low + high + 1) // 2
        if len(_render(name, message[:mid] + "…").encode("utf-8")) <= MAX_COMMAND_BYTES:
            low = mid
        else:
            high = mid - 1
    return _render(name, message[:low].rstrip() + "…")
