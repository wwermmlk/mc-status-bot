import asyncio
import struct
import unittest

from rcon import Rcon, RconAuthError, RconError

PASSWORD = "secret-pass"


class FakeRconServer:
    """마인크래프트 RCON 동작을 흉내 내는 테스트 서버 (클라이언트 코드와 독립 구현)."""

    def __init__(self):
        self.commands: list[str] = []
        self.drop_next_command = False
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        authed = False
        try:
            while True:
                length = struct.unpack("<i", await reader.readexactly(4))[0]
                data = await reader.readexactly(length)
                req_id, kind = struct.unpack("<ii", data[:8])
                body = data[8:-2].decode("utf-8")
                if kind == 3:
                    authed = body == PASSWORD
                    self._send(writer, req_id if authed else -1, 2, "")
                elif kind == 2 and authed:
                    if self.drop_next_command:
                        self.drop_next_command = False
                        writer.close()
                        return
                    self.commands.append(body)
                    self._send(writer, req_id, 0, f"echo:{body}")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @staticmethod
    def _send(writer, req_id, kind, body):
        payload = struct.pack("<ii", req_id, kind) + body.encode("utf-8") + b"\x00\x00"
        writer.write(struct.pack("<i", len(payload)) + payload)


class RconTest(unittest.TestCase):
    def run_with_server(self, scenario):
        async def runner():
            fake = FakeRconServer()
            port = await fake.start()
            try:
                return await scenario(fake, port)
            finally:
                await fake.stop()

        return asyncio.run(runner())

    def test_command_roundtrip_with_korean(self):
        async def scenario(fake, port):
            rcon = Rcon("127.0.0.1", port, PASSWORD)
            result = await rcon.command('tellraw @a ["안녕"]')
            await rcon.close()
            return fake.commands, result

        commands, result = self.run_with_server(scenario)
        self.assertEqual(commands, ['tellraw @a ["안녕"]'])
        self.assertEqual(result, 'echo:tellraw @a ["안녕"]')

    def test_wrong_password_raises_auth_error(self):
        async def scenario(fake, port):
            rcon = Rcon("127.0.0.1", port, "wrong")
            with self.assertRaises(RconAuthError):
                await rcon.command("list")
            return fake.commands

        self.assertEqual(self.run_with_server(scenario), [])

    def test_reconnects_after_connection_drop(self):
        async def scenario(fake, port):
            rcon = Rcon("127.0.0.1", port, PASSWORD)
            await rcon.command("first")
            fake.drop_next_command = True  # 서버 재시작 등으로 연결이 끊긴 상황
            second = await rcon.command("second")
            await rcon.close()
            return fake.commands, second

        commands, second = self.run_with_server(scenario)
        self.assertEqual(commands, ["first", "second"])
        self.assertEqual(second, "echo:second")

    def test_server_down_raises_rcon_error(self):
        async def scenario():
            # 아무도 듣지 않는 포트
            rcon = Rcon("127.0.0.1", 1, PASSWORD, timeout=1)
            with self.assertRaises(RconError):
                await rcon.command("list")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
