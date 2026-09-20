import os
import asyncio
import tempfile
import threading
from typing import List, Dict, Tuple, Optional
import speech_recognition as sr
import edge_tts
from google import genai
from google.genai import types
from zai import ZaiClient

from config import (
    GEMINI_API_KEY, AI_PERSONA, TTS_VOICE, logger,
    AI_PROVIDER, ZAI_API_KEY, ZAI_MODEL, ZAI_BASE_URL,
)

# ---- Kokoro TTS lazy loader (imported only if installed) ----
_kokoro_pipeline = None
_kokoro_lock = threading.Lock()

def _get_kokoro_pipeline(lang_code: str = 'a'):
    """Lazily initializes the Kokoro TTS pipeline (singleton per process)."""
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        with _kokoro_lock:
            if _kokoro_pipeline is None:
                try:
                    from kokoro import KPipeline
                    _kokoro_pipeline = KPipeline(lang_code=lang_code)
                    logger.info("Kokoro TTS pipeline initialized (lang_code='%s').", lang_code)
                except ImportError:
                    logger.warning("Kokoro TTS not installed. Run: pip install kokoro soundfile")
                    _kokoro_pipeline = False  # Mark as unavailable
    return _kokoro_pipeline if _kokoro_pipeline is not False else None


# Voice catalogue exposed to the UI
VOICE_CATALOGUE = [
    # Kokoro voices (grouped)
    {"engine": "kokoro", "id": "af_heart",   "label": "❤️ Heart – US Female (Warm)"},
    {"engine": "kokoro", "id": "af_sarah",   "label": "🌸 Sarah – US Female (Friendly)"},
    {"engine": "kokoro", "id": "af_bella",   "label": "✨ Bella – US Female (Expressive)"},
    {"engine": "kokoro", "id": "am_liam",    "label": "🎙️ Liam – US Male (Clear)"},
    {"engine": "kokoro", "id": "am_adam",    "label": "💼 Adam – US Male (Professional)"},
    {"engine": "kokoro", "id": "bf_emma",    "label": "🇬🇧 Emma – UK Female (Formal)"},
    {"engine": "kokoro", "id": "bm_george",  "label": "🇬🇧 George – UK Male (Authoritative)"},
    {"engine": "kokoro", "id": "hf_alpha",   "label": "🇮🇳 Kokoro Hindi – Female"},
    {"engine": "kokoro", "id": "hm_omega",   "label": "🇮🇳 Kokoro Hindi – Male"},
    # Edge-TTS voices (Hindi + Global)
    {"engine": "edge",   "id": "hi-IN-SwaraNeural",       "label": "🇮🇳 Swara – Edge Hindi Female (Highly Realistic)"},
    {"engine": "edge",   "id": "hi-IN-MadhurNeural",      "label": "🇮🇳 Madhur – Edge Hindi Male (Natural)"},
    {"engine": "edge",   "id": "hi-IN-KavyaNeural",       "label": "🇮🇳 Kavya – Edge Hindi Female"},
    {"engine": "edge",   "id": "en-US-ChristopherNeural", "label": "🔵 Christopher – Edge US Male"},
    {"engine": "edge",   "id": "en-US-JennyNeural",       "label": "🔵 Jenny – Edge US Female"},
    {"engine": "edge",   "id": "en-US-AriaNeural",        "label": "🔵 Aria – Edge US Female"},
    {"engine": "edge",   "id": "en-GB-SoniaNeural",       "label": "🔵 Sonia – Edge UK Female"},
    {"engine": "edge",   "id": "en-AU-WilliamNeural",     "label": "🔵 William – Edge AU Male"},
]


