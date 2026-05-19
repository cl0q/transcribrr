# Transcribrr

Audio-Transkription für macOS mit automatischem Routing zwischen lokalem
Whisper (Apple Silicon) und Google Cloud Speech-to-Text.

- **Kurze Aufnahmen (< 5 Min):** Lokal via [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — keine Cloud, keine Latenz.
- **Lange Aufnahmen (≥ 5 Min):** GCP Speech-to-Text, parallel in 58s-Segmenten.
- **Niedrige Konfidenz:** Automatischer Fallback von lokal → GCP.
- **macOS-Integration:** Rechtsklick auf Audio-Datei → Dienste → Transcribrr.
  Plus zweiter Service "Transcribrr Latest" für die neueste Voice-Memo-Aufnahme.

## Installation

```bash
# 1. Dependencies
brew install ffmpeg terminal-notifier
pip3 install -r requirements.txt

# 2. GCP-Auth (eine der drei Optionen)
gcloud auth login
# ODER
export GOOGLE_APPLICATION_CREDENTIALS=/pfad/zu/service-account.json
# ODER
export GOOGLE_API_KEY=dein-api-key

# Projekt-ID (falls nicht via gcloud config)
export GOOGLE_CLOUD_PROJECT=dein-projekt-id

# 3. macOS-Services installieren
python3 install_service.py
```

## Verwendung

### CLI

```bash
# Auto-Routing (lokal vs GCP)
python3 transcribe.py memo.m4a

# Erzwingen
python3 transcribe.py memo.m4a --force-local
python3 transcribe.py memo.m4a --force-gcp --model chirp_2

# Strukturierte Ausgabe
python3 transcribe.py memo.m4a --json
python3 transcribe.py memo.m4a --text

# macOS-Notifications anzeigen
python3 transcribe.py memo.m4a --notify
```

### macOS Quick Action

Nach `python3 install_service.py`:

1. **Beliebige Audio-Datei** → Rechtsklick → Dienste → **Transcribrr**
2. **Letzte Voice-Memo-Aufnahme** → Tastenkürzel zum Service "Transcribrr Latest" binden:
   Systemeinstellungen → Tastatur → Tastaturkurzbefehle → Dienste

Transkript landet in der Zwischenablage. Drei Benachrichtigungen:
Start (welches Backend) → ggf. Fallback (GCP wegen niedriger Konfidenz) → Fertig.

### Voice-Memos-Workflow

Voice Memos ist eine Sandbox-App und exponiert seine Aufnahmen nicht via
Services-API. Der zweite Service "Transcribrr Latest" umgeht das, indem er
direkt aus `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/`
die neueste `.m4a` liest. Beim ersten Aufruf fragt macOS nach Festplattenvollzugriff
für Automator/WorkflowKit.

## Konfiguration

Wichtige Flags (siehe `python3 transcribe.py --help`):

| Flag | Beschreibung | Default |
|---|---|---|
| `--language`, `-l` | BCP-47 Sprachcode | `de-DE` |
| `--model`, `-m` | GCP-Modell (`long`, `short`, `chirp_2`, …) | `long` |
| `--whisper-model` | mlx-whisper HuggingFace-Repo | `mlx-community/whisper-medium-mlx` |
| `--local-threshold` | Sekunden unter der lokal genutzt wird | `300` |
| `--confidence-threshold` | Unter diesem Wert Fallback auf GCP | `0.50` |
| `--keep-silence` | Stille NICHT entfernen (GCP-Pfad) | aus |
| `--no-clipboard` | Nicht in die Zwischenablage kopieren | aus |

## Kosten

GCP Speech-to-Text v2:

- Standard-Modelle: $0.024 / Min, abgerechnet in 15s-Schritten
- Chirp-Modelle: $0.054 / Min
- 60 Min/Monat kostenlos

Lokale Transkription ist gratis.

## Deinstallation

```bash
python3 install_service.py --uninstall
```

## Lizenz

MIT — siehe [LICENSE](LICENSE).
