import unittest

from bridge import LOG_MESSAGE_LIMIT, chunk_log_lines, format_event, webhook_name_allowed
from mc_log import LogEvent


class FormatEventTest(unittest.TestCase):
    def test_messages(self):
        self.assertEqual(format_event(LogEvent("join", "12:34", "Steve")), "`12:34` ➕ **Steve**님이 접속했습니다")
        self.assertEqual(format_event(LogEvent("leave", "12:35", "Steve")), "`12:35` ➖ **Steve**님이 퇴장했습니다")
        self.assertEqual(format_event(LogEvent("start", "09:00")), "`09:00` ✅ 서버가 열렸습니다")
        self.assertEqual(format_event(LogEvent("stop", "23:59")), "`23:59` ⛔ 서버가 닫혔습니다")
        self.assertIsNone(format_event(LogEvent("other")))

    def test_underscore_names_do_not_become_italic(self):
        self.assertIn(r"**Steve\_\_1**", format_event(LogEvent("join", "12:00", "Steve__1")))

    def test_death_text_is_escaped(self):
        message = format_event(LogEvent("death", "12:00", "Steve", "Steve was slain by *Boss*"))
        self.assertEqual(message, r"`12:00` ☠️ Steve was slain by \*Boss\*")


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
