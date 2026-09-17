import json
import unittest

from mc_text import DISCORD_COLOR, MAX_COMMAND_BYTES, build_tellraw, clean_text

PREFIX = "tellraw @a "


def components(command: str):
    """명령이 올바른 형식인지 확인하고 JSON 부분을 파싱해 돌려준다."""
    assert command.startswith(PREFIX), command
    body = command[len(PREFIX):]
    assert "\n" not in command and "\r" not in command
    return json.loads(body)


class TellrawTest(unittest.TestCase):
    def test_basic_format_and_color(self):
        parts = components(build_tellraw("디코유저", "안녕하세요"))
        self.assertEqual(parts[1], {"text": "[Discord] ", "color": DISCORD_COLOR})
        self.assertEqual(parts[2]["text"], "디코유저")
        self.assertEqual(parts[3]["text"], ": 안녕하세요")

    def test_injection_attempts_stay_as_text(self):
        attacks = [
            '"}] ; op Hacker',
            '\\"},{"text":"x","clickEvent":{"action":"run_command","value":"/op me"}}',
            "hi\nop Hacker",
            "hi\r\nstop",
            "]}[{",
            "\\\\\\",
        ]
        for attack in attacks:
            parts = components(build_tellraw(attack, attack))
            # 컴포넌트 개수와 키가 그대로 → 구조가 깨지지 않음
            self.assertEqual(len(parts), 4, attack)
            for part in parts[1:]:
                self.assertEqual(set(part), {"text", "color"}, attack)
            # 공격 문자열은 그대로 '글자' 로만 전달됨
            self.assertEqual(parts[3]["text"], ": " + clean_text(attack), attack)

    def test_minecraft_color_codes_removed(self):
        parts = components(build_tellraw("§cAdmin", "§4§l공지"))
        self.assertEqual(parts[2]["text"], "cAdmin")
        self.assertNotIn("§", parts[3]["text"])

    def test_custom_emoji_and_whitespace(self):
        self.assertEqual(clean_text("hi <:pog:123456> <a:wave:99>"), "hi :pog: :wave:")
        self.assertEqual(clean_text("  a\n\n\tb  "), "a b")

    def test_long_korean_is_truncated_by_bytes(self):
        command = build_tellraw("닉네임", "가" * 1000)
        self.assertLessEqual(len(command.encode("utf-8")), MAX_COMMAND_BYTES)
        text = components(command)[3]["text"]
        self.assertTrue(text.endswith("…"))
        self.assertGreater(len(text), 300)  # 너무 많이 잘리지 않음

    def test_short_message_not_truncated(self):
        self.assertNotIn("…", build_tellraw("a", "b" * 200))

    def test_empty_name_fallback(self):
        self.assertEqual(components(build_tellraw("\n", "hi"))[2]["text"], "알 수 없음")


if __name__ == "__main__":
    unittest.main()
