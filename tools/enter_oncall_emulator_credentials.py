"""Fill Chrome's local on-call HTTP Basic dialog on Pixel_API34.

Run only when the dialog for 10.0.2.2:18090 is visible. No secret is printed.
The lab emulator is 1080x2400; coordinates are not for a real phone.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parents[1]


def adb(*args: str) -> str:
    return subprocess.run(["adb", *args], check=True, capture_output=True, text=True).stdout


def main() -> None:
    if "emulator-5554\tdevice" not in adb("devices"):
        raise SystemExit("Pixel_API34 에뮬레이터가 연결되지 않았습니다.")
    values = dict(line.split("=", 1) for line in (HERE / ".env").read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    user = values["LAB_ONCALL_VIEW_USER"]
    password = values["LAB_ONCALL_VIEW_PASSWORD"]
    if not user.isalnum() or not password.isalnum():
        raise SystemExit("ADB 입력 가능한 영숫자 형식이 아닙니다. 값을 출력하지 않았습니다.")
    adb("shell", "input", "text", user)
    adb("shell", "input", "tap", "220", "970")
    adb("shell", "input", "text", password)
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    adb("shell", "input", "tap", "930", "1095")
    print("에뮬레이터 Chrome의 로컬 온콜 로그인 입력을 보냈습니다. 인증값은 출력하지 않았습니다.")


if __name__ == "__main__":
    main()
