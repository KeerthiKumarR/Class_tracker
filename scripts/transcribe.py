import os
import re
import math
import tempfile
import subprocess
import requests
from groq import Groq, RateLimitError, APIError


import sys
import shutil


def get_ffmpeg_bin() -> str:
    """Finds FFmpeg binary across system paths and bundle."""
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", shutil.which("ffmpeg"), "/usr/bin/ffmpeg"]:
        if p and os.path.exists(p):
            return p
    bundle_bin = getattr(sys, '_MEIPASS', None)
    if bundle_bin:
        p = os.path.join(bundle_bin, "bin", "ffmpeg")
        if os.path.exists(p):
            return p
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_embedded = os.path.join(project_root, "embedded_bin", "ffmpeg")
    if os.path.exists(p_embedded):
        return p_embedded
    return "ffmpeg"


def get_ffprobe_bin() -> str:
    """Finds FFprobe binary across system paths and bundle."""
    for p in ["/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", shutil.which("ffprobe"), "/usr/bin/ffprobe"]:
        if p and os.path.exists(p):
            return p
    bundle_bin = getattr(sys, '_MEIPASS', None)
    if bundle_bin:
        p = os.path.join(bundle_bin, "bin", "ffprobe")
        if os.path.exists(p):
            return p
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p_embedded = os.path.join(project_root, "embedded_bin", "ffprobe")
    if os.path.exists(p_embedded):
        return p_embedded
    return "ffprobe"