class AIEngine:
    """Orchestrates STT, Gemini LLM, and multi-engine TTS (Kokoro + Edge-TTS)."""

    def __init__(
        self,
        api_key: str = GEMINI_API_KEY,
        persona: str = AI_PERSONA,
        voice: str = TTS_VOICE,
        provider: str = AI_PROVIDER,
    ):
        self.api_key = api_key
        self.persona = persona
        self.voice = voice
        self.provider = provider.lower()
        self.recognizer = sr.Recognizer()

        # Determine engine from voice id
        self._resolve_engine(voice)

        self.genai_client = None
        self.zai_client = None

        if self.provider == "zai":
            if ZAI_API_KEY:
                self.zai_client = ZaiClient(api_key=ZAI_API_KEY, base_url=ZAI_BASE_URL)
                logger.info("Z.ai LLM Client initialized successfully (model=%s, base_url=%s).", ZAI_MODEL, ZAI_BASE_URL)
            else:
                logger.warning("AI_PROVIDER=zai but ZAI_API_KEY is not set. LLM responses will use fallback rules.")
        else:
            if self.api_key:
                self.genai_client = genai.Client(api_key=self.api_key)
                logger.info("Gemini API Client initialized successfully.")
            else:
                logger.warning("GEMINI_API_KEY is not set. LLM responses will use fallback rules.")

    @property
    def _llm_ready(self) -> bool:
        return self.genai_client is not None or self.zai_client is not None

    def _resolve_engine(self, voice_id: str):
        """Sets self.engine and self.voice based on the given voice ID."""
        self.voice = voice_id
        # Check if voice is in Kokoro catalogue
        for v in VOICE_CATALOGUE:
            if v["id"] == voice_id:
                self.engine = v["engine"]
                return
        # Default: if it looks like an Edge voice ID, use edge
        self.engine = "edge" if "-Neural" in voice_id else "kokoro"

    def set_voice(self, voice_id: str):
        """Dynamically switches the TTS voice and engine."""
        self._resolve_engine(voice_id)
        logger.info("TTS voice changed to: %s (engine: %s)", self.voice, self.engine)

    # ------------------------------------------------------------------
    # Speech-to-Text
    # ------------------------------------------------------------------
    def transcribe_audio(self, wav_file_path: str) -> str:
        """Transcribes audio from a WAV file into text using Google Speech Recognition."""
        if not wav_file_path or not os.path.exists(wav_file_path):
            return ""

        try:
            with sr.AudioFile(wav_file_path) as source:
                audio = self.recognizer.record(source)

            text = self.recognizer.recognize_google(audio)
            logger.info("Transcribed Speech: '%s'", text)
            return text.strip()

        except sr.UnknownValueError:
            logger.debug("Speech recognition could not understand audio (silence/noise).")
            return ""
        except sr.RequestError as e:
            logger.error("Could not request results from Speech Recognition service: %s", str(e))
            return ""
        except Exception as e:
            logger.error("Error transcribing audio: %s", str(e))
            return ""
        finally:
            if os.path.exists(wav_file_path):
                try:
                    os.remove(wav_file_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # LLM Response Generation
    # ------------------------------------------------------------------
    def generate_response(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        call_context: Optional[str] = None
    ) -> str:
        """
        Generates a conversational response using Gemini LLM.
        Accepts optional per-call context (e.g. 'Sell this product: X').
        """
        if not user_message:
            return "I didn't quite catch that. Could you please repeat?"

        if not self._llm_ready:
            return "Thank you for calling. I am an automated assistant. How can I assist you today?"

        active_persona = system_prompt or self.persona

        # Build conversation history block (last 6 turns only for speed)
        history_text = ""
        for turn in conversation_history[-6:]:
            history_text += f"{turn['speaker']}: {turn['message']}\n"

        # Inject pre-call mission context if provided
        context_block = ""
        if call_context and call_context.strip():
            context_block = (
                f"\n[CALL MISSION BRIEF - Follow this context strictly]:\n"
                f"{call_context.strip()}\n"
            )

        prompt = (
            f"{active_persona}"
            f"{context_block}\n"
            f"Recent Conversation:\n{history_text}\n"
            f"Caller: {user_message}\n"
            f"AI (1-2 short sentences only, natural phone speech):"
        )

        try:
            if self.zai_client:
                completion = self.zai_client.chat.completions.create(
                    model=ZAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.75,
                    max_tokens=200,
                    reasoning_effort="low",
                )
                ai_text = (completion.choices[0].message.content or "").strip()
            else:
                response = self.genai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0.75,
                        max_output_tokens=80  # Short responses = faster TTS + more natural conversation
                    )
                )
                ai_text = response.text.strip() if response.text else ""

            ai_text = ai_text or "I understand. Could you tell me more?"
            logger.info("Generated AI Response: '%s'", ai_text)
            return ai_text

        except Exception as e:
            logger.error("%s API generation error: %s", self.provider, str(e))
            return "I apologize, I'm having a little trouble right now."

    # ------------------------------------------------------------------
    # Text-to-Speech: Kokoro (primary) + Edge-TTS (fallback)
    # ------------------------------------------------------------------
    def synthesize_speech(self, text: str, voice: Optional[str] = None) -> str:
        """
        Converts text to an audio file using the configured TTS engine.
        Returns the path to the generated audio file (WAV or MP3).
        """
        if not text:
            return ""

        active_voice = voice or self.voice
        # Re-resolve engine if a voice override is given
        engine = self.engine
        for v in VOICE_CATALOGUE:
            if v["id"] == active_voice:
                engine = v["engine"]
                break

        if engine == "kokoro":
            return self._synthesize_kokoro(text, active_voice)
        else:
            return self._synthesize_edge_tts(text, active_voice)

    def _synthesize_kokoro(self, text: str, voice: str) -> str:
        """Generates speech using Kokoro TTS (local, CPU, ~150ms latency)."""
        try:
            import soundfile as sf
            import numpy as np

            lang_code = 'a'
            if voice.startswith('h'):
                lang_code = 'h'
            elif voice.startswith('b'):
                lang_code = 'b'
            elif voice.startswith('j'):
                lang_code = 'j'
            elif voice.startswith('e'):
                lang_code = 'e'
            elif voice.startswith('f'):
                lang_code = 'f'

            pipeline = _get_kokoro_pipeline(lang_code=lang_code)
            if pipeline is None:
                logger.warning("Kokoro not available, falling back to Edge-TTS.")
                return self._synthesize_edge_tts(text, "hi-IN-SwaraNeural" if lang_code == 'h' else "en-US-ChristopherNeural")

            # Generate audio chunks
            audio_chunks = []
            generator = pipeline(text, voice=voice, speed=1.0)
            for _, _, audio in generator:
                if audio is not None:
                    audio_chunks.append(audio)

            if not audio_chunks:
                return ""

            combined = np.concatenate(audio_chunks) if len(audio_chunks) > 1 else audio_chunks[0]

            # Save to temp WAV file (24kHz, Kokoro default)
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            sf.write(tmp.name, combined, 24000)
            logger.info("Kokoro TTS synthesis complete: voice=%s, file=%s", voice, tmp.name)
            return tmp.name

        except Exception as e:
            logger.error("Kokoro TTS error: %s. Falling back to Edge-TTS.", e)
            return self._synthesize_edge_tts(text, "en-US-ChristopherNeural")

    def _synthesize_edge_tts(self, text: str, voice: str) -> str:
        """Generates speech using Microsoft Edge Neural TTS (online, free)."""
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        try:
            async def _run():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(tmp.name)

            asyncio.run(_run())
            logger.info("Edge-TTS synthesis complete: voice=%s", voice)
            return tmp.name
        except Exception as e:
            logger.error("Edge-TTS synthesis error: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Voice Catalogue
    # ------------------------------------------------------------------
    def get_voice_catalogue(self) -> List[Dict]:
        """Returns available TTS voices with engine labels for the UI selector."""
        return VOICE_CATALOGUE

    # ------------------------------------------------------------------
    # Call Summarization
    # ------------------------------------------------------------------
    def summarize_call(self, transcripts: List[Dict[str, str]]) -> Tuple[str, str]:
        """Generates a structured call summary and identifies caller intent using Gemini."""
        if not transcripts:
            return "No conversation recorded.", "Unknown"

        if not self._llm_ready:
            full_text = " ".join([f"{t['speaker']}: {t['message']}" for t in transcripts])
            return f"Call record: {full_text[:200]}...", "General Inquiry"

        try:
            transcript_formatted = "\n".join([f"{t['speaker']}: {t['message']}" for t in transcripts])
            prompt = (
                "Analyze the following call transcript and return a JSON object with two fields:\n"
                "1. 'summary': A concise 2-3 sentence summary of what was discussed and agreed upon.\n"
                "2. 'intent': A short label (e.g. Appointment Booking, Sales Inquiry, Customer Support, Spam, Follow-up).\n\n"
                f"Transcript:\n{transcript_formatted}\n"
                "Respond with JSON only."
            )

            import json

            if self.zai_client:
                completion = self.zai_client.chat.completions.create(
                    model=ZAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    max_tokens=400,
                    reasoning_effort="low",
                )
                raw_text = completion.choices[0].message.content or "{}"
            else:
                response = self.genai_client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                raw_text = response.text if response.text else "{}"

            data = json.loads(raw_text)
            summary = data.get("summary", "Summary unavailable.")
            intent = data.get("intent", "General")
            logger.info("Call Summary Generated: Intent='%s'", intent)
            return summary, intent

        except Exception as e:
            logger.error("Failed to generate call summary: %s", str(e))
            return "Call summary generation failed.", "General"
