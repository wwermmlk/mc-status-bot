# mc-status-bot

마인크래프트 서버와 디스코드를 잇는 두 개의 파이썬 봇입니다.

## 스택

- Python 3.10+ (asyncio), 외부 의존성은 `discord.py`, `mcstatus`, `python-dotenv` 세 개뿐입니다. 새 의존성은 꼭 필요할 때만 추가합니다.
- 설정은 모두 `.env`에서 읽습니다(`python-dotenv`). 비밀값은 코드와 테스트에 넣지 않습니다.

## 구성 요소

| 파일 | 실행 위치 | 역할 |
|---|---|---|
| `bot.py` | Oracle Cloud (Ubuntu, systemd) | 상태 보드 메시지를 30초마다 수정 |
| `bridge.py` | 마인크래프트 서버 PC (Windows, 작업 스케줄러) | 채팅 양방향 연동, 콘솔 로그 전송 |
| `mc_query.py`, `site_info.py` | 상태 봇 전용 | 서버 조회, 홈페이지 문구 |
| `mc_log.py`, `mc_text.py`, `rcon.py` | 브리지 전용 | 로그 추적·파싱, tellraw 변환, RCON |
| `diag.py` | 양쪽 | 채널 권한 진단 (`python diag.py bridge`) |

두 프로그램은 같은 디스코드 봇 계정을 쓸 수 있습니다. 그래서 슬래시 커맨드는 `tree.sync()`(전체 덮어쓰기) 대신 명령어별 upsert로 등록합니다.

## 테스트

```bash
python -m unittest
```

외부 연결 없이 도는 단위 테스트입니다. 로그 파서, `tellraw` 변환(명령 주입 방어 포함), RCON(가짜 서버), 디스코드 메시지 형식을 검증합니다.

로그 형식이나 메시지 형식을 바꾸면 실제 서버 로그 형식을 테스트에 추가합니다. 테스트 데이터에는 실제 플레이어 닉네임, 서버 주소, IP를 쓰지 않고 `Steve`, `203.0.113.x` 같은 예시 값을 씁니다.

## 배포

- 상태 봇: `scp bot.py ubuntu@<오라클IP>:~/mc-status-bot/` 후 `sudo systemctl restart mc-status-bot`
- 브리지: 서버 PC에서 작업 스케줄러 `mc-bridge` 끝내기 → `git pull` → 실행. 실행 기록은 `bridge.log`
