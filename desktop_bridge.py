import os
import time
import threading
import logging
import scipy.io.wavfile as wavfile
import sounddevice as sd
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import logger, BASE_DIR, RECORDINGS_DIR, AI_PERSONA, TTS_VOICE, ADB_MODE, ADB_WIRELESS_IP, ADB_WIRELESS_PORT, AI_ENABLED, DIRECT_MIC_DEVICE, DIRECT_SPEAKER_DEVICE, save_env_settings
from database import DatabaseManager
from phone_controller import PhoneController
from audio_router import AudioRouter, list_audio_devices
from ai_engine import AIEngine

class DesktopBridge:
    """
    Native JavaScript-to-Python API Bridge for pywebview.
    Exposes ONLY public methods to window.pywebview.api without any HTTP/REST server overhead.
    Private attributes start with `_` to prevent pywebview from reflecting native COM objects recursively.
    """

    def __init__(self, window_ref=None):
        self._window = window_ref
        self._db = DatabaseManager()
        self._phone = PhoneController()
        self._router = AudioRouter()
        self._ai = AIEngine()

        self._monitor_active = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._active_call_thread: Optional[threading.Thread] = None
        self._stop_current_call_flag = False
        self._injected_ai_text: Optional[str] = None

        # System settings & AI Mode local state
        self._ai_enabled = AI_ENABLED
        self._direct_mic_device = DIRECT_MIC_DEVICE
        self._direct_speaker_device = DIRECT_SPEAKER_DEVICE
        self._current_persona = AI_PERSONA
        self._current_voice = TTS_VOICE
        self._current_call_context = ""
        self._speaker_test_active = False
        self._speaker_test_thread: Optional[threading.Thread] = None

        # Apply default voice configuration to AI engine
        self._ai.set_voice(self._current_voice)

    def set_window(self, window):
        """Sets the reference to pywebview window once initialized."""
        self._window = window

    def emit_event(self, event_type: str, data: Dict[str, Any]):
        """Executes a JavaScript callback inside the pywebview browser container."""
        if self._window:
            try:
                import json
                json_payload = json.dumps(data)
                js_code = f"if (window.onDesktopEvent) window.onDesktopEvent('{event_type}', {json_payload});"
                self._window.evaluate_js(js_code)
            except Exception as e:
                logger.error("Failed to emit UI event %s: %s", event_type, e)

    # ---------------------------------------------------------
    # System & Connectivity APIs
    # ---------------------------------------------------------
    def get_system_status(self) -> Dict[str, Any]:
        """Returns ADB connection status, device list, call state, audio devices, and monitor status."""
        adb_status = self._phone.get_connection_status() # 'CONNECTED', 'UNAUTHORIZED', 'DISCONNECTED'
        devices = self._phone.get_connected_devices()
        target_id = self._phone.get_target_device_id() or "Auto-Detect Phone"
        call_state = self._phone.get_call_state() if adb_status == "CONNECTED" else "DISCONNECTED"

        return {
            "adb_connected": (adb_status == "CONNECTED"),
            "adb_status": adb_status,
            "adb_mode": self._phone.mode,
            "target_id": target_id,
            "devices": devices,
            "call_state": call_state,
            "monitor_active": self._monitor_active,
            "wireless_ip": ADB_WIRELESS_IP,
            "wireless_port": ADB_WIRELESS_PORT,
            "ai_enabled": self._ai_enabled,
            "ai_persona": self._current_persona,
            "tts_voice": self._current_voice,
            "audio_input_device": str(self._router.input_device) if self._router.input_device is not None else "default",
            "audio_output_device": str(self._router.output_device) if self._router.output_device is not None else "default",
            "direct_mic_device": str(self._direct_mic_device) if self._direct_mic_device is not None else "default",
            "direct_speaker_device": str(self._direct_speaker_device) if self._direct_speaker_device is not None else "default"
        }

    def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Returns detailed list of all connected ADB devices."""
        return self._phone.get_connected_devices()

    def select_adb_device(self, serial: str) -> Dict[str, Any]:
        """Selects target ADB device serial."""
        self._phone.select_device(serial)
        return self.get_system_status()

    def set_adb_mode(self, mode: str) -> Dict[str, Any]:
        """Switch between 'usb' and 'wireless' ADB modes."""
        self._phone.set_mode(mode.lower())
        return self.get_system_status()

    def restart_adb_server(self) -> Dict[str, Any]:
        """Restarts the local ADB server daemon to fix hung USB drivers."""
        self._phone.restart_adb()
        return self.get_system_status()

    def connect_wireless_adb(self, ip: str, port: str = "5555") -> Dict[str, Any]:
        """Connect to Wireless ADB IP:Port."""
        success = self._phone.connect_wireless(ip=ip, port=port)
        return {"success": success, "status": self.get_system_status()}

    def pair_wireless_adb(self, ip: str, port: str, code: str) -> Dict[str, Any]:
        """Pair with Wireless ADB."""
        success = self._phone.pair_wireless(ip=ip, pairing_port=port, pairing_code=code)
        return {"success": success}

    def launch_scrcpy_audio_bridge(self) -> Dict[str, Any]:
        """Launches scrcpy.exe with audio forwarding automatically in a background process."""
        import subprocess
        scrcpy_executable = BASE_DIR / "scrcpy-win64-v4.1" / "scrcpy.exe"
        if not scrcpy_executable.exists():
            found = list(BASE_DIR.glob("scrcpy*/scrcpy.exe"))
            if found:
                scrcpy_executable = found[0]

        if not scrcpy_executable.exists():
            return {"success": False, "error": "scrcpy.exe not found in project directory."}

        try:
            cmd = [str(scrcpy_executable), "--no-video", "--audio-source=mic-voice-communication"]
            subprocess.Popen(cmd, cwd=str(scrcpy_executable.parent))
            logger.info("Launched scrcpy audio bridge process: %s", cmd)
            return {"success": True, "message": "SUCCESS: Scrcpy Audio Bridge launched!"}
        except Exception as e:
            logger.error("Failed to launch scrcpy audio bridge: %s", e)
            return {"success": False, "error": str(e)}

    def list_audio_devices(self) -> List[Dict[str, Any]]:
        """List soundcard input and output devices."""
        return list_audio_devices()

    # ---------------------------------------------------------
    # Call Control & Session Management APIs
    # ---------------------------------------------------------
    def toggle_ai_mode(self, enabled: bool) -> Dict[str, Any]:
        """Toggles AI Assistant mode on or off."""
        self._ai_enabled = bool(enabled)
        save_env_settings(ai_enabled=self._ai_enabled)
        logger.info("AI Assistant Mode set to: %s", self._ai_enabled)
        return {"success": True, "ai_enabled": self._ai_enabled}

    def start_outbound_call(self, phone_number: str, call_context: str = "") -> Dict[str, Any]:
        """Initiates an outbound call session in a background thread with target context prompt."""
        if not phone_number or len(phone_number.strip()) < 3:
            return {"success": False, "error": "Invalid phone number"}

        if self._active_call_thread and self._active_call_thread.is_alive():
            return {"success": False, "error": "A call session is already in progress"}

        self._stop_current_call_flag = False
        self._current_call_context = call_context.strip()
        success = self._phone.make_call(phone_number.strip())
        if not success:
            return {"success": False, "error": "Could not establish call via ADB"}

        self._active_call_thread = threading.Thread(
            target=self._dispatch_call_session,
            args=(phone_number.strip(), "OUTGOING"),
            daemon=True
        )
        self._active_call_thread.start()
        return {"success": True}

    def end_call(self) -> Dict[str, Any]:
        """Hangs up the active phone call."""
        self._stop_current_call_flag = True
        self._phone.end_call()
        self.emit_event("call_state_change", {"state": "IDLE", "message": "Call ended by user"})
        return {"success": True}

    def inject_ai_response(self, custom_text: str) -> Dict[str, Any]:
        """Intervene and force AI to speak custom text."""
        if custom_text and custom_text.strip():
            self._injected_ai_text = custom_text.strip()
            return {"success": True}
        return {"success": False}

    def toggle_monitor_mode(self, enable: bool) -> Dict[str, Any]:
        """Toggles incoming call listener loop on or off."""
        if enable:
            if not self._monitor_active:
                self._monitor_active = True
                self._monitor_thread = threading.Thread(target=self._monitor_incoming_loop, daemon=True)
                self._monitor_thread.start()
        else:
            self._monitor_active = False

        return {"monitor_active": self._monitor_active}

    # ---------------------------------------------------------
    # Internal Call Engine Thread Logic
    # ---------------------------------------------------------
    def _monitor_incoming_loop(self):
        """Background thread monitoring for incoming phone calls."""
        logger.info("Started background incoming call monitor thread.")
        self.emit_event("monitor_status", {"active": True})

        while self._monitor_active:
            try:
                state = self._phone.get_call_state()
                self.emit_event("call_state_change", {"state": state})

                if state == "RINGING":
                    caller_number = self._phone.get_incoming_number()
                    self.emit_event("incoming_call", {"number": caller_number})
                    time.sleep(1)
                    if self._phone.answer_call():
                        self._dispatch_call_session(phone_number=caller_number, call_type="INCOMING")
                time.sleep(2)
            except Exception as e:
                logger.error("Error in monitor loop: %s", e)
                time.sleep(2)

        self.emit_event("monitor_status", {"active": False})
        logger.info("Stopped background incoming call monitor thread.")

    def _dispatch_call_session(self, phone_number: str, call_type: str):
        """Dispatches call session to AI dialogue loop or Direct Headset Audio bridge based on ai_enabled state."""
        if self._ai_enabled:
            self._run_call_session(phone_number=phone_number, call_type=call_type)
        else:
            self._run_direct_call_session(phone_number=phone_number, call_type=call_type)

    def _run_direct_call_session(self, phone_number: str, call_type: str):
        """Runs a direct manual phone call with real-time PC Headset Mic & Speaker audio pass-through (AI Turned OFF)."""
        logger.info("Starting Direct PC Headset Call Session (AI Turned OFF)...")
        call_id = self._db.start_call(phone_number=phone_number, call_type=call_type)
        self.emit_event("call_started", {
            "call_id": call_id,
            "phone_number": phone_number,
            "call_type": call_type,
            "state": "CONNECTED",
            "ai_enabled": False
        })
        self.emit_event("transcript", {
            "call_id": call_id,
            "speaker": "AI",
            "message": "🎧 Direct PC Headset Call Mode Active (AI Turned OFF). Converse directly using your PC Headphones & Microphone."
        })

        recorded_chunks = []
        # Launch real-time full-duplex audio bridge
        self._router.start_direct_audio_bridge(
            direct_mic=self._direct_mic_device,
            direct_speaker=self._direct_speaker_device,
            recorded_chunks_list=recorded_chunks
        )

        recording_filename = f"call_direct_{call_id}_{int(time.time())}.wav"
        recording_path = RECORDINGS_DIR / recording_filename

        while not self._stop_current_call_flag:
            current_state = self._phone.get_call_state()
            if current_state == "IDLE":
                logger.info("Direct call disconnected.")
                break

            mic_energy, caller_energy = self._router.get_direct_audio_energy()
            self.emit_event("audio_energy", {"caller": caller_energy, "ai": mic_energy})
            time.sleep(0.1)

        # Stop audio bridge
        self._router.stop_direct_audio_bridge()

        # Save call recording if audio captured
        full_audio_saved_path = ""
        if recorded_chunks:
            try:
                combined_audio = np.concatenate(recorded_chunks, axis=0)
                wavfile.write(str(recording_path), 16000, combined_audio)
                full_audio_saved_path = str(recording_path)
                logger.info("Saved direct call recording to %s", full_audio_saved_path)
            except Exception as ex:
                logger.error("Failed to save direct call recording: %s", ex)

        self._db.end_call(
            call_id,
            summary="Direct manual call using PC headset (AI turned off).",
            intent="Direct Call",
            audio_path=full_audio_saved_path
        )

        self.emit_event("call_ended", {
            "call_id": call_id,
            "phone_number": phone_number,
            "summary": "Direct PC Headset Call (AI Disabled)",
            "intent": "Direct Call",
            "audio_path": full_audio_saved_path
        })
        self.emit_event("call_state_change", {"state": "IDLE"})

    def _run_call_session(self, phone_number: str, call_type: str):
        """Full active dialogue session with live STT, TTS, recording, and events."""
        call_id = self._db.start_call(phone_number=phone_number, call_type=call_type)
        self.emit_event("call_started", {
            "call_id": call_id,
            "phone_number": phone_number,
            "call_type": call_type,
            "state": "CONNECTED"
        })

        recording_filename = f"call_{call_id}_{int(time.time())}.wav"
        recording_path = RECORDINGS_DIR / recording_filename
        recorded_chunks = []

        initial_greeting = "Hello! I am an AI assistant calling on behalf of my user. How can I help you today?"
        if call_type == "INCOMING":
            initial_greeting = "Hello! Thank you for calling. How can I assist you today?"

        self._db.add_transcript(call_id, "AI", initial_greeting)
        self.emit_event("transcript", {"call_id": call_id, "speaker": "AI", "message": initial_greeting})

        # Synthesize & play initial greeting
        greeting_audio = self._ai.synthesize_speech(initial_greeting, voice=self._current_voice)
        if greeting_audio:
            try:
                sr, data = wavfile.read(greeting_audio)
                recorded_chunks.append(data)
            except Exception:
                pass

            self._router.play_audio_file(greeting_audio)
            try:
                os.remove(greeting_audio)
            except Exception:
                pass

        history = [{"speaker": "AI", "message": initial_greeting}]
        silence_count = 0

        while not self._stop_current_call_flag:
            current_state = self._phone.get_call_state()
            if current_state == "IDLE":
                logger.info("Call disconnected by remote party.")
                break

            # 1. Check for manual intervention speech injection
            if self._injected_ai_text:
                custom_reply = self._injected_ai_text
                self._injected_ai_text = None
                self._db.add_transcript(call_id, "AI", custom_reply)
                self.emit_event("transcript", {"call_id": call_id, "speaker": "AI", "message": custom_reply})
                history.append({"speaker": "AI", "message": custom_reply})
                inj_audio = self._ai.synthesize_speech(custom_reply, voice=self._current_voice)
                if inj_audio:
                    self._router.play_audio_file(inj_audio)
                    try:
                        os.remove(inj_audio)
                    except Exception:
                        pass
                continue

            # 2. Record caller audio chunk & measure energy level
            self.emit_event("audio_energy", {"caller": 0.2, "ai": 0.0})
            audio_wav, speech_detected = self._router.record_audio_chunk(duration_seconds=4.0)

            if audio_wav and os.path.exists(audio_wav):
                try:
                    sr, chunk_data = wavfile.read(audio_wav)
                    recorded_chunks.append(chunk_data)
                except Exception:
                    pass

            if not speech_detected:
                silence_count += 1
                if silence_count >= 5:
                    recheck_msg = "Are you still there?"
                    self._db.add_transcript(call_id, "AI", recheck_msg)
                    self.emit_event("transcript", {"call_id": call_id, "speaker": "AI", "message": recheck_msg})
                    history.append({"speaker": "AI", "message": recheck_msg})
                    recheck_audio = self._ai.synthesize_speech(recheck_msg, voice=self._current_voice)
                    if recheck_audio:
                        self._router.play_audio_file(recheck_audio)
                        try:
                            os.remove(recheck_audio)
                        except Exception:
                            pass
                    silence_count = 0
                continue

            silence_count = 0

            # 3. Transcribe caller audio (STT)
            logger.info("Running speech-to-text transcription on caller audio...")
            caller_text = self._ai.transcribe_audio(audio_wav)
            if not caller_text:
                logger.info("Speech recognition: No text transcribed (silence or low volume).")
                continue

            self._db.add_transcript(call_id, "CALLER", caller_text)
            self.emit_event("transcript", {"call_id": call_id, "speaker": "CALLER", "message": caller_text})
            history.append({"speaker": "CALLER", "message": caller_text})

            # Check for hangup keywords
            if any(w in caller_text.lower() for w in ["bye", "goodbye", "hang up", "catch you later"]):
                farewell = "Thank you. Have a great day! Goodbye."
                self._db.add_transcript(call_id, "AI", farewell)
                self.emit_event("transcript", {"call_id": call_id, "speaker": "AI", "message": farewell})
                farewell_audio = self._ai.synthesize_speech(farewell, voice=self._current_voice)
                if farewell_audio:
                    self._router.play_audio_file(farewell_audio)
                    try:
                        os.remove(farewell_audio)
                    except Exception:
                        pass
                self._phone.end_call()
                break

            # 4. Generate AI response (LLM)
            ai_reply = self._ai.generate_response(
                caller_text, 
                history, 
                system_prompt=self._current_persona,
                call_context=getattr(self, "_current_call_context", "")
            )
            self._db.add_transcript(call_id, "AI", ai_reply)
            self.emit_event("transcript", {"call_id": call_id, "speaker": "AI", "message": ai_reply})
            history.append({"speaker": "AI", "message": ai_reply})

            # 5. Synthesize & play AI response (TTS)
            self.emit_event("audio_energy", {"caller": 0.0, "ai": 0.8})
            reply_audio = self._ai.synthesize_speech(ai_reply, voice=self._current_voice)
            if reply_audio:
                try:
                    sr, reply_data = wavfile.read(reply_audio)
                    recorded_chunks.append(reply_data)
                except Exception:
                    pass
                self._router.play_audio_file(reply_audio)
                try:
                    os.remove(reply_audio)
                except Exception:
                    pass

        # Save concatenated full call recording
        full_audio_saved_path = ""
        if recorded_chunks:
            try:
                combined_audio = np.concatenate(recorded_chunks, axis=0)
                wavfile.write(str(recording_path), 16000, combined_audio)
                full_audio_saved_path = str(recording_path)
                logger.info("Saved call recording to %s", full_audio_saved_path)
            except Exception as ex:
                logger.error("Failed to save call recording: %s", ex)

        # Post-call summarize
        transcripts = self._db.get_call_transcripts(call_id)
        summary, intent = self._ai.summarize_call(transcripts)
        self._db.end_call(call_id, summary=summary, intent=intent, audio_path=full_audio_saved_path)

        self.emit_event("call_ended", {
            "call_id": call_id,
            "phone_number": phone_number,
            "summary": summary,
            "intent": intent,
            "audio_path": full_audio_saved_path
        })
        self.emit_event("call_state_change", {"state": "IDLE"})

    # ---------------------------------------------------------
    # History & Analytics APIs
    # ---------------------------------------------------------
    def get_call_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetches call records from database."""
        return self._db.get_call_history(limit=limit)

    def get_call_transcripts(self, call_id: int) -> List[Dict[str, Any]]:
        """Fetches call dialogue history for specified call ID."""
        return self._db.get_call_transcripts(call_id)

    # ---------------------------------------------------------
    # Settings & Persona APIs
    # ---------------------------------------------------------
    def get_voice_catalogue(self) -> List[Dict[str, Any]]:
        """Returns the list of available TTS voices grouped by engine."""
        return self._ai.get_voice_catalogue()

    def update_ai_settings(
        self,
        persona: str,
        voice: str,
        input_device: str = "",
        output_device: str = "",
        ai_enabled: bool = True,
        direct_mic_device: str = "",
        direct_speaker_device: str = ""
    ) -> Dict[str, Any]:
        """Update system instructions prompt, voice settings, audio device routing, and AI engine mode."""
        self._ai_enabled = bool(ai_enabled)
        if persona and persona.strip():
            self._current_persona = persona.strip()
        if voice and voice.strip():
            self._current_voice = voice.strip()
            self._ai.set_voice(voice.strip())
        
        # Configure input / output devices dynamically by string name
        if input_device:
            self._router.input_device = input_device
        if output_device:
            self._router.output_device = output_device
        if direct_mic_device:
            self._direct_mic_device = direct_mic_device
        if direct_speaker_device:
            self._direct_speaker_device = direct_speaker_device

        save_env_settings(
            persona=self._current_persona,
            voice=self._current_voice,
            input_dev=str(self._router.input_device),
            output_dev=str(self._router.output_device),
            ai_enabled=self._ai_enabled,
            direct_mic=str(self._direct_mic_device),
            direct_speaker=str(self._direct_speaker_device)
        )

        return {
            "success": True,
            "ai_enabled": self._ai_enabled,
            "persona": self._current_persona,
            "voice": self._current_voice,
            "input_device": str(self._router.input_device) if self._router.input_device is not None else "default",
            "output_device": str(self._router.output_device) if self._router.output_device is not None else "default",
            "direct_mic_device": str(self._direct_mic_device) if self._direct_mic_device is not None else "default",
            "direct_speaker_device": str(self._direct_speaker_device) if self._direct_speaker_device is not None else "default"
        }

    def test_tts_voice(self, sample_text: str, voice: str) -> Dict[str, Any]:
        """Generates and plays a quick TTS test audio clip in a background thread."""
        text = sample_text or "Hello! This is a test of the selected AI neural voice."
        v = voice or self._current_voice

        def run_test():
            try:
                audio_file = self._ai.synthesize_speech(text, voice=v)
                if audio_file:
                    self._router.play_audio_file(audio_file)
                    try:
                        os.remove(audio_file)
                    except Exception:
                        pass
            except Exception as e:
                logger.error("TTS test voice error: %s", e)

        threading.Thread(target=run_test, daemon=True).start()
        return {"success": True, "message": "TTS Voice test started in background"}

    def test_audio_loopback(self) -> Dict[str, Any]:
        """
        Plays a test tone to the designated Audio Output device, records from the Audio Input
        device simultaneously in a background thread, and emits diagnostic result event.
        """
        logger.info("Starting Audio Loopback Diagnostic Test in background worker thread...")

        def run_loopback():
            import sounddevice as sd
            import numpy as np

            in_idx = self._router._get_device_index(self.input_device if hasattr(self, 'input_device') else self._router.input_device, "input")
            out_idx = self._router._get_device_index(self.output_device if hasattr(self, 'output_device') else self._router.output_device, "output")
            
            try:
                device_info = sd.query_devices(in_idx, 'input')
                samplerate = int(device_info['default_samplerate'])
            except Exception:
                samplerate = 16000

            duration = 1.5  # seconds
            num_frames = int(duration * samplerate)

            t = np.linspace(0, duration, num_frames, endpoint=False)
            test_tone = 0.4 * np.sin(2 * np.pi * 440 * t)
            test_tone_pcm = (test_tone * 32767).astype(np.int16)

            recorded = None
            exception_msg = ""

            try:
                recorded = sd.playrec(
                    test_tone_pcm,
                    samplerate=samplerate,
                    channels=1,
                    dtype='int16',
                    device=(in_idx, out_idx)
                )
                sd.wait()
            except Exception as e:
                logger.error("Audio loopback test failed to execute: %s", e)
                exception_msg = str(e)

            if recorded is None:
                self.emit_event("diagnostic_result", {"type": "loopback", "success": False, "message": f"Audio engine failed: {exception_msg}"})
                return

            audio_data = recorded.astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(audio_data**2)))
            peak = float(np.max(np.abs(audio_data)))
            success = rms > 0.003

            logger.info("Loopback Diagnostic: Success=%s | RMS=%.4f | Peak=%.4f", success, rms, peak)
            msg = "SUCCESS: Audio Loopback SUCCESS! The microphone successfully heard the speaker output." if success else "WARNING: Audio loopback failed. No signal returned. Please ensure VB-Audio Cable is correctly set up."
            self.emit_event("diagnostic_result", {"type": "loopback", "success": success, "rms": rms, "message": msg})

        threading.Thread(target=run_loopback, daemon=True).start()
        return {"success": True, "message": "Loopback test started in background"}

    def auto_configure_audio_routing(self) -> Dict[str, Any]:
        """
        Automatically scans connected soundcard devices and assigns the appropriate
        VB-Cable CABLE Input and CABLE Output device names, ensuring they both use
        the exact same Host API (prioritizing WASAPI for stability).
        """
        logger.info("Automatically configuring VB-Audio Cable routing...")
        devices = self.list_audio_devices()
        
        # Prioritized APIs list: WASAPI > DirectSound > MME > WDM-KS
        target_apis = ["Windows WASAPI", "Windows DirectSound", "MME", "Windows WDM-KS"]
        
        selected_input = None
        selected_output = None
        selected_api = None
        
        for api in target_apis:
            api_devices = [d for d in devices if d.get("host_api") == api]
            
            # Find input device containing 'cable output' or 'cable'
            cable_in = None
            for dev in api_devices:
                name = dev["name"].lower()
                if dev["max_input_channels"] > 0 and ("cable output" in name or "cable-output" in name or "cable" in name):
                    cable_in = dev["name"]
                    break
                    
            # Find output device containing 'cable input' or 'cable'
            cable_out = None
            for dev in api_devices:
                name = dev["name"].lower()
                if dev["max_output_channels"] > 0 and ("cable input" in name or "cable-input" in name or "cable" in name):
                    cable_out = dev["name"]
                    break
                    
            if cable_in and cable_out:
                selected_input = cable_in
                selected_output = cable_out
                selected_api = api
                break
                
        if selected_input and selected_output:
            self._router.input_device = selected_input
            self._router.output_device = selected_output
            save_env_settings(input_dev=selected_input, output_dev=selected_output)
            logger.info("Auto-configured audio routing successfully on Host API '%s'. Input: '%s' | Output: '%s'", 
                        selected_api, selected_input, selected_output)
            return {
                "success": True,
                "input_device": selected_input,
                "output_device": selected_output,
                "message": f"SUCCESS: Auto-configured VB-Cable on {selected_api}!\nInput: {selected_input}\nOutput: {selected_output}"
            }
            
        return {
            "success": False,
            "error": "VB-Audio Cable pair was not found on any host API driver. Please ensure it is installed and enabled."
        }

    def start_telephony_speaker_test(self) -> Dict[str, Any]:
        """
        Synthesizes a test voice message and loops it in a background thread to the
        configured output device so the user can check if they hear it on their phone.
        """
        if self._speaker_test_active:
            return {"success": True, "message": "Speaker test is already active"}

        logger.info("Initializing speaker diagnostics...")
        self._speaker_test_active = True
        
        def play_loop():
            # Generate a test speech clip
            text = "Testing telephony loopback. This is a repeating audio test message to verify the speaker channel connection."
            voice = self._current_voice
            
            try:
                audio_file = self._ai.synthesize_speech(text, voice=voice)
            except Exception as se:
                logger.error("Failed to synthesize test speech: %s. Using default tone instead.", se)
                audio_file = None
                
            import numpy as np
            import sounddevice as sd
            import time

            out_idx = self._router._get_device_index(self._router.output_device, "output")
            
            if audio_file and os.path.exists(audio_file):
                while self._speaker_test_active:
                    logger.info("Playing speaker test speech loop...")
                    self._router.play_audio_file(audio_file, stop_check_func=lambda: self._speaker_test_active)
                    # Pause for 1.0 seconds between loops
                    for _ in range(10):
                        if not self._speaker_test_active:
                            break
                        time.sleep(0.1)
                try:
                    os.remove(audio_file)
                except Exception:
                    pass
            else:
                # Fallback: Play repeating 1-second 440Hz beep tones
                samplerate = 16000
                duration = 1.0
                t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
                beep = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
                
                while self._speaker_test_active:
                    logger.info("Playing speaker test tone beep...")
                    try:
                        sd.play(beep, samplerate=samplerate, device=out_idx)
                        sd.wait()
                    except Exception as e:
                        logger.error("Beep test failed: %s", e)
                        break
                    # Wait 1.0s between beeps
                    for _ in range(10):
                        if not self._speaker_test_active:
                            break
                        time.sleep(0.1)
                        
            logger.info("Speaker diagnostic thread stopped.")

        self._speaker_test_thread = threading.Thread(target=play_loop, daemon=True)
        self._speaker_test_thread.start()
        
        return {"success": True, "message": "Diagnostic speech loop started."}

    def stop_telephony_speaker_test(self) -> Dict[str, Any]:
        """Stops the repeating speaker test audio loop."""
        self._speaker_test_active = False
        try:
            import sounddevice as sd
            sd.stop()
        except Exception as e:
            logger.warning("Error stopping sounddevice: %s", e)

        self._speaker_test_thread = None
        logger.info("Speaker diagnostics stopped.")
        return {"success": True, "message": "Diagnostic speech loop stopped."}

    def run_telephony_mic_test(self) -> Dict[str, Any]:
        """
        Records 3.5 seconds from the configured input channel in a background thread
        and emits the measured volume level to the UI.
        """
        logger.info("Starting downstream telephony microphone capture test in background thread...")
        
        def run_mic_capture():
            temp_path, _ = self._router.record_audio_chunk(duration_seconds=3.5, silence_threshold=0.0)
            if not temp_path or not os.path.exists(temp_path):
                self.emit_event("diagnostic_result", {"type": "mic", "success": False, "volume_percent": 0, "message": "Failed to open input stream or record audio."})
                return
                
            import wave
            import numpy as np
            
            try:
                with wave.open(temp_path, "rb") as wf:
                    params = wf.getparams()
                    frames = wf.readframes(params.nframes)
                    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                    
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                    
                rms = float(np.sqrt(np.mean(data**2)))
                peak = float(np.max(np.abs(data)))
                volume_percent = min(100, int((rms / 0.1) * 100))
                success = rms > 0.0003
                
                logger.info("Mic Diagnostic: Success=%s | RMS=%.5f | Peak=%.5f | Volume=%s%%", success, rms, peak, volume_percent)
                
                msg = f"SUCCESS: Captured phone microphone signal (volume: {volume_percent}%)." if success else "⚠️ Silence detected (0% volume). Check Scrcpy audio forwarding or AUX cable."
                self.emit_event("diagnostic_result", {
                    "type": "mic",
                    "success": success,
                    "rms": rms,
                    "volume_percent": volume_percent,
                    "message": msg
                })
            except Exception as e:
                logger.error("Failed to read mic test wav: %s", e)
                self.emit_event("diagnostic_result", {"type": "mic", "success": False, "volume_percent": 0, "message": f"Error parsing recording: {e}"})

        threading.Thread(target=run_mic_capture, daemon=True).start()
        return {"success": True, "message": "Microphone test started in background"}
