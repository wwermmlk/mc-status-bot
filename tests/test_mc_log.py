import asyncio
import os
import tempfile
import unittest

from mc_log import LogParser, LogTailer, mask_ips

FORGE = "[17Sep2026 12:34:56.789] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: "
VANILLA = "[12:34:56] [Server thread/INFO]: "


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = LogParser()

    def test_chat_forge_and_vanilla(self):
        for prefix in (FORGE, VANILLA):
            event = self.parser.parse(prefix + "<Steve> 안녕하세요")
            self.assertEqual(event.kind, "chat")
            self.assertEqual(event.name, "Steve")
            self.assertEqual(event.text, "안녕하세요")
            self.assertEqual(event.time, "12:34:56")

    def test_chat_not_secure_prefix(self):
        event = self.parser.parse(VANILLA + "[Not Secure] <Alex> hi")
        self.assertEqual((event.kind, event.name, event.text), ("chat", "Alex", "hi"))

    def test_chat_containing_angle_brackets(self):
        # 다른 사람 이름을 흉내 내도 실제 발신자는 앞의 이름이다.
        event = self.parser.parse(VANILLA + "<Steve> <Admin> 서버 닫습니다")
        self.assertEqual(event.name, "Steve")
        self.assertEqual(event.text, "<Admin> 서버 닫습니다")

    def test_join_leave_tracks_online(self):
        self.assertEqual(self.parser.parse(FORGE + "Steve joined the game").kind, "join")
        self.assertIn("Steve", self.parser.online)
        self.assertEqual(self.parser.parse(FORGE + "Steve left the game").kind, "leave")
        self.assertNotIn("Steve", self.parser.online)

    def test_death_only_for_online_players(self):
        line = FORGE + "Steve was slain by Zombie"
        self.assertEqual(self.parser.parse(line).kind, "other")  # 접속 기록 없음
        self.parser.parse(FORGE + "Steve joined the game")
        event = self.parser.parse(line)
        self.assertEqual(event.kind, "death")
        self.assertEqual(event.text, "Steve was slain by Zombie")

    def test_non_death_line_starting_with_player_name(self):
        self.parser.parse(FORGE + "Steve joined the game")
        for msg in ("Steve has made the advancement [Stone Age]", "Steve lost connection: Disconnected"):
            self.assertEqual(self.parser.parse(FORGE + msg).kind, "other", msg)

    def test_server_start_and_stop(self):
        self.parser.parse(FORGE + "Steve joined the game")
        start = FORGE.replace("MinecraftServer", "dedicated.DedicatedServer")
        self.assertEqual(self.parser.parse(start + 'Done (12.345s)! For help, type "help"').kind, "start")
        self.assertEqual(self.parser.parse(FORGE + "Stopping server").kind, "stop")
        self.assertEqual(self.parser.online, set())

    def test_other_threads_and_stacktraces_are_other(self):
        self.assertEqual(self.parser.parse("[12:34:56] [Worker-Main-1/INFO]: <Steve> fake").kind, "other")
        self.assertEqual(self.parser.parse("\tat java.base/java.lang.Thread.run(Thread.java:833)").kind, "other")

    # 실제 운영 서버(Forge 모드팩, 한국어 Windows)에서 찍힌 형식. 이름만 바꿈.
    KO_FORGE_ASYNC = "[189월2026 00:10:01.861] [ForkJoinPool.commonPool-worker-1/INFO] [net.minecraft.server.MinecraftServer/]: "
    KO_FORGE_SERVER = "[189월2026 00:11:05.217] [Server thread/INFO] [net.minecraft.server.MinecraftServer/]: "

    def test_styled_chat_from_async_thread(self):
        # 채팅 서식 모드가 이름 뒤에 기호(»)를 붙였고, cp949 로그에서 "?" 로 저장됨
        event = self.parser.parse(self.KO_FORGE_ASYNC + " Steve ? 아 된다")
        self.assertEqual((event.kind, event.name, event.text, event.time), ("chat", "Steve", "아 된다", "00:10:01"))
        self.assertEqual(self.parser.parse(self.KO_FORGE_ASYNC + " Alex_01 » 네").text, "네")

    def test_styled_chat_text_keeps_question_marks(self):
        event = self.parser.parse(self.KO_FORGE_ASYNC + " Steve ? 이거 쓰고 다시 받을수 잇을까요?")
        self.assertEqual(event.text, "이거 쓰고 다시 받을수 잇을까요?")

    def test_real_server_lines_that_are_not_chat(self):
        self.parser.parse(FORGE + "Steve joined the game")
        for msg in (
            "Can't keep up! Is the server overloaded? Running 2005ms or 40 ticks behind",
            "[Steve: Killed Steve]",
            "Steve has reached the goal [Sky's the Limit]",
            "There are 6 of a max of 20 players online: Steve, Alex",
        ):
            self.assertNotEqual(self.parser.parse(self.KO_FORGE_SERVER + msg).kind, "chat", msg)

    def test_styled_chat_only_from_minecraft_server_logger(self):
        other_mod = "[189월2026 00:10:01.861] [ForkJoinPool.commonPool-worker-1/INFO] [SomeMod/]: "
        self.assertEqual(self.parser.parse(other_mod + " Steve ? 가짜").kind, "other")

    def test_kill_command_death(self):
        self.parser.parse(FORGE + "Steve joined the game")
        event = self.parser.parse(self.KO_FORGE_SERVER + "Steve was killed")
        self.assertEqual((event.kind, event.text), ("death", "Steve was killed"))

    def test_mask_ips(self):
        # 203.0.113.0/24 는 문서·예시용으로 예약된 IP 대역
        line = FORGE + "Steve[/203.0.113.45:51234] logged in with entity id 123 at (1.5, 64.0, -3.2)"
        masked = mask_ips(line)
        self.assertNotIn("203.0.113.45", masked)
        self.assertIn("Steve[/[IP 숨김]]", masked)
        self.assertIn("(1.5, 64.0, -3.2)", masked)  # 좌표는 건드리지 않음


