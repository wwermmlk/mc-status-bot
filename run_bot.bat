@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".env" (
    echo [오류] .env 파일이 없습니다. .env.example 을 복사해 .env 로 만들고 값을 채워주세요.
    pause
    exit /b 1
)

:loop
echo [%date% %time%] 봇을 시작합니다...
python bot.py
echo [%date% %time%] 봇이 종료되었습니다. 10초 후 재시작합니다. (창을 닫으면 완전히 종료)
timeout /t 10 /nobreak > nul
goto loop
