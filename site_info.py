"""서버 홈페이지에서 대표 문구(강조 줄)를 가져온다.

홈페이지는 Next.js로 만들어져 있고 별도 API가 없어서, 페이지에 박혀 있는
heroHighlight 값을 읽어온다. 문구는 자주 바뀌지 않으므로 일정 시간 캐시한다.
"""

from __future__ import annotations

import logging
import re
import time

import aiohttp

log = logging.getLogger("mc-status-bot")

TIMEOUT = 5.0

# 홈페이지 구조가 바뀔 수 있으므로 위에서부터 순서대로 시도한다.
PATTERNS = (
    # Next.js가 페이지에 심어두는 데이터 (따옴표가 이스케이프된 형태 포함)
    r'\\?"heroHighlight\\?"\s*:\s*\\?"(.*?)\\?"',
    # 실제로 렌더링된 강조 문구 (위가 실패했을 때의 대비책)
    r'<span class="text-\[#0071e3\]">(.*?)</span>',
)


class HeroTextCache:
    """홈페이지 문구를 캐시한다. 30초마다 홈페이지를 두드리지 않기 위함."""

    def __init__(self, url: str, ttl: float = 600.0) -> None:
        self.url = url
        self.ttl = ttl
        self._text: str | None = None
        self._fetched_at: float = 0.0

    async def get(self) -> str | None:
        """캐시된 문구를 돌려준다. 만료됐으면 새로 가져온다.

        가져오기에 실패하면 마지막으로 성공한 값을 그대로 쓴다.
        홈페이지가 잠깐 죽었다고 디스코드 표시까지 깨질 이유는 없다.
        """
        if not self.url:
            return None
        if self._text is not None and time.monotonic() - self._fetched_at < self.ttl:
            return self._text

        text = await self._fetch()
        if text:
            self._text = text
            self._fetched_at = time.monotonic()
        elif self._text is None:
            # 한 번도 성공한 적이 없으면 잠시 뒤 다시 시도하도록 짧게만 쉰다.
            self._fetched_at = time.monotonic() - self.ttl + 60
        return self._text

    async def _fetch(self) -> str | None:
        try:
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.url) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
        except Exception as exc:  # noqa: BLE001 - 홈페이지 장애가 봇에 번지면 안 됨
            log.warning("홈페이지 문구를 가져오지 못했습니다: %s", exc)
            return None

        for pattern in PATTERNS:
            match = re.search(pattern, html)
            if match:
                value = match.group(1).strip()
                if value:
                    return value
        log.warning("홈페이지에서 대표 문구를 찾지 못했습니다. 구조가 바뀌었을 수 있습니다.")
        return None


if __name__ == "__main__":
    import asyncio
    import os

    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("SITE_URL", "").strip()
    print(f"주소   : {url or '(설정 안 됨)'}")
    print(f"대표 문구: {asyncio.run(HeroTextCache(url).get()) or '(가져오지 못함)'}")
