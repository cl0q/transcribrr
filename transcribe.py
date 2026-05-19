#!/usr/bin/env python3
"""
transcribe.py — Automatische Transkription
  < 5 min  →  lokal  (mlx-whisper, Apple Silicon)
  ≥ 5 min  →  GCP Speech-to-Text
  Konfidenz < 50%  →  Fallback auf GCP
"""

import sys
import os
import base64
import json
import subprocess
import time
import argparse
import tempfile
import shutil
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# ─── ANSI Farben ─────────────────────────────────────────────────────────────
R      = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
MAGENTA= "\033[95m"
CYAN   = "\033[96m"

def _c(color: str, text: str) -> str:
    return f"{color}{text}{R}"

# ─── Konstanten ───────────────────────────────────────────────────────────────
LOCAL_THRESHOLD_SECS  = 5 * 60         # < 5 min → lokal
CONFIDENCE_THRESHOLD  = 0.50           # unter 50% → GCP-Fallback
WHISPER_MODEL         = "mlx-community/whisper-medium-mlx"

FREE_TIER_SECONDS     = 60 * 60        # 60 Min/Monat kostenlos (GCP)
BILLING_INCREMENT     = 15             # Abrechnung in 15s-Schritten
GCP_PRICES = {
    "chirp": 0.054 / 60, "chirp_2": 0.054 / 60,
    "long":  0.024 / 60, "short":   0.024 / 60,
    "latest_long": 0.024 / 60, "latest_short": 0.024 / 60,
    "default": 0.024 / 60,
}

API_RECOGNIZE = (
    "https://speech.googleapis.com/v2"
    "/projects/{project}/locations/global/recognizers/_:recognize"
)
MAX_CHUNK_SECS = 58

# ─── UI-Hilfsfunktionen ───────────────────────────────────────────────────────

_NOTIFY_ENABLED = False
_TERMINAL_NOTIFIER = shutil.which("terminal-notifier")

def _send_notification(title: str, message: str, subtitle: str = "") -> None:
    """macOS-Benachrichtigung. No-op wenn --notify nicht gesetzt.

    Bevorzugt terminal-notifier (zuverlässig — Notification wird Terminal.app
    zugeordnet, das normalerweise erlaubt ist). Fallback: osascript (kann von
    macOS gefiltert werden, wenn Script Editor keine Notification-Permission hat).
    """
    if not _NOTIFY_ENABLED:
        return

    try:
        if _TERMINAL_NOTIFIER:
            cmd = [
                _TERMINAL_NOTIFIER,
                "-title", title,
                "-message", message,
                "-sender", "com.apple.Terminal",
                "-group", "transcribrr",  # neue Notif ersetzt alte
            ]
            if subtitle:
                cmd += ["-subtitle", subtitle]
            subprocess.run(cmd, capture_output=True, timeout=5)
        else:
            def esc(s: str) -> str:
                return s.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{esc(message)}" with title "{esc(title)}"'
            if subtitle:
                script += f' subtitle "{esc(subtitle)}"'
            subprocess.run(["osascript", "-e", script],
                           capture_output=True, timeout=5)
    except Exception:
        pass  # niemals wegen Notification crashen

def die(msg: str, hint: str = "") -> None:
    _send_notification("Transcribrr — Fehler", msg, hint)
    print(f"\n{_c(BOLD+RED, '✗ Fehler:')} {msg}", file=sys.stderr)
    if hint:
        print(f"  {_c(DIM, hint)}", file=sys.stderr)
    sys.exit(1)

def warn(msg: str)  -> None: print(f"{_c(BOLD+YELLOW, '⚠  ')}{msg}")
def info(msg: str)  -> None: print(f"{_c(CYAN, '→')} {msg}")
def ok(msg: str)    -> None: print(f"{_c(BOLD+GREEN, '✓')} {msg}")

