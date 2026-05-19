#!/usr/bin/env python3
"""
install_service.py — Installiert Transcribrr als macOS Quick Action / Service.

Nach der Installation erscheint "Transcribrr" im Rechtsklick-Menü von Finder
(und allen Apps, die die Services-API unterstützen, z.B. Forklift) für
ausgewählte Audio-Dateien.

Aufruf:
    python3 install_service.py            # installieren / aktualisieren
    python3 install_service.py --uninstall # entfernen
"""

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

FILE_SERVICE_NAME = "Transcribrr"
LATEST_SERVICE_NAME = "Transcribrr Latest"
SERVICES_DIR = Path.home() / "Library" / "Services"
FILE_WORKFLOW_PATH = SERVICES_DIR / f"{FILE_SERVICE_NAME}.workflow"
LATEST_WORKFLOW_PATH = SERVICES_DIR / f"{LATEST_SERVICE_NAME}.workflow"

SCRIPT_DIR = Path(__file__).resolve().parent
TRANSCRIBE_PY = SCRIPT_DIR / "transcribe.py"
LATEST_HELPER_PY = SCRIPT_DIR / "transcribe_latest_voicememo.py"


def gen_uuid() -> str:
    return str(uuid.uuid4()).upper()


SHELL_PATH_PREAMBLE = (
    'export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:'
    '/usr/bin:/bin:/usr/sbin:/sbin:$PATH"'
)


def build_file_shell_script(transcribe_py: Path) -> str:
    """Shell-Script für Datei-Service (input: ausgewählte Audio-Dateien)."""
    return f"""#!/bin/zsh
{SHELL_PATH_PREAMBLE}

# Sequentiell, damit mehrere Dateien nicht ums Clipboard kämpfen.
for f in "$@"; do
    /usr/bin/env python3 "{transcribe_py}" --notify "$f" >/dev/null 2>&1
done
"""


def build_latest_shell_script(helper_py: Path) -> str:
    """Shell-Script für Latest-Voice-Memo-Service (kein Input)."""
    return f"""#!/bin/zsh
{SHELL_PATH_PREAMBLE}
/usr/bin/env python3 "{helper_py}" >/dev/null 2>&1
"""