class TailerTest(unittest.TestCase):
    def test_skips_existing_follows_new_and_reopens_on_rotation(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "latest.log")
                with open(path, "wb") as f:
                    f.write(b"old line\n")

                tailer = LogTailer(path, poll_seconds=0.02)
                lines = tailer.lines()
                got = []

                async def collect(n):
                    while len(got) < n:
                        got.append(await asyncio.wait_for(anext(lines), 2))

                # 첫 읽기를 시작시켜 기존 내용 위치를 기억하게 한다.
                reader = asyncio.ensure_future(collect(1))
                await asyncio.sleep(0.1)
                with open(path, "ab") as f:
                    f.write("새 줄 한글\r\npar".encode("utf-8"))
                await asyncio.sleep(0.1)
                with open(path, "ab") as f:
                    f.write(b"tial\n")
                await reader
                await collect(2)

                # 서버 재시작: 기존 파일을 옮기고 새 파일 생성
                os.replace(path, os.path.join(tmp, "old.log"))
                with open(path, "wb") as f:
                    f.write(b"after restart\n")
                await collect(3)
                return got

        self.assertEqual(asyncio.run(scenario()), ["새 줄 한글", "partial", "after restart"])

    def test_cp949_fallback(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "latest.log")
                lines = LogTailer(path, poll_seconds=0.02).lines()
                task = asyncio.ensure_future(asyncio.wait_for(anext(lines), 2))
                await asyncio.sleep(0.1)
                with open(path, "wb") as f:  # 봇 시작 후 생긴 파일 → 처음부터 읽음
                    f.write("<Steve> 안녕\n".encode("cp949"))
                return await task

        self.assertEqual(asyncio.run(scenario()), "<Steve> 안녕")


if __name__ == "__main__":
    unittest.main()
