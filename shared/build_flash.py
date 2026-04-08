"""
Shared build and flash layer for all four MCU platforms.
Detects platform from project directory structure if not specified.
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Literal

Platform = Literal["arduino", "esp32", "pico", "teensy"]


class BuildFlashError(Exception):
    pass


def _run(cmd: list[str], cwd: str, env: dict | None = None) -> tuple[int, str, str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    return result.returncode, result.stdout, result.stderr


def detect_platform(project_path: str) -> Platform:
    """Infer platform from project directory contents."""
    p = Path(project_path)
    if (p / "CMakeLists.txt").exists() and (p / "pico_sdk_import.cmake").exists():
        return "pico"
    if (p / "CMakeLists.txt").exists() and any(p.glob("sdkconfig*")):
        return "esp32"
    if (p / "platformio.ini").exists():
        return "teensy"
    if any(p.glob("*.ino")):
        return "arduino"
    # Check parent structure
    parent = str(p).lower()
    for name in ("arduino", "esp32", "pico", "teensy"):
        if f"/{name}/" in parent:
            return name  # type: ignore
    raise BuildFlashError(f"Cannot detect platform in {project_path}. Specify platform explicitly.")


# ---------------------------------------------------------------------------
# Arduino
# ---------------------------------------------------------------------------

def arduino_build(project_path: str, fqbn: str) -> tuple[bool, str]:
    """Compile Arduino sketch. Returns (success, output)."""
    code, out, err = _run(
        ["arduino-cli", "compile", "--fqbn", fqbn, "."],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"Arduino compile failed:\n{combined}")
    return True, combined


def arduino_flash(project_path: str, fqbn: str, port: str) -> tuple[bool, str]:
    code, out, err = _run(
        ["arduino-cli", "upload", "--fqbn", fqbn, "--port", port, "."],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"Arduino upload failed:\n{combined}")
    return True, combined


# ---------------------------------------------------------------------------
# ESP32
# ---------------------------------------------------------------------------

_IDF_EXPORT = "source /opt/esp-idf/export.sh"


def esp32_build(project_path: str) -> tuple[bool, str]:
    code, out, err = _run(
        ["bash", "-c", f"{_IDF_EXPORT} && idf.py build"],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"ESP-IDF build failed:\n{combined}")
    return True, combined


def esp32_flash(project_path: str, port: str = "/dev/ttyUSB0") -> tuple[bool, str]:
    code, out, err = _run(
        ["bash", "-c", f"{_IDF_EXPORT} && idf.py flash -p {port}"],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"ESP-IDF flash failed:\n{combined}")
    return True, combined


# ---------------------------------------------------------------------------
# Pico
# ---------------------------------------------------------------------------

def pico_build(project_path: str, board: str = "pico", sdk_path: str = "/opt/pico-sdk") -> tuple[bool, str]:
    p = Path(project_path)
    build_dir = p / "build"
    build_dir.mkdir(exist_ok=True)

    # Configure if CMakeCache not present
    if not (build_dir / "CMakeCache.txt").exists():
        code, out, err = _run(
            [
                "cmake",
                f"-DPICO_SDK_PATH={sdk_path}",
                f"-DPICO_BOARD={board}",
                "-B", "build",
                ".",
            ],
            cwd=project_path,
        )
        if code != 0:
            raise BuildFlashError(f"CMake configure failed:\n{out + err}")

    nproc = os.cpu_count() or 4
    code, out, err = _run(
        ["cmake", "--build", "build", f"-j{nproc}"],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"Pico build failed:\n{combined}")
    return True, combined


def pico_flash(project_path: str, binary_name: str | None = None) -> tuple[bool, str]:
    p = Path(project_path)
    build_dir = p / "build"

    # Find UF2
    if binary_name:
        uf2 = build_dir / f"{binary_name}.uf2"
    else:
        uf2s = list(build_dir.glob("*.uf2"))
        if not uf2s:
            raise BuildFlashError(f"No .uf2 file found in {build_dir}")
        uf2 = uf2s[0]

    if not shutil.which("picotool"):
        raise BuildFlashError("picotool not found in PATH")

    code, out, err = _run(
        ["picotool", "load", str(uf2), "-f"],
        cwd=project_path,
    )
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"picotool load failed:\n{combined}")

    # Reboot
    _run(["picotool", "reboot"], cwd=project_path)
    return True, combined


# ---------------------------------------------------------------------------
# Teensy (PlatformIO)
# ---------------------------------------------------------------------------

def teensy_build(project_path: str) -> tuple[bool, str]:
    code, out, err = _run(["pio", "run"], cwd=project_path)
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"PlatformIO build failed:\n{combined}")
    return True, combined


def teensy_flash(project_path: str) -> tuple[bool, str]:
    code, out, err = _run(["pio", "run", "--target", "upload"], cwd=project_path)
    combined = out + err
    if code != 0:
        raise BuildFlashError(f"PlatformIO upload failed:\n{combined}")
    return True, combined


# ---------------------------------------------------------------------------
# Unified entrypoints
# ---------------------------------------------------------------------------

def build(
    project_path: str,
    platform: Platform | None = None,
    fqbn: str = "arduino:avr:uno",
    board: str = "pico",
    sdk_path: str = "/opt/pico-sdk",
) -> tuple[bool, str]:
    platform = platform or detect_platform(project_path)
    if platform == "arduino":
        return arduino_build(project_path, fqbn)
    if platform == "esp32":
        return esp32_build(project_path)
    if platform == "pico":
        return pico_build(project_path, board=board, sdk_path=sdk_path)
    if platform == "teensy":
        return teensy_build(project_path)
    raise BuildFlashError(f"Unknown platform: {platform}")


def flash(
    project_path: str,
    platform: Platform | None = None,
    fqbn: str = "arduino:avr:uno",
    port: str = "/dev/ttyACM0",
    binary_name: str | None = None,
) -> tuple[bool, str]:
    platform = platform or detect_platform(project_path)
    if platform == "arduino":
        return arduino_flash(project_path, fqbn, port)
    if platform == "esp32":
        return esp32_flash(project_path, port)
    if platform == "pico":
        return pico_flash(project_path, binary_name)
    if platform == "teensy":
        return teensy_flash(project_path)
    raise BuildFlashError(f"Unknown platform: {platform}")


def build_and_flash(
    project_path: str,
    platform: Platform | None = None,
    fqbn: str = "arduino:avr:uno",
    port: str = "/dev/ttyACM0",
    board: str = "pico",
    sdk_path: str = "/opt/pico-sdk",
    binary_name: str | None = None,
) -> tuple[bool, str]:
    platform = platform or detect_platform(project_path)
    ok, build_out = build(project_path, platform, fqbn=fqbn, board=board, sdk_path=sdk_path)
    ok, flash_out = flash(project_path, platform, fqbn=fqbn, port=port, binary_name=binary_name)
    return True, build_out + "\n" + flash_out