def fmt_duration(secs: float) -> str:
    secs = int(secs)
    m, s = divmod(secs, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024: return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def hr(width: int = 60, char: str = "─", color: str = BLUE) -> str:
    return _c(color, char * width)

def row(label: str, value: str, val_color: str = R, width: int = 22) -> None:
    print(f"  {_c(DIM, label.ljust(width))}{val_color}{value}{R}")

def section(title: str) -> None:
    print(f"\n{hr()}")
    print(f"{_c(BOLD+CYAN, '  ' + title)}")
    print(hr())

def confidence_bar(conf: float, width: int = 20) -> str:
    filled = round(conf * width)
    bar = "█" * filled + "░" * (width - filled)
    color = GREEN if conf >= 0.85 else YELLOW if conf >= 0.65 else RED
    return f"{color}{bar}{R} {color}{conf:.1%}{R}"

def wrap_text(text: str, width: int = 72, indent: str = "  ") -> str:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(indent + line)
            line = word
        else:
            line = (line + " " + word).lstrip()
    if line: lines.append(indent + line)
    return "\n".join(lines)

# ─── Audio ───────────────────────────────────────────────────────────────────

def get_audio_info(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]
    try:
        out  = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(out.stdout)
    except FileNotFoundError:
        die("ffprobe nicht gefunden.", "brew install ffmpeg")
    except subprocess.CalledProcessError:
        die(f"Datei kann nicht gelesen werden: {path.name}",
            "Prüfe ob es sich um eine gültige Audiodatei handelt.")
    except json.JSONDecodeError:
        die("ffprobe-Ausgabe konnte nicht geparst werden.")

    fmt   = data.get("format", {})
    audio = next((s for s in data.get("streams", [])
                  if s.get("codec_type") == "audio"), None)
    if not audio:
        die(f"Keine Audiospur gefunden: {path.name}")

    duration = float(fmt.get("duration") or audio.get("duration") or 0)
    if duration == 0:
        die("Audiodauer ist 0 Sekunden — Datei leer oder beschädigt.")

    return {
        "duration":    duration,
        "format_name": fmt.get("format_long_name", fmt.get("format_name", "?")),
        "codec":       audio.get("codec_long_name", audio.get("codec_name", "?")),
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels":    int(audio.get("channels") or 1),
        "size_bytes":  int(fmt.get("size") or 0),
    }


def strip_silence(src: Path, dst: Path) -> None:
    filter_str = (
        "silenceremove="
        "start_periods=1:start_threshold=-40dB:start_duration=0.5:"
        "stop_periods=-1:stop_threshold=-40dB:stop_duration=3"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src), "-af", filter_str,
           "-ar", "16000", "-ac", "1", "-c:a", "flac", str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        die("Stille-Entfernung fehlgeschlagen.", e.stderr.decode()[:200])


def to_flac(src: Path, dst: Path, start: float = 0.0, dur: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if start > 0:     cmd += ["-ss", str(start)]
    if dur is not None: cmd += ["-t", str(dur)]
    cmd += ["-ar", "16000", "-ac", "1", "-c:a", "flac", str(dst)]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        die("Audio-Konvertierung fehlgeschlagen.", e.stderr.decode()[:200])

# ─── Lokale Transkription (mlx-whisper) ──────────────────────────────────────

def _whisper_confidence(segments: list) -> float:
    """Gewichtetes Mittel der Segment-Konfidenz (exp(avg_logprob))."""
    total_dur, weighted = 0.0, 0.0
    for s in segments:
        dur = s.get("end", 0) - s.get("start", 0)
        if s.get("no_speech_prob", 0) > 0.8:
            continue
        conf = math.exp(max(s.get("avg_logprob", -1.0), -5.0))
        weighted  += conf * dur
        total_dur += dur
    return weighted / total_dur if total_dur > 0 else 0.0


def _resolve_model_path(model_repo: str) -> str:
    """Lokalen HuggingFace-Cache-Pfad zurückgeben, falls vorhanden — spart den Netzwerk-Check."""
    try:
        from huggingface_hub import snapshot_download
        return snapshot_download(model_repo, local_files_only=True)
    except Exception:
        return model_repo


def transcribe_local(
    audio_path: Path,
    language: str,
    model_repo: str,
    silent: bool,
) -> dict:
    try:
        import mlx_whisper
    except ImportError:
        die(
            "mlx-whisper nicht installiert.",
            "pip3 install mlx-whisper",
        )

    lang = language.split("-")[0]   # de-DE → de
    model_path = _resolve_model_path(model_repo)

    if not silent:
        info(f"Lokales Modell: {_c(BOLD, model_repo.split('/')[-1])} …")

    t0 = time.time()
    result = mlx_whisper.transcribe(
        str(audio_path),
        path_or_hf_repo=model_path,
        language=lang,
        verbose=False,
    )
    elapsed = time.time() - t0

    segs      = result.get("segments", [])
    conf      = _whisper_confidence(segs)
    full_text = result.get("text", "").strip()

    return {
        "text":     full_text,
        "confidence": conf,
        "segments": [
            {
                "text":       s.get("text", "").strip(),
                "confidence": math.exp(max(s.get("avg_logprob", -1.0), -5.0)),
                "start":      s.get("start", 0),
            }
            for s in segs
        ],
        "elapsed":  elapsed,
        "backend":  "local-whisper",
    }

# ─── GCP Authentifizierung ───────────────────────────────────────────────────

def _try_gcloud_token() -> str | None:
    for gcloud in ["gcloud",
                   os.path.expanduser("~/google-cloud-sdk/bin/gcloud"),
                   "/usr/lib/google-cloud-sdk/bin/gcloud"]:
        try:
            r = subprocess.run([gcloud, "auth", "print-access-token"],
                               capture_output=True, text=True, timeout=15)
            t = r.stdout.strip()
            if r.returncode == 0 and t:
                return t
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _try_service_account_token() -> str | None:
    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_file:
        return None
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GReq
        creds = service_account.Credentials.from_service_account_file(
            creds_file, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(GReq())
        return creds.token
    except ImportError:
        warn("google-auth nicht installiert → pip3 install google-auth")
        return None
    except Exception as e:
        die(f"Service-Account-Fehler: {e}")


def get_credentials() -> tuple[str, str]:
    for fn, label in [(_try_gcloud_token, "gcloud"),
                      (_try_service_account_token, "service-account")]:
        t = fn()
        if t:
            return t, label
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if api_key:
        return api_key, "api-key"
    die(
        "Keine GCP-Authentifizierung gefunden.",
        "1. gcloud auth login\n"
        "  2. export GOOGLE_APPLICATION_CREDENTIALS=/pfad/key.json\n"
        "  3. export GOOGLE_API_KEY=schlüssel",
    )


def get_project_id() -> str:
    for var in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT"):
        if os.environ.get(var):
            return os.environ[var]
    for gcloud in ["gcloud", os.path.expanduser("~/google-cloud-sdk/bin/gcloud")]:
        try:
            r = subprocess.run([gcloud, "config", "get-value", "project"],
                               capture_output=True, text=True, timeout=10)
            v = r.stdout.strip()
            if r.returncode == 0 and v and v != "(unset)":
                return v
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_file:
        try:
            with open(creds_file) as f:
                data = json.load(f)
            if data.get("project_id"):
                return data["project_id"]
        except Exception:
            pass
    die("Keine GCP-Projekt-ID gefunden.",
        "export GOOGLE_CLOUD_PROJECT=dein-projekt-id")

# ─── GCP API-Aufruf ───────────────────────────────────────────────────────────

def recognize_chunk(
    audio_bytes: bytes,
    project: str, token: str, auth_type: str,
    language: str, model: str,
) -> dict:
    url     = API_RECOGNIZE.format(project=project)
    headers = {"Content-Type": "application/json"}
    if auth_type == "api-key":
        url += f"?key={token}"
    else:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "config": {
            "auto_decoding_config": {},
            "language_codes": [language],
            "model": model,
            "features": {
                "enable_automatic_punctuation": True,
                "enable_word_confidence": True,
            },
        },
        "content": base64.b64encode(audio_bytes).decode(),
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
    except requests.exceptions.ConnectionError:
        die("Keine Netzwerkverbindung zur Google-API.")
    except requests.exceptions.Timeout:
        die("API-Timeout nach 120 Sekunden.")

    if resp.status_code == 401:
        die("Authentifizierung abgelaufen.", "gcloud auth login")
    if resp.status_code == 403:
        err = resp.json().get("error", {}).get("message", "")
        die(f"Zugriff verweigert: {err}",
            "Speech-to-Text API aktivieren:\n"
            "  https://console.cloud.google.com/apis/library/speech.googleapis.com")
    if resp.status_code == 400:
        err = resp.json().get("error", {}).get("message", resp.text[:200])
        die(f"Ungültige Anfrage: {err}")
    if resp.status_code != 200:
        die(f"API-Fehler {resp.status_code}:", resp.text[:300])
    return resp.json()


def transcribe_gcp(
    work_path: Path,
    work_duration: float,
    tmpdir: str,
    language: str,
    model: str,
    silent: bool,
    project: str,
    token: str,
    auth_type: str,
) -> dict:
    n_chunks   = math.ceil(work_duration / MAX_CHUNK_SECS)
    chunk_specs = [
        (i, i * MAX_CHUNK_SECS, min(MAX_CHUNK_SECS, work_duration - i * MAX_CHUNK_SECS))
        for i in range(n_chunks)
    ]

    if not silent and n_chunks > 1:
        warn(f"{fmt_duration(work_duration)} → {n_chunks} Segmente, parallel verarbeitet.")

    def convert_chunk(spec):
        i, start, seg_dur = spec
        dst = Path(tmpdir) / f"chunk_{i:03d}.flac"
        to_flac(work_path, dst, start=start, dur=seg_dur)
        return i, dst, start, seg_dur

    workers = min(n_chunks, os.cpu_count() or 4)
    chunks: list = [None] * n_chunks
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, dst, start, seg_dur in pool.map(convert_chunk, chunk_specs):
            chunks[i] = (dst, start, seg_dur)

    t0, done = time.time(), 0
    results_map: dict = {}

    def do_chunk(idx_chunk):
        i, (chunk_path, chunk_start, _) = idx_chunk
        audio_bytes = chunk_path.read_bytes()
        resp = recognize_chunk(audio_bytes, project, token, auth_type,
                               language, model)
        return i, chunk_start, resp

    with ThreadPoolExecutor(max_workers=n_chunks) as pool:
        futures = {pool.submit(do_chunk, (i, c)): i for i, c in enumerate(chunks)}
        for future in as_completed(futures):
            i, chunk_start, resp = future.result()
            results_map[i] = (chunk_start, resp)
            done += 1
            if not silent and n_chunks > 1:
                print(f"  {_c(CYAN, f'{done}/{n_chunks}')} Segmente fertig …",
                      end="\r")

    if not silent and n_chunks > 1:
        print(" " * 40, end="\r")

    segments: list = []
    total_conf, conf_n = 0.0, 0
    for i in range(n_chunks):
        chunk_start, resp = results_map[i]
        for result in resp.get("results", []):
            for alt in result.get("alternatives", [])[:1]:
                text = alt.get("transcript", "").strip()
                conf = float(alt.get("confidence", 0.0))
                if text:
                    segments.append({"text": text, "confidence": conf,
                                     "start": chunk_start})
                    if conf > 0:
                        total_conf += conf
                        conf_n    += 1

    return {
        "text":       " ".join(s["text"] for s in segments),
        "confidence": total_conf / conf_n if conf_n else 0.0,
        "segments":   segments,
        "elapsed":    time.time() - t0,
        "backend":    "gcp",
    }

# ─── Kosten ───────────────────────────────────────────────────────────────────

def calc_cost(duration_secs: float, model: str) -> dict:
    units        = math.ceil(duration_secs / BILLING_INCREMENT)
    billed_secs  = units * BILLING_INCREMENT
    price_per_sec = GCP_PRICES.get(model.lower(), GCP_PRICES["default"])
    return {
        "billed_secs":   billed_secs,
        "billed_mins":   billed_secs / 60,
        "cost_usd":      billed_secs * price_per_sec,
        "price_per_min": price_per_sec * 60,
    }

# ─── Hauptprogramm ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="transcribe",
        description=(
            "Automatische Transkription:\n"
            f"  < {LOCAL_THRESHOLD_SECS//60} Min  →  lokal (mlx-whisper)\n"
            f"  ≥ {LOCAL_THRESHOLD_SECS//60} Min  →  GCP Speech-to-Text\n"
            f"  Konfidenz < {CONFIDENCE_THRESHOLD:.0%}  →  GCP-Fallback"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Umgebungsvariablen:\n"
            "  GOOGLE_CLOUD_PROJECT            GCP-Projekt-ID\n"
            "  GOOGLE_APPLICATION_CREDENTIALS  Pfad zur Service-Account-JSON\n"
            "  GOOGLE_API_KEY                  API-Schlüssel\n\n"
            "Beispiele:\n"
            "  transcribe memo.m4a\n"
            "  transcribe memo.m4a --force-gcp --model chirp\n"
            "  transcribe memo.m4a --force-local --no-clipboard\n"
        ),
    )
    parser.add_argument("file",          help="Audiodatei (m4a, mp3, wav, flac, …)")
    parser.add_argument("--language", "-l", default="de-DE",
                        help="BCP-47 Sprachcode (Standard: de-DE)")
    parser.add_argument("--model", "-m", default="long",
                        choices=["long","short","latest_long","latest_short","chirp","chirp_2"],
                        help="GCP-Modell (Standard: long)")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL,
                        help=f"mlx-whisper Modell (Standard: {WHISPER_MODEL})")
    parser.add_argument("--local-threshold", type=int, default=LOCAL_THRESHOLD_SECS,
                        metavar="SECS",
                        help=f"Unter dieser Dauer lokal transkribieren (Standard: {LOCAL_THRESHOLD_SECS}s)")
    parser.add_argument("--confidence-threshold", type=float, default=CONFIDENCE_THRESHOLD,
                        metavar="0-1",
                        help=f"Unter diesem Wert GCP-Fallback (Standard: {CONFIDENCE_THRESHOLD})")
    parser.add_argument("--force-local",  action="store_true",
                        help="Immer lokal transkribieren (kein GCP-Fallback)")
    parser.add_argument("--force-gcp",   action="store_true",
                        help="Immer GCP verwenden")
    parser.add_argument("--no-clipboard", action="store_true",
                        help="Nicht in Zwischenablage kopieren")
    parser.add_argument("--keep-silence", action="store_true",
                        help="Stille nicht entfernen (Standard: wird entfernt)")
    parser.add_argument("--json", action="store_true",
                        help="Strukturierte JSON-Ausgabe")
    parser.add_argument("--text", action="store_true",
                        help="Nur transkribierten Text ausgeben")
    parser.add_argument("--notify", action="store_true",
                        help="macOS-Benachrichtigungen anzeigen (Start, Fallback, Ende)")
    args = parser.parse_args()

    global _NOTIFY_ENABLED
    _NOTIFY_ENABLED = args.notify

    silent  = args.json or args.text
    use_clipboard = not args.no_clipboard

    if not silent:
        print(f"\n{_c(BOLD+MAGENTA, '🎙  Transkription')}\n")

    # ── Datei validieren ──────────────────────────────────────────────
    audio_path = Path(args.file)
    if not audio_path.exists():
        die(f"Datei nicht gefunden: {args.file}")
    if not audio_path.is_file():
        die(f"Kein reguläres File: {args.file}")

    # ── Audio-Metadaten ───────────────────────────────────────────────
    if not silent: info("Lese Audio-Metadaten …")
    meta     = get_audio_info(audio_path)
    duration = meta["duration"]

    if not silent:
        section("Audio")
        row("Datei:",      audio_path.name,        BOLD)
        row("Format:",     meta["format_name"])
        row("Codec:",      meta["codec"])
        row("Dauer:",      fmt_duration(duration),  CYAN)
        row("Samplerate:", f"{meta['sample_rate']:,} Hz")
        row("Kanäle:",     str(meta["channels"]))
        row("Größe:",      fmt_size(meta["size_bytes"]))

    # ── Backend bestimmen ─────────────────────────────────────────────
    if args.force_gcp:
        use_local = False
    elif args.force_local:
        use_local = True
    else:
        use_local = duration < args.local_threshold

    if not silent:
        if use_local:
            info(f"Backend: {_c(BOLD, 'lokal')} "
                 f"{_c(DIM, f'(< {fmt_duration(args.local_threshold)})')}")
        else:
            info(f"Backend: {_c(BOLD, 'GCP Speech-to-Text')} "
                 f"{_c(DIM, f'(≥ {fmt_duration(args.local_threshold)})')}")

    _send_notification(
        "Transcribrr",
        f"{'Lokal' if use_local else 'GCP'} · {fmt_duration(duration)}",
        audio_path.name,
    )

    tmpdir = tempfile.mkdtemp(prefix="transcribe_")
    try:
        result = None

        # ── Lokale Transkription ──────────────────────────────────────
        if use_local:
            if not silent: section("Transkription  (lokal)")
            result = transcribe_local(
                audio_path, args.language, args.whisper_model, silent)

            if not silent:
                conf = result["confidence"]
                bar  = confidence_bar(conf)
                if conf < args.confidence_threshold:
                    warn(
                        f"Konfidenz {bar}  "
                        f"{_c(DIM, f'< {args.confidence_threshold:.0%} Schwelle')}"
                    )
                    if not args.force_local:
                        warn("Starte GCP-Fallback …")
                else:
                    ok(f"Konfidenz {bar}")

            # Fallback auf GCP wenn Konfidenz zu niedrig
            if (result["confidence"] < args.confidence_threshold
                    and not args.force_local):
                _send_notification(
                    "Transcribrr",
                    f"Konfidenz {result['confidence']:.0%} — wechsle zu GCP",
                    audio_path.name,
                )
                use_local = False   # für Ausgabe
                result    = None    # erzwingt GCP-Pfad unten

        # ── GCP Transkription ─────────────────────────────────────────
        if result is None:
            # Stille entfernen
            work_path, work_duration = audio_path, duration
            if not args.keep_silence:
                if not silent:
                    info("Entferne Stille (Pausen > 3s, Anfang/Ende > 0.5s) …")
                processed = Path(tmpdir) / "no_silence.flac"
                strip_silence(audio_path, processed)
                proc_meta     = get_audio_info(processed)
                work_path     = processed
                work_duration = proc_meta["duration"]
                removed       = duration - work_duration
                if not silent:
                    if removed >= 1.0:
                        ok(
                            f"Stille entfernt: {_c(CYAN, fmt_duration(removed))} "
                            f"{_c(DIM, f'({removed/duration:.0%}) → '
                                       f'effektiv: {fmt_duration(work_duration)}')}"
                        )
                    else:
                        ok("Keine nennenswerte Stille gefunden.")

            # Auth
            if not silent: info("Authentifiziere …")
            token, auth_type = get_credentials()
            project          = get_project_id()
            if not silent:
                ok(f"Projekt: {_c(BOLD, project)}  {_c(DIM, f'(via {auth_type})')}")

            if not silent: section("Transkription  (GCP)")
            result = transcribe_gcp(
                work_path, work_duration, tmpdir,
                args.language, args.model, silent,
                project, token, auth_type,
            )
            result["work_duration"] = work_duration
            result["removed_secs"]  = duration - work_duration

        # ── Clipboard ─────────────────────────────────────────────────
        full_text = result["text"]
        if use_clipboard and full_text:
            subprocess.run("pbcopy", input=full_text.encode(), check=True)
            if not silent:
                ok("Transkript in Zwischenablage kopiert.")

        # ── Fertig-Notification ───────────────────────────────────────
        if full_text:
            backend_label = "Lokal" if result["backend"] == "local-whisper" else "GCP"
            _send_notification(
                "Transcribrr — Fertig",
                f"In Zwischenablage · {result['confidence']:.0%} · {result['elapsed']:.1f}s",
                f"{audio_path.name} · {backend_label}",
            )
        else:
            _send_notification(
                "Transcribrr",
                "Keine Sprache erkannt",
                audio_path.name,
            )

        # ── Ausgabe ───────────────────────────────────────────────────
        if args.text:
            print(full_text)
            return

        if args.json:
            out = {
                "transcript":       full_text,
                "confidence":       result["confidence"],
                "backend":          result["backend"],
                "segments":         result["segments"],
                "duration_seconds": duration,
                "processing_seconds": result["elapsed"],
                "language":         args.language,
            }
            if result["backend"] == "gcp":
                out["model"]                       = args.model
                out["effective_duration_seconds"]  = result.get("work_duration", duration)
                out["silence_removed_seconds"]     = result.get("removed_secs", 0)
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        # Fließtext
        if full_text:
            print()
            print(wrap_text(full_text))
        else:
            warn("Keine Sprache erkannt — Transkription leer.")

        # ── Statistiken ───────────────────────────────────────────────
        section("Statistiken" + ("  (GCP)" if result["backend"].startswith("gcp") else "  (lokal)"))

        row("Konfidenz:",       confidence_bar(result["confidence"]))
        row("Audiodauer:",      fmt_duration(duration), CYAN)

        if result["backend"].startswith("gcp"):
            removed = result.get("removed_secs", 0)
            work_dur = result.get("work_duration", duration)
            if removed >= 1.0:
                row("Stille entfernt:",
                    _c(YELLOW, f"−{fmt_duration(removed)}") +
                    _c(DIM, f"  ({removed/duration:.0%})"))
                row("Effektive Dauer:", fmt_duration(work_dur), CYAN)
        else:
            work_dur = duration

        row("Verarbeitungszeit:", f"{result['elapsed']:.1f}s")
        if result["elapsed"] > 0:
            row("Echtzeit-Faktor:",
                _c(GREEN, f"{work_dur/result['elapsed']:.1f}×") + " schneller")
        row("Backend:", result["backend"])
        if result["backend"].startswith("gcp"):
            row("Modell:", args.model)
        else:
            row("Modell:", args.whisper_model.split("/")[-1])
        row("Sprache:", args.language)

        if result["backend"].startswith("gcp"):
            cost = calc_cost(work_dur, args.model)
            print()
            row("Abgerechnet:",
                f"{cost['billed_secs']}s = {cost['billed_mins']:.2f} Min", DIM)
            price_str = f"${cost['price_per_min']:.4f}/Min"
            if cost["cost_usd"] == 0:
                row("Kosten (geschätzt):",
                    _c(GREEN, "$0.00") +
                    _c(DIM, f"  (≤ {FREE_TIER_SECONDS//60} Min/Monat Gratis-Kontingent)"))
            else:
                row("Kosten (geschätzt):",
                    _c(YELLOW, f"${cost['cost_usd']:.6f}") + _c(DIM, f"  ({price_str})"))
            row("Gratis-Kontingent:",
                _c(DIM, f"{FREE_TIER_SECONDS//60} Min/Monat (dann {price_str})"))

        print(f"\n{hr(60, '─', DIM)}\n")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
