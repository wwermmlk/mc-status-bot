import unittest
from datetime import datetime

from bridge import LOG_MESSAGE_LIMIT, chunk_log_lines, format_event, make_stamp, webhook_name_allowed
from mc_log import LogEvent

STAMP = "`[12:34:56]` "


class StampTest(unittest.TestCase):
    def test_time_only(self):
        self.assertEqual(make_stamp("00:18:37", show_date=False), "`[00:18:37]` ")

    def test_with_date(self):
        now = datetime(2026, 9, 18, 0, 18, 40)
        self.assertEqual(make_stamp("00:18:37", True, now), "`[2026-09-18 00:18:37]` ")

    def test_date_rolls_back_just_after_midnight(self):
        # 23:59:58 에 찍힌 로그를 자정 직후에 처리하면 어제 날짜여야 한다
        now = datetime(2026, 9, 18, 0, 0, 3)
        self.assertEqual(make_stamp("23:59:58", True, now), "`[2026-09-17 23:59:58]` ")

    def test_small_clock_skew_is_not_yesterday(self):
        # 서버 PC 시계가 몇 초 빨라도 날짜가 바뀌면 안 된다
        now = datetime(2026, 9, 18, 15, 0, 0)
        self.assertEqual(make_stamp("15:00:04", True, now), "`[2026-09-18 15:00:04]` ")

    def test_missing_time(self):
        self.assertEqual(make_stamp("", True), "")


class FormatEventTest(unittest.TestCase):
    def test_messages(self):
        self.assertEqual(format_event(LogEvent("join", "12:34:56", "Steve"), STAMP), "`[12:34:56]` ➕ **Steve**님이 접속했습니다")
        self.assertEqual(format_event(LogEvent("leave", "12:34:56", "Steve"), STAMP), "`[12:34:56]` ➖ **Steve**님이 퇴장했습니다")
        self.assertEqual(format_event(LogEvent("start"), STAMP), "`[12:34:56]` ✅ 서버가 열렸습니다")
        self.assertEqual(format_event(LogEvent("stop"), STAMP), "`[12:34:56]` ⛔ 서버가 닫혔습니다")
        self.assertIsNone(format_event(LogEvent("other"), STAMP))

    def test_underscore_names_do_not_become_italic(self):
        self.assertIn(r"**Steve\_\_1**", format_event(LogEvent("join", name="Steve__1"), STAMP))

    def test_discord_echo_line(self):
        message = format_event(LogEvent("discord", name="서버장", text="점검 *10분* 뒤"), STAMP)
        self.assertEqual(message, r"`[12:34:56]` [Discord] 서버장: 점검 \*10분\* 뒤")

    def test_death_text_is_escaped(self):
        message = format_event(LogEvent("death", name="Steve", text="Steve was slain by *Boss*"), STAMP)
        self.assertEqual(message, r"`[12:34:56]` ☠️ Steve was slain by \*Boss\*")


class WebhookNameTest(unittest.TestCase):
    def test_forbidden_words(self):
        self.assertTrue(webhook_name_allowed("Steve"))
        self.assertFalse(webhook_name_allowed("DiscordFan"))
        self.assertFalse(webhook_name_allowed("clyde_01"))


class ChunkLogLinesTest(unittest.TestCase):
    def test_every_message_fits_discord_limit(self):
        lines = [f"[12:00:00] [Server thread/INFO]: line {i} " + "x" * 80 for i in range(500)]
        chunks = chunk_log_lines(lines)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)
            self.assertTrue(chunk.startswith("```\n") and chunk.endswith("\n```"))
        # 줄이 빠지거나 순서가 바뀌지 않음
        rebuilt = "\n".join(c[4:-4] for c in chunks).split("\n")
        self.assertEqual(rebuilt, lines)

    def test_overlong_line_is_cut(self):
        [chunk] = chunk_log_lines(["y" * 5000])
        self.assertLessEqual(len(chunk), 2000)
        self.assertLessEqual(len(chunk[4:-4]), LOG_MESSAGE_LIMIT)

    def test_backticks_cannot_close_code_block(self):
        [chunk] = chunk_log_lines(["evil ``` @everyone"])
        self.assertEqual(chunk.count("```"), 2)


if __name__ == "__main__":
    unittest.main()