def _compress_audio(audio_path: str, output_path: str, bitrate: str = "24k") -> None:
    """Compress audio to 16kHz mono MP3 for low bandwidth and lightning fast cloud upload."""
    ffmpeg_bin = get_ffmpeg_bin()
    cmd = [
        ffmpeg_bin,
        "-i", audio_path,
        "-ar", "16000",
        "-ac", "1",
        "-b:a", bitrate,
        output_path,
        "-y",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        if audio_path.lower().endswith(".mp3"):
            import shutil
            shutil.copy2(audio_path, output_path)
        else:
            raise RuntimeError(f"ffmpeg audio compression failed on {audio_path}:\n{res.stderr}")


def _get_audio_duration_seconds(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe or ffmpeg."""
    ffprobe_bin = get_ffprobe_bin()
    ffmpeg_bin = get_ffmpeg_bin()

    # Method 1: Try ffprobe
    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        try:
            val = float(res.stdout.strip())
            if val > 0:
                return val
        except ValueError:
            pass

    # Method 2: Fallback to ffmpeg stderr parsing
    cmd = [ffmpeg_bin, "-i", audio_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
    if match:
        hours = float(match.group(1))
        minutes = float(match.group(2))
        seconds = float(match.group(3))
        return hours * 3600 + minutes * 60 + seconds

    return 0.0


import time


def _transcribe_groq_single_file(client: Groq, file_path: str, model: str, language: str, prompt: str) -> str:
    """Call Groq Whisper API for a single audio file with 3-attempt exponential backoff."""
    lang_param = None if (not language or language.lower() == "auto") else language
    prompt_text = prompt or "C++ Data Structures and Algorithms lecture covering lambdas, syntax, captures by value and reference, mutable, vectors, std::function, classwork, homework, and problem solving."

    last_error = None
    for attempt, delay in enumerate([2, 4, 8], start=1):
        try:
            with open(file_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(os.path.basename(file_path), f.read()),
                    model=model,
                    language=lang_param,
                    prompt=prompt_text,
                    response_format="text",
                )
            return str(response).strip()
        except Exception as e:
            last_error = e
            print(f"[WARN] Groq Whisper API attempt {attempt}/3 failed: {e}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError(f"Groq Whisper transcription failed after 3 attempts: {last_error}")



def transcribe_groq(audio_path: str, api_key: str, model: str = "whisper-large-v3", language: str = "hi") -> str:
    """Transcribe audio using Groq Cloud Whisper API.

    Automatically compresses audio to speech-optimized MP3. For long recordings,
    splits into 10-minute segments (~1.8MB each) to prevent network timeouts and
    ensure reliable, fast processing.
    """
    # 5-minute timeout on client requests to prevent premature timeouts
    client = Groq(api_key=api_key, timeout=300.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        print("[INFO] Compressing audio for fast cloud upload...")
        compressed_mp3 = os.path.join(tmpdir, "compressed.mp3")
        _compress_audio(audio_path, compressed_mp3, bitrate="24k")

        duration = _get_audio_duration_seconds(compressed_mp3)
        file_size = os.path.getsize(compressed_mp3)
        segment_duration = 600  # 10 minutes per chunk (approx 1.8MB per chunk at 24kbps)

        # If file is short (under 10 minutes), send directly
        if duration <= segment_duration and file_size <= (10 * 1024 * 1024):
            print(f"[INFO] Uploading audio ({file_size / (1024*1024):.1f}MB, {duration/60:.1f} mins) to Groq...")
            return _transcribe_groq_single_file(client, compressed_mp3, model, language, prompt=None)

        # Persistent chunk cache directory in ~/.classtracker_cache
        cache_dir = os.path.expanduser("~/.classtracker_cache")
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        cache_key = hashlib.md5(f"{os.path.basename(audio_path)}_{os.path.getsize(audio_path)}".encode()).hexdigest()

        # If file is long, chunk into 10-minute segments
        num_segments = math.ceil(duration / segment_duration) if duration > 0 else max(2, math.ceil(file_size / (5 * 1024 * 1024)))
        print(f"[INFO] Long audio ({duration/60:.1f} mins, {file_size / (1024*1024):.1f}MB). Processing in {num_segments} segments...")
        transcripts = []

        for i in range(num_segments):
            chunk_cache_file = os.path.join(cache_dir, f"{cache_key}_chunk_{i:03d}.txt")
            if os.path.exists(chunk_cache_file):
                try:
                    with open(chunk_cache_file, "r", encoding="utf-8") as cf:
                        cached_text = cf.read().strip()
                    if cached_text:
                        print(f"[INFO] Segment {i+1}/{num_segments} restored from local cache (already transcribed).")
                        transcripts.append(cached_text)
                        continue
                except Exception:
                    pass

            start_sec = i * segment_duration
            chunk_path = os.path.join(tmpdir, f"chunk_{i:03d}.mp3")
            ffmpeg_bin = get_ffmpeg_bin()
            chunk_cmd = [
                ffmpeg_bin,
                "-ss", str(start_sec),
                "-i", compressed_mp3,
                "-t", str(segment_duration),
                "-c", "copy",
                chunk_path,
                "-y",
            ]
            subprocess.run(chunk_cmd, capture_output=True)
            if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
                start_min = int(start_sec // 60)
                end_min = int(min(duration, (i + 1) * segment_duration) // 60)
                print(f"[INFO] Transcribing segment {i+1}/{num_segments} ({start_min}m–{end_min}m) via Groq...")
                seg_text = _transcribe_groq_single_file(client, chunk_path, model, language, prompt=None)
                if seg_text:
                    transcripts.append(seg_text)
                    try:
                        with open(chunk_cache_file, "w", encoding="utf-8") as cf:
                            cf.write(seg_text)
                    except Exception:
                        pass

        full_transcript = " ".join(transcripts).strip()
        # Clean up cache files upon successful completion
        if full_transcript:
            for i in range(num_segments):
                cf_path = os.path.join(cache_dir, f"{cache_key}_chunk_{i:03d}.txt")
                if os.path.exists(cf_path):
                    try:
                        os.remove(cf_path)
                    except Exception:
                        pass

        return full_transcript


def transcribe_sarvam(audio_path: str, sarvam_api_key: str, language_code: str = "hi-IN") -> str:
    """Fallback: Transcribe audio using Sarvam AI Speech-to-Text API."""
    url = "https://api.sarvam.ai/speech-to-text"
    headers = {"api-subscription-key": sarvam_api_key}

    with tempfile.TemporaryDirectory() as tmpdir:
        compressed_wav = os.path.join(tmpdir, "sarvam.wav")
        ffmpeg_bin = get_ffmpeg_bin()
        cmd = [ffmpeg_bin, "-i", audio_path, "-ar", "16000", "-ac", "1", compressed_wav, "-y"]
        subprocess.run(cmd, capture_output=True)

        with open(compressed_wav, "rb") as f:
            files = {"file": (os.path.basename(compressed_wav), f, "audio/wav")}
            data = {
                "model": "saaras:v2",
                "language_code": language_code or "hi-IN",
                "with_diarization": "false",
            }
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=300)
            if resp.status_code != 200:
                raise RuntimeError(f"Sarvam AI API error ({resp.status_code}): {resp.text}")
            result_json = resp.json()
            return result_json.get("transcript", "").strip()


def transcribe_audio(
    audio_path: str,
    config: dict,
    language: str = "hi",
) -> str:
    """Cloud transcription dispatcher:

    1. Groq Cloud Whisper (Primary)
    2. Sarvam AI (Fallback, if configured)
    """
    groq_api_key = config.get("GROQ_API_KEY")
    groq_whisper_model = config.get("WHISPER_GROQ_MODEL", "whisper-large-v3")
    sarvam_api_key = config.get("SARVAM_API_KEY")

    # Tier 1: Groq Cloud Whisper
    if groq_api_key:
        try:
            print(f"[INFO] Transcribing via Groq Cloud ({groq_whisper_model}) in language '{language}'...")
            return transcribe_groq(audio_path, groq_api_key, model=groq_whisper_model, language=language)
        except (RateLimitError, APIError, Exception) as e:
            print(f"[WARN] Groq Cloud transcription encountered an issue: {e}")
            print("[INFO] Attempting fallback transcription provider...")

    # Tier 2: Sarvam AI (if key is set)
    if sarvam_api_key and sarvam_api_key.strip():
        try:
            print("[INFO] Transcribing via Sarvam AI fallback...")
            sarvam_lang = "hi-IN" if language in ("hi", "auto") else f"{language}-IN"
            return transcribe_sarvam(audio_path, sarvam_api_key, language_code=sarvam_lang)
        except Exception as e:
            print(f"[WARN] Sarvam AI transcription failed: {e}")

    raise RuntimeError("All transcription providers (Groq, Sarvam) failed or are not configured.")
