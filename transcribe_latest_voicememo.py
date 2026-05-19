#!/usr/bin/env python3
"""
transcribe_latest_voicememo.py
Greift die neueste Voice Memo Aufnahme und transkribiert sie.
Workaround für die Sandbox: Voice Memos.app exponiert keine Services-API,
also lesen wir den Recordings-Ordner direkt.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

VOICE_MEMOS_DIR = (
    Path.home()
    / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
)
SCRIPT_DIR = Path(__file__).resolve().parent
TRANSCRIBE_PY = SCRIPT_DIR / "transcribe.py"


def notify(title: str, message: str, subtitle: str = "") -> None:
    tn = shutil.which("terminal-notifier")
    if tn:
        cmd = [
            tn, "-title", title, "-message", message,
            "-sender", "com.apple.Terminal", "-group", "transcribrr",
        ]
        if subtitle:
            cmd += ["-subtitle", subtitle]
        subprocess.run(cmd, capture_output=True)
        return
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{message}" with title "{title}"'],
        capture_output=True,
    )


def main() -> None:
    if not VOICE_MEMOS_DIR.exists():
        notify(
            "Transcribrr — Fehler",
            "Voice Memos Ordner nicht gefunden",
            str(VOICE_MEMOS_DIR),
        )
        sys.exit(1)

    try:
        files = [p for p in VOICE_MEMOS_DIR.iterdir()
                 if p.suffix.lower() == ".m4a"]
    except PermissionError:
        notify(
            "Transcribrr — Kein Zugriff",
            "Automator braucht Full Disk Access",
            "Systemeinstellungen → Datenschutz → Festplattenvollzugriff",
        )
        sys.exit(1)

    if not files:
        notify("Transcribrr", "Keine Voice Memo Aufnahmen gefunden")
        sys.exit(1)

    latest = max(files, key=lambda p: p.stat().st_mtime)

    # exec — transcribe.py übernimmt mit eigenen Notifications
    os.execvp("python3",
              ["python3", str(TRANSCRIBE_PY), "--notify", str(latest)])


if __name__ == "__main__":
    main()