def build_document_wflow(command: str, accepts_files: bool) -> dict:
    """Inhalt von document.wflow — der eigentliche Workflow.

    Struktur orientiert sich exakt an einem real von Automator generierten
    Quick-Action-Workflow mit Run Shell Script. macOS ist sehr pingelig bei
    Typen (Integer vs Boolean) und Feldnamen (AMApplication ohne Präfix-
    Variante). Bei Abweichungen erscheint:
    "The Service cannot be run because it is not configured correctly."
    """
    input_uuid = gen_uuid()
    output_uuid = gen_uuid()
    action_uuid = gen_uuid()

    if accepts_files:
        accept_types = ["com.apple.cocoa.string"]
        input_method = 1  # 1 = "as arguments"
        input_type_id = "com.apple.Automator.fileSystemObject.music"
        processes_input = True
    else:
        accept_types = []
        input_method = 0  # ignoriert, da kein Input
        input_type_id = "com.apple.Automator.nothing"
        processes_input = False

    return {
        "AMApplicationBuild": "528",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [
            {
                "action": {
                    "AMAccepts": {
                        "Container": "List",
                        "Optional": True,
                        "Types": accept_types,
                    },
                    "AMActionVersion": "2.0.3",
                    "AMApplication": ["Automator"],
                    "AMParameterProperties": {
                        "COMMAND_STRING": {},
                        "CheckedForUserDefaultShell": {},
                        "inputMethod": {},
                        "shell": {},
                        "source": {},
                    },
                    "AMProvides": {
                        "Container": "List",
                        "Types": ["com.apple.cocoa.string"],
                    },
                    "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
                    "ActionName": "Run Shell Script",
                    "ActionParameters": {
                        "COMMAND_STRING": command,
                        "CheckedForUserDefaultShell": True,
                        "inputMethod": input_method,
                        "shell": "/bin/zsh",
                        "source": "",
                    },
                    "BundleIdentifier": "com.apple.RunShellScript",
                    "CFBundleVersion": "2.0.3",
                    "CanShowSelectedItemsWhenRun": True,
                    "CanShowWhenRun": True,
                    "Category": ["AMCategoryUtilities"],
                    "Class Name": "RunShellScriptAction",
                    "InputUUID": input_uuid,
                    "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
                    "OutputUUID": output_uuid,
                    "UUID": action_uuid,
                    "UnlocalizedApplications": ["Automator"],
                    "arguments": {
                        "0": {
                            "default value": 0,
                            "name": "inputMethod",
                            "required": "0",
                            "type": "0",
                            "uuid": "0",
                        },
                        "1": {
                            "default value": "",
                            "name": "CheckedForUserDefaultShell",
                            "required": "0",
                            "type": "0",
                            "uuid": "1",
                        },
                        "2": {
                            "default value": "",
                            "name": "source",
                            "required": "0",
                            "type": "0",
                            "uuid": "2",
                        },
                        "3": {
                            "default value": "",
                            "name": "COMMAND_STRING",
                            "required": "0",
                            "type": "0",
                            "uuid": "3",
                        },
                        "4": {
                            "default value": "/bin/sh",
                            "name": "shell",
                            "required": "0",
                            "type": "0",
                            "uuid": "4",
                        },
                    },
                    "conversionLabel": 0,
                    "isViewVisible": 1,
                    "location": "309.000000:316.000000",
                    "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/Base.lproj/main.nib",
                },
                "isViewVisible": 1,
            }
        ],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": input_type_id,
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 11,
            "processesInput": processes_input,
            "serviceInputTypeIdentifier": input_type_id,
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": processes_input,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def build_info_plist(service_name: str, accepts_files: bool) -> dict:
    """Info.plist — registriert den Service bei macOS."""
    entry = {
        "NSBackgroundColorName": "background",
        "NSIconName": "NSActionTemplate",
        "NSMenuItem": {"default": service_name},
        "NSMessage": "runWorkflowAsService",
    }
    if accepts_files:
        entry["NSSendFileTypes"] = [
            "public.audio",
            "public.mpeg-4-audio",
            "public.mp3",
            "com.apple.m4a-audio",
            "public.wav",
            "public.aifc-audio",
            "public.aiff-audio",
        ]
    return {"NSServices": [entry]}


def _send_test_notification() -> bool:
    """Test-Notification rausschicken, damit der User checken kann ob's klappt."""
    tn = shutil.which("terminal-notifier")
    if tn:
        r = subprocess.run(
            [tn, "-title", "Transcribrr", "-message",
             "Installation erfolgreich — solltest du diese Nachricht sehen?",
             "-sender", "com.apple.Terminal", "-group", "transcribrr"],
            capture_output=True,
        )
        return r.returncode == 0
    r = subprocess.run(
        ["osascript", "-e",
         'display notification "Installation erfolgreich" with title "Transcribrr"'],
        capture_output=True,
    )
    return r.returncode == 0


def _install_one(workflow_path: Path, service_name: str,
                 command: str, accepts_files: bool) -> None:
    if workflow_path.exists():
        print(f"→ Entferne bestehende Installation: {workflow_path}")
        shutil.rmtree(workflow_path)

    contents = workflow_path / "Contents"
    contents.mkdir(parents=True, exist_ok=True)

    info_path = contents / "Info.plist"
    with info_path.open("wb") as f:
        plistlib.dump(build_info_plist(service_name, accepts_files), f)

    doc_path = contents / "document.wflow"
    with doc_path.open("wb") as f:
        plistlib.dump(build_document_wflow(command, accepts_files), f)

    for p in (info_path, doc_path):
        r = subprocess.run(["plutil", "-lint", str(p)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"✗ Ungültiges Plist: {p}\n{r.stdout}{r.stderr}",
                  file=sys.stderr)
            sys.exit(1)

    print(f"✓ Installiert: {workflow_path}")


def install() -> None:
    if not TRANSCRIBE_PY.exists():
        print(f"✗ transcribe.py nicht gefunden: {TRANSCRIBE_PY}", file=sys.stderr)
        sys.exit(1)
    if not LATEST_HELPER_PY.exists():
        print(f"✗ Helper nicht gefunden: {LATEST_HELPER_PY}", file=sys.stderr)
        sys.exit(1)

    os.chmod(TRANSCRIBE_PY, 0o755)
    os.chmod(LATEST_HELPER_PY, 0o755)

    _install_one(
        FILE_WORKFLOW_PATH, FILE_SERVICE_NAME,
        build_file_shell_script(TRANSCRIBE_PY),
        accepts_files=True,
    )
    _install_one(
        LATEST_WORKFLOW_PATH, LATEST_SERVICE_NAME,
        build_latest_shell_script(LATEST_HELPER_PY),
        accepts_files=False,
    )

    subprocess.run(
        ["/System/Library/CoreServices/pbs", "-update"],
        capture_output=True,
    )
    subprocess.run(["killall", "Finder"], capture_output=True)

    has_tn = bool(shutil.which("terminal-notifier"))
    print()
    print(f"  Notification-Backend: "
          f"{'terminal-notifier' if has_tn else 'osascript (Fallback)'}")
    _send_test_notification()
    print("  → Test-Notification gesendet. Siehst du sie?")

    print()
    print("Zwei Services installiert:")
    print(f"  • {FILE_SERVICE_NAME}")
    print("      Rechtsklick auf Audio-Datei → Dienste → Transcribrr")
    print(f"  • {LATEST_SERVICE_NAME}")
    print("      Transkribiert die NEUESTE Voice Memo Aufnahme")
    print("      (kein Input nötig — auf Tastenkürzel binden!)")
    print()
    print("Tastenkürzel binden:")
    print(f"  Systemeinstellungen → Tastatur → Tastaturkurzbefehle → Dienste")
    print(f"  → {LATEST_SERVICE_NAME} aktivieren + Shortcut zuweisen (z.B. ⌘⇧T)")
    print()
    print("Beim ERSTEN Aufruf von 'Transcribrr Latest':")
    print("  macOS fragt nach Festplattenvollzugriff für Automator/Workflow.")
    print("  Falls nicht: Systemeinstellungen → Datenschutz → Festplattenvollzugriff")
    print("  → Automator (oder /System/Library/CoreServices/WorkflowKit) aktivieren.")
    print()
    print("Falls du die Test-Notification NICHT siehst:")
    print("  Systemeinstellungen → Mitteilungen → terminal-notifier (oder Terminal)")
    print("  → Mitteilungen erlauben")


def uninstall() -> None:
    removed = []
    for path in (FILE_WORKFLOW_PATH, LATEST_WORKFLOW_PATH):
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    if not removed:
        print("→ Nichts zu deinstallieren.")
        return
    subprocess.run(
        ["/System/Library/CoreServices/pbs", "-update"],
        capture_output=True,
    )
    subprocess.run(["killall", "Finder"], capture_output=True)
    for r in removed:
        print(f"✓ Entfernt: {r}")


def main() -> None:
    p = argparse.ArgumentParser(description="Installiert Transcribrr als macOS Service.")
    p.add_argument("--uninstall", action="store_true",
                   help="Service entfernen statt installieren")
    args = p.parse_args()

    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
