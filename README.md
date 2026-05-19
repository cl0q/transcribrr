# Transcribrr

Audio-Transkription für macOS mit automatischem Routing zwischen lokalem
Whisper (Apple Silicon) und Google Cloud Speech-to-Text.

- **Kurze Aufnahmen (< 5 Min):** Lokal via [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — keine Cloud, keine Latenz.
- **Lange Aufnahmen (≥ 5 Min):** GCP Speech-to-Text, parallel in 58s-Segmenten.
- **Niedrige Konfidenz:** Automatischer Fallback von lokal → GCP.
- **LLM-Polish (optional):** Transkript geht durch [together.ai](https://together.ai) (Qwen 2.5 72B Turbo) — Füllwörter raus, offensichtliche Hörfehler kontextuell korrigiert ("Eiweiß" → "iOS" wenn Software-Kontext).
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

# 3. (Optional, empfohlen) together.ai für LLM-Polish
mkdir -p ~/.config
echo 'TOGETHER_API_KEY=tk-...' >> ~/.config/transcribrr.env

# 4. macOS-Services installieren
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

## LLM-Polish

Wenn `TOGETHER_API_KEY` in `~/.config/transcribrr.env` oder als Umgebungsvariable
gesetzt ist, geht das Rohtranskript automatisch durch ein LLM, bevor es im
Clipboard landet. Default-Modell: `Qwen/Qwen2.5-72B-Instruct-Turbo` (gutes
Deutsch, ~1–2 s Latenz).

Was der Polish-Step macht:

- Entfernt Füllwörter (äh, ähm, also halt, weißt du, …)
- Korrigiert offensichtliche Hörfehler aus dem Kontext  
  *Beispiel:* "Eiweiß" → "iOS" wenn von App-Entwicklung gesprochen wird
- Setzt sinnvolle Satzzeichen und Großschreibung
- **Erfindet keine Information** und **fasst nichts zusammen**

Bei API-Fehler oder Timeout fällt das Tool auf den Rohtext zurück — du
bekommst immer ein Clipboard-Ergebnis.

Abschalten: `--no-polish` oder API-Key entfernen.  
Modell wechseln: `--polish-model meta-llama/Llama-3.3-70B-Instruct-Turbo`

## Konfiguration

Wichtige Flags (siehe `python3 transcribe.py --help`):

| Flag | Beschreibung | Default |
|---|---|---|
| `--language`, `-l` | BCP-47 Sprachcode | `de-DE` |
| `--model`, `-m` | GCP-Modell (`long`, `short`, `chirp_2`, …) | `long` |
| `--whisper-model` | mlx-whisper HuggingFace-Repo | `mlx-community/whisper-medium-mlx` |
| `--local-threshold` | Sekunden unter der lokal genutzt wird | `300` |
| `--confidence-threshold` | Unter diesem Wert Fallback auf GCP | `0.50` |
| `--no-polish` | LLM-Polish abschalten | aus (= polish wenn Key da) |
| `--polish-model` | together.ai-Modell für Polish | `Qwen/Qwen2.5-72B-Instruct-Turbo` |
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
