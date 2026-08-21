import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Base Directory
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file
load_dotenv(BASE_DIR / ".env")

# Logging Configuration
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "ai_calling.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("AICalling")

# API Keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Audio Configuration
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "default")
AUDIO_OUTPUT_DEVICE = os.getenv("AUDIO_OUTPUT_DEVICE", "default")
DIRECT_MIC_DEVICE = os.getenv("DIRECT_MIC_DEVICE", "default")
DIRECT_SPEAKER_DEVICE = os.getenv("DIRECT_SPEAKER_DEVICE", "default")
SAMPLE_RATE = 16000  # 16 kHz sample rate standard for voice telephony
CHANNELS = 1         # Mono audio

# AI Engine Configuration
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() in ("true", "1", "yes")

# ADB Configuration
import shutil

def find_adb_path() -> str:
    env_path = os.getenv("ADB_PATH", "adb")
    if shutil.which(env_path):
        return env_path
    
    # Check for bundled scrcpy adb.exe in project root
    for item in BASE_DIR.glob("scrcpy*/adb.exe"):
        if item.exists():
            return str(item)
    return env_path

ADB_PATH = find_adb_path()
ADB_DEVICE_SERIAL = os.getenv("ADB_DEVICE_SERIAL", "")
ADB_MODE = os.getenv("ADB_MODE", "USB").upper() # "USB" or "WIRELESS"
ADB_WIRELESS_IP = os.getenv("ADB_WIRELESS_IP", "")
ADB_WIRELESS_PORT = os.getenv("ADB_WIRELESS_PORT", "5555")

# AI Persona & TTS Configuration
AI_PERSONA = os.getenv(
    "AI_PERSONA",
    "You are an intelligent, polite AI voice assistant answering a phone call. "
    "Keep responses conversational, natural, short (1-2 sentences), and easy to understand over audio."
)
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")  # Microsoft Edge TTS voice

# Database & Storage Paths
DB_PATH = BASE_DIR / "data" / "calls.db"
DB_PATH.parent.mkdir(exist_ok=True)

RECORDINGS_DIR = BASE_DIR / "data" / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

def save_env_settings(
    persona: str = None,
    voice: str = None,
    input_dev: str = None,
    output_dev: str = None,
    mode: str = None,
    ip: str = None,
    port: str = None,
    ai_enabled: bool = None,
    direct_mic: str = None,
    direct_speaker: str = None
) -> bool:
    """Persists updated system configuration parameters back to .env file."""
    try:
        env_file = BASE_DIR / ".env"
        env_dict = {}

        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line_str = line.strip()
                    if line_str and not line_str.startswith("#") and "=" in line_str:
                        key, val = line_str.split("=", 1)
                        env_dict[key.strip()] = val.strip()

        if persona is not None:
            # Clean newlines for env formatting
            clean_p = persona.replace("\n", " ").strip()
            env_dict["AI_PERSONA"] = f'"{clean_p}"'
        if voice is not None:
            env_dict["TTS_VOICE"] = voice.strip()
        if input_dev is not None:
            env_dict["AUDIO_INPUT_DEVICE"] = input_dev.strip()
        if output_dev is not None:
            env_dict["AUDIO_OUTPUT_DEVICE"] = output_dev.strip()
        if mode is not None:
            env_dict["ADB_MODE"] = mode.strip().upper()
        if ip is not None:
            env_dict["ADB_WIRELESS_IP"] = ip.strip()
        if port is not None:
            env_dict["ADB_WIRELESS_PORT"] = str(port).strip()
        if ai_enabled is not None:
            env_dict["AI_ENABLED"] = "true" if ai_enabled else "false"
        if direct_mic is not None:
            env_dict["DIRECT_MIC_DEVICE"] = direct_mic.strip()
        if direct_speaker is not None:
            env_dict["DIRECT_SPEAKER_DEVICE"] = direct_speaker.strip()

        with open(env_file, "w", encoding="utf-8") as f:
            for k, v in env_dict.items():
                f.write(f"{k}={v}\n")
        
        logger.info("Successfully updated .env settings.")
        return True
    except Exception as e:
        logger.error("Failed to write to .env file: %s", e)
        return False

