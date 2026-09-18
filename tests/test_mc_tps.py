import asyncio
import unittest

from mc_tps import TpsInfo, fetch_tps, parse_tps
from rcon import RconError

FORGE = (
    "Overall: Mean tick time: 4.031 ms. Mean TPS: 20.000\n"
    "Dim minecraft:overworld: Mean tick time: 2.507 ms. Mean TPS: 20.000\n"
    "Dim minecraft:the_nether: Mean tick time: 0.512 ms. Mean TPS: 20.000"
)
FORGE_LAGGING = "Overall: Mean tick time: 78.412 ms. Mean TPS: 12.754"
PAPER = "§6TPS from last 1m, 5m, 15m: §a20.0, §a19.94, §a20.0"


class ParseTpsTest(unittest.TestCase):
    def test_forge_uses_overall_line(self):
        self.assertEqual(parse_tps(FORGE), TpsInfo(20.0, 4.031))

    def test_forge_without_overall_line(self):
        single = "Dim minecraft:overworld: Mean tick time: 9.5 ms. Mean TPS: 18.2"
        self.assertEqual(parse_tps(single), TpsInfo(18.2, 9.5))

    def test_forge_lagging(self):
        self.assertEqual(parse_tps(FORGE_LAGGING), TpsInfo(12.754, 78.412))

    def test_paper_with_color_codes(self):
        self.assertEqual(parse_tps(PAPER), TpsInfo(20.0))

    def test_tps_capped_at_20(self):
        # 서버가 20.06 처럼 반올림된 값을 주기도 한다
        self.assertEqual(parse_tps("Overall: Mean tick time: 1.0 ms. Mean TPS: 20.06").tps, 20.0)

    def test_unknown_output(self):
        for text in ("", "Unknown command", "There are 3 of a max of 20 players online: a, b, c"):
            self.assertIsNone(parse_tps(text), text)


class EmbedTest(unittest.TestCase):
    def test_format_tps_signal(self):
        from bot import format_tps

        self.assertEqual(format_tps(TpsInfo(20.0, 4.031)), "🟢 20.0 / 20 (4.0 ms/틱)")
        self.assertTrue(format_tps(TpsInfo(17.2, 55.0)).startswith("🟡"))
        self.assertTrue(format_tps(TpsInfo(9.4, 105.0)).startswith("🔴"))
        self.assertEqual(format_tps(TpsInfo(20.0)), "🟢 20.0 / 20")

    def test_field_only_added_when_tps_known(self):
        from bot import build_embed
        from mc_query import ServerInfo

        info = ServerInfo(online=True, players_online=3, players_max=20, latency_ms=8.0)
        names = [f.name for f in build_embed(info).fields]
        self.assertNotIn("서버 성능 (TPS)", names)
        with_tps = [f for f in build_embed(info, None, TpsInfo(20.0, 3.0)).fields]
        self.assertIn("서버 성능 (TPS)", [f.name for f in with_tps])

    def test_private_host_check(self):
        from bot import is_private_host

        for host in ("127.0.0.1", "192.168.0.21", "10.0.0.5", "100.101.102.103"):
            self.assertTrue(is_private_host(host), host)
        # 203.0.113.x 같은 문서용 대역은 파이썬이 사설망으로 분류하므로 공인 주소로 확인한다
        for host in ("8.8.8.8", "93.184.216.34", "example.com"):
            self.assertFalse(is_private_host(host), host)


class FetchTpsTest(unittest.TestCase):
    class FakeRcon:
        def __init__(self, replies):
            self.replies = replies
            self.sent = []

        async def command(self, command):
            self.sent.append(command)
            reply = self.replies.get(command)
            if isinstance(reply, Exception):
                raise reply
            return reply if reply is not None else "Unknown or incomplete command"

    def test_forge_first(self):
        rcon = self.FakeRcon({"forge tps": FORGE})
        self.assertEqual(asyncio.run(fetch_tps(rcon)), TpsInfo(20.0, 4.031))
        self.assertEqual(rcon.sent, ["forge tps"])

    def test_falls_back_to_vanilla_command(self):
        rcon = self.FakeRcon({"tps": PAPER})
        self.assertEqual(asyncio.run(fetch_tps(rcon)), TpsInfo(20.0))
        self.assertEqual(rcon.sent, ["forge tps", "tps"])

    def test_no_command_works(self):
        rcon = self.FakeRcon({})
        self.assertIsNone(asyncio.run(fetch_tps(rcon)))

    def test_rcon_failure_returns_none(self):
        rcon = self.FakeRcon({"forge tps": RconError("서버 꺼짐")})
        self.assertIsNone(asyncio.run(fetch_tps(rcon)))
        self.assertEqual(rcon.sent, ["forge tps"])  # 연결이 안 되면 두 번째 명령은 시도하지 않음


if __name__ == "__main__":
    unittest.main()
