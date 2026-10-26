#!/usr/bin/env python3
"""Build a standalone dict-anki executable with PyInstaller and register it
with the desktop, detecting the OS automatically.

  Linux:   dist/dict-anki
           + ~/.local/share/applications/dict-anki.desktop
           + ~/.local/share/icons/hicolor/256x256/apps/dict-anki.png
  Windows: dist\\dict-anki.exe
           + "dict.cc → Anki" shortcut on the Desktop and in the Start Menu

Usage:  python build.py

The build runs under $PYTHON if set, else the project venv if present, else the
interpreter running this script. Missing dependencies are pip-installed into it.
PyInstaller can't cross-compile: run this on the OS you want an executable for.
"""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "dict-anki"
DISPLAY_NAME = "dict.cc → Anki"
COMMENT = "German flashcard creator using dict.cc"


def find_python(system: str) -> str:
    if os.environ.get("PYTHON"):
        return os.environ["PYTHON"]
    venv_py = ROOT / "venv" / ("Scripts/python.exe" if system == "Windows" else "bin/python")
    return str(venv_py) if venv_py.exists() else sys.executable


def run(*cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True, **kw)


def ensure_deps(py: str):
    probe = "import PyQt6, requests, bs4, gtts, PyInstaller"
    if subprocess.run([py, "-c", probe], capture_output=True).returncode != 0:
        run(py, "-m", "pip", "install", "-r", "requirements.txt", "pyinstaller")


def render_icon(py: str, system: str) -> Path:
    """Render the in-app ♾ icon with the app's own drawing code. Uses the native
    platform plugin: under 'offscreen' there are no fonts and ♾ comes out as an
    empty box."""
    if system == "Windows":
        out = ROOT / "icon.ico"
        code = f"app._save_icon_ico({str(out)!r})"
    else:
        out = ROOT / "build" / "icon.png"
        out.parent.mkdir(exist_ok=True)
        code = f"app._render_icon_pixmap(256).save({str(out)!r}, 'PNG')"
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    run(py, "-c", f"from PyQt6.QtWidgets import QApplication; import app; "
                  f"a = QApplication([]); {code}", env=env)
    return out


def build_exe(py: str, system: str, icon: Path) -> Path:
    cmd = [py, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--name", NAME, "--windowed", "--onefile",
           "--specpath", "build"]  # keep the generated .spec out of the project root
    if system == "Windows":
        cmd += ["--icon", icon]  # ELF binaries have no embedded icon
    run(*cmd, "app.py")
    exe = ROOT / "dist" / (NAME + ".exe" if system == "Windows" else NAME)
    if not exe.exists():
        sys.exit(f"PyInstaller finished but {exe} is missing")
    return exe


def install_linux_entry(exe: Path, icon: Path):
    data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    icon_dir = data / "icons/hicolor/256x256/apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icon_dir / f"{NAME}.png")

    apps = data / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    desktop = apps / f"{NAME}.desktop"
    # Exec values containing spaces must be double-quoted (Desktop Entry spec)
    exec_path = str(exe).replace("\\", "\\\\").replace('"', '\\"')
    desktop.write_text(f"""[Desktop Entry]
Version=1.0
Type=Application
Name={DISPLAY_NAME}
Comment={COMMENT}
Exec="{exec_path}"
Icon={NAME}
Terminal=false
Categories=Education;Languages;
StartupNotify=true
StartupWMClass={NAME}
""", encoding="utf-8")
    desktop.chmod(0o755)
    print(f"Desktop entry: {desktop}")

    for tool, args in (("update-desktop-database", [apps]),
                       ("gtk-update-icon-cache", ["-f", "-t", data / "icons/hicolor"])):
        if shutil.which(tool):
            subprocess.run([tool, *map(str, args)], capture_output=True)


def install_windows_shortcuts(exe: Path):
    def q(s) -> str:  # PowerShell single-quoted string literal
        return "'" + str(s).replace("'", "''") + "'"

    # WScript.Shell creates .lnk files; GetFolderPath follows OneDrive redirection.
    # Its Save() converts the path to the ANSI code page, so the → in the name
    # becomes an invalid "?". Save under an ASCII name, then let PowerShell
    # (full Unicode) move it into place.
    ps = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$tmp = Join-Path ([IO.Path]::GetTempPath()) {q(NAME + '.lnk')}
foreach ($d in @([Environment]::GetFolderPath('Desktop'),
                 [Environment]::GetFolderPath('Programs'))) {{
    $lnk = $shell.CreateShortcut($tmp)
    $lnk.TargetPath = {q(exe)}
    $lnk.WorkingDirectory = {q(exe.parent)}
    $lnk.IconLocation = {q(f'{exe},0')}
    $lnk.Description = {q(COMMENT)}
    $lnk.Save()
    $dest = Join-Path $d {q(DISPLAY_NAME + '.lnk')}
    Move-Item -LiteralPath $tmp -Destination $dest -Force
    Write-Host "Shortcut: $dest"
}}
"""
    run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps)


def main():
    system = platform.system()
    if system not in ("Linux", "Windows"):
        sys.exit(f"Unsupported OS: {system} (only Linux and Windows are supported)")

    py = find_python(system)
    print(f"OS: {system}  Python: {py}")
    ensure_deps(py)
    icon = render_icon(py, system)
    exe = build_exe(py, system, icon)
    print(f"Built: {exe}")

    if system == "Linux":
        install_linux_entry(exe, icon)
    else:
        install_windows_shortcuts(exe)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(f"Command failed with exit code {e.returncode}")
