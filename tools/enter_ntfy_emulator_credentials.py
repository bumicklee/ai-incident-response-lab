"""Fill the ntfy login dialog on the dedicated Android 34 lab emulator.

Run only after the app shows "Login required". No credential is printed or
saved to a screenshot. Screen coordinates match the 1080x2400 Pixel_API34 AVD.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parents[1]


def adb(*args: str) -> str:
    result = subprocess.run(["adb", *args], check=True, capture_output=True, text=True)
    return result.stdout


def main() -> None:
    devices = adb("devices")
    if "emulator-5554\tdevice" not in devices:
        raise SystemExit("Pixel_API34 에뮬레이터가 연결되지 않았습니다.")
    values = dict(line.split("=", 1) for line in (HERE / ".env").read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    user = values["LAB_NTFY_VIEW_USER"]
    password = values["LAB_NTFY_VIEW_PASSWORD"]
    if not user.isalnum() or not password.isalnum():
        raise SystemExit("ADB 입력 가능한 영숫자 형식이 아닙니다. 값을 출력하지 않았습니다.")
    adb("shell", "input", "text", user)
    adb("shell", "input", "tap", "220", "730")
    adb("shell", "input", "text", password)
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    adb("shell", "input", "tap", "950", "210")
    print("에뮬레이터 ntfy 로그인 입력을 보냈습니다. 인증값은 출력하지 않았습니다.")


if __name__ == "__main__":
    main()
