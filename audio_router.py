import time
import os
import wave
import tempfile
import threading
import sounddevice as sd
import numpy as np
from typing import Optional, List, Dict, Tuple, Any
from config import AUDIO_INPUT_DEVICE, AUDIO_OUTPUT_DEVICE, SAMPLE_RATE, CHANNELS, logger

def list_audio_devices() -> List[Dict[str, Any]]:
    """Returns a list of all audio input and output devices connected to the PC with host API names."""
    devices = sd.query_devices()
    try:
        host_apis = sd.query_hostapis()
    except Exception:
        host_apis = []
        
    device_list = []
    for idx, dev in enumerate(devices):
        api_idx = dev.get("hostapi", 0)
        api_name = host_apis[api_idx]["name"] if api_idx < len(host_apis) else "Unknown"
        
        device_list.append({
            "index": idx,
            "name": dev["name"],
            "host_api": api_name,
            "max_input_channels": dev["max_input_channels"],
            "max_output_channels": dev["max_output_channels"],
            "default_samplerate": dev["default_samplerate"]
        })
    return device_list

class AudioRouter:
    """Handles low-latency audio capture from phone speaker stream and playback to mic stream."""

    def __init__(
        self,
        input_device: Optional[str] = AUDIO_INPUT_DEVICE,
        output_device: Optional[str] = AUDIO_OUTPUT_DEVICE,
        samplerate: int = SAMPLE_RATE,
        channels: int = CHANNELS
    ):
        self.input_device = input_device
        self.output_device = output_device
        self.samplerate = samplerate
        self.channels = channels

        # Direct audio bridge state
        self._bridge_active = False
        self._bridge_thread: Optional[threading.Thread] = None
        self._energy_mic = 0.0
        self._energy_caller = 0.0

        logger.info("AudioRouter initialized. Input Device: %s | Output Device: %s", self.input_device, self.output_device)

    def _get_device_index(self, dev_name_or_id: Any, kind: str) -> Optional[int]:
        """Resolves the current device index dynamically by matching the name."""
        if dev_name_or_id in (None, "", "default", "None"):
            return None
            
        dev_str = str(dev_name_or_id).strip()
        devices = sd.query_devices()
        
        # Match by name substring
        for idx, dev in enumerate(devices):
            if dev_str.lower() in dev["name"].lower():
                if (kind == "input" and dev["max_input_channels"] > 0) or \
                   (kind == "output" and dev["max_output_channels"] > 0):
                    return idx
                    
        # Digit fallback
        if dev_str.isdigit():
            idx = int(dev_str)
            if idx < len(devices):
                return idx
                
        logger.warning("Audio device '%s' (%s) not found by name. Falling back to default.", dev_str, kind)
        return None

    def _parse_device(self, dev_str: str, kind: str) -> Optional[int]:
        """Legacy helper. Redirects to new dynamic index resolver."""
        return self._get_device_index(dev_str, kind)

    def record_audio_chunk(self, duration_seconds: float = 4.0, silence_threshold: float = 0.0015) -> Tuple[str, bool]:
        """
        Records audio from the input channel with real-time Voice Activity Detection (VAD).
        Stops early as soon as the user finishes speaking (silence after speech).
        Resolves device index and native samplerate dynamically to prevent out-of-range & samplerate errors.
        """
        in_device_idx = self._get_device_index(self.input_device, "input")
        
        # Query native sample rate of the selected device
        try:
            device_info = sd.query_devices(in_device_idx, 'input')
            device_samplerate = int(device_info['default_samplerate'])
            logger.info("Selected input device index: %s | Native samplerate: %s Hz", in_device_idx, device_samplerate)
        except Exception as e:
            logger.warning("Could not query input device metadata: %s. Using default 16000 Hz.", e)
            device_samplerate = 16000

        logger.info("Recording caller audio stream (with VAD early-stop)...")
        chunk_size = 1024
        recorded_data = []
        
        speech_started = False
        silence_frames = 0
        
        # 1.0s of continuous silence after speech started triggers early stop
        max_silence_frames = int(1.0 * device_samplerate / chunk_size)
        max_total_frames = int(duration_seconds * device_samplerate)
        total_frames_recorded = 0
        
        try:
            with sd.InputStream(
                samplerate=device_samplerate,
                channels=self.channels,
                dtype="int16",
                device=in_device_idx,
                blocksize=chunk_size
            ) as stream:
                while total_frames_recorded < max_total_frames:
                    data, overflowed = stream.read(chunk_size)
                    recorded_data.append(data)
                    total_frames_recorded += len(data)
                    
                    # Compute RMS of this chunk
                    audio_chunk = data.astype(np.float32) / 32768.0
                    rms = np.sqrt(np.mean(audio_chunk**2))
                    
                    if rms > silence_threshold:
                        if not speech_started:
                            speech_started = True
                            logger.info("🎙️ User speech detected (RMS: %.5f > threshold %.5f). Recording...", rms, silence_threshold)
                        silence_frames = 0
                    else:
                        if speech_started:
                            silence_frames += 1
                            if silence_frames >= max_silence_frames:
                                logger.info("🤫 User finished speaking. Silence detected. Stopping recording early.")
                                break
            
            if not recorded_data:
                logger.warning("No audio data was recorded.")
                return "", False
                
            recording = np.concatenate(recorded_data, axis=0)
            
            # Resample to 16000 Hz standard if native device rate differs
            if device_samplerate != 16000:
                try:
                    logger.info("Software resampling recorded audio from %s Hz to 16000 Hz...", device_samplerate)
                    from scipy.signal import resample
                    new_num_samples = int(len(recording) * 16000 / device_samplerate)
                    recording = resample(recording, new_num_samples).astype(np.int16)
                except Exception as re:
                    logger.error("Scipy resample failed: %s. Using simple decimation fallback.", re)
                    step = int(device_samplerate / 16000)
                    if step > 1:
                        recording = recording[::step]
            
            # Check overall RMS
            audio_data = recording.astype(np.float32) / 32768.0
            overall_rms = np.sqrt(np.mean(audio_data**2))
            has_speech = speech_started or (overall_rms > silence_threshold)
            
            # Digital Volume Amplification: Boost low telephony signals for STT accuracy
            if has_speech:
                max_val = np.max(np.abs(recording))
                if 0 < max_val < 16000: # If audio level is low, normalize peak to 24000 (~75% max int16)
                    scale = 24000.0 / max_val
                    recording = np.clip(recording.astype(np.float32) * scale, -32767, 32767).astype(np.int16)
                    logger.info("Applied digital audio gain scaling (%.2fx boost) for Speech Recognition.", scale)

            # Save recording to temp WAV file
            temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            temp_path = temp_file.name
            temp_file.close()
            
            with wave.open(temp_path, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)
                wf.setframerate(16000) # Save standard 16kHz
                wf.writeframes(recording.tobytes())
                
            if overall_rms == 0.0:
                logger.warning("============================================================")
                logger.warning("[AudioCapture Alert] ABSOLUTE SILENCE (RMS: 0.00000) on input '%s'.", self.input_device)
                logger.warning("  - Root Cause: Scrcpy call audio is playing to your PC Speakers instead of VB-Cable!")
                logger.warning("  - Quick Fix 1: In Windows Sound Settings -> Volume Mixer, set Scrcpy Output to 'CABLE Input'.")
                logger.warning("  - Quick Fix 2: Or select 'Default System Input' or your physical Microphone in Settings.")
                logger.warning("============================================================")
                
            logger.info("Audio recording completed. Overall RMS: %.5f | Speech detected: %s", overall_rms, has_speech)
            return temp_path, has_speech
            
        except Exception as e:
            logger.error("Error recording audio: %s", str(e))
            return "", False

    def play_audio_file(self, file_path: str, stop_check_func=None) -> bool:
        """Plays an audio file (WAV/MP3) through the designated audio output channel back to caller with safe non-blocking cancellation."""
        if not os.path.exists(file_path):
            logger.error("[AudioPlayback] Target audio file does not exist: %s", file_path)
            return False

        try:
            import soundfile as sf
            data, samplerate = sf.read(file_path)
            duration = len(data) / float(samplerate)
            
            # Compute signal peak & RMS energy
            audio_float = data.astype(np.float32)
            if audio_float.ndim > 1:
                audio_float = np.mean(audio_float, axis=1)
            rms = float(np.sqrt(np.mean(audio_float**2)))
            peak = float(np.max(np.abs(audio_float)))

            # Resolve output device index & metadata
            out_device_idx = self._get_device_index(self.output_device, "output")
            dev_name = "Default System Output"
            host_api = "Default API"
            
            if out_device_idx is not None:
                try:
                    dev_info = sd.query_devices(out_device_idx, 'output')
                    dev_name = dev_info.get("name", "Unknown")
                    host_api_idx = dev_info.get("hostapi", 0)
                    host_apis = sd.query_hostapis()
                    if host_api_idx < len(host_apis):
                        host_api = host_apis[host_api_idx].get("name", "Unknown")
                except Exception:
                    pass

            logger.info("============================================================")
            logger.info("[AudioPlayback] Initiating AI Voice Transmission")
            logger.info("  - Target Output Device: [%s] '%s' (Driver: %s)", out_device_idx if out_device_idx is not None else "Default", dev_name, host_api)
            logger.info("  - Audio Source File: %s", file_path)
            logger.info("  - Sample Rate: %d Hz | Duration: %.2f sec | Signal RMS: %.4f | Peak: %.4f", samplerate, duration, rms, peak)
            
            if "cable input" in dev_name.lower() or "cable" in dev_name.lower():
                logger.info("  - Audio Route Note: Streaming to VB-Audio Cable Input virtual line.")
                logger.info("    (Note: If testing on PC speakers, enable 'Listen to this device' on CABLE Output in Windows Sound Control Panel)")
            elif "hands-free" in dev_name.lower() or "headset" in dev_name.lower():
                logger.info("  - Audio Route Note: Streaming wirelessly over Bluetooth Hands-Free AG Audio to phone!")
            else:
                logger.info("  - Audio Route Note: Streaming to physical PC soundcard output endpoint '%s'.", dev_name)

            logger.info("============================================================")

            # Play using sounddevice
            sd.play(data, samplerate=samplerate, device=out_device_idx)

            # Safe non-blocking loop checking for user cancellation every 50ms
            start_time = time.time()
            interrupted = False
            
            while (time.time() - start_time) < duration:
                if stop_check_func and not stop_check_func():
                    try:
                        sd.stop()
                    except Exception:
                        pass
                    interrupted = True
                    logger.info("[AudioPlayback] Playback cleanly stopped by user request.")
                    break
                time.sleep(0.05)

            if not interrupted:
                try:
                    sd.wait()
                except Exception:
                    pass
                logger.info("[AudioPlayback] Playback completed successfully (Duration: %.2fs).", duration)
            return True

        except Exception as e:
            logger.error("[AudioPlayback] CRITICAL: Audio playback failed on device '%s': %s", self.output_device, str(e), exc_info=True)
            return False

    def start_direct_audio_bridge(
        self,
        direct_mic: Optional[str] = None,
        direct_speaker: Optional[str] = None,
        recorded_chunks_list: Optional[list] = None
    ) -> bool:
        """
        Launches real-time full-duplex audio pass-through bridging for Direct PC Headset Calling Mode (AI OFF).
        - Stream 1 (Upstream): PC Headset Mic -> Phone Input (e.g. CABLE Input / Bluetooth)
        - Stream 2 (Downstream): Phone Call Output -> PC Headset Speaker (e.g. CABLE Output / Headphones)
        """
        if self._bridge_active:
            logger.warning("Direct audio bridge is already active.")
            return True

        self._bridge_active = True
        
        def bridge_worker():
            mic_idx = self._get_device_index(direct_mic, "input")
            phone_in_idx = self._get_device_index(self.input_device, "input")
            phone_out_idx = self._get_device_index(self.output_device, "output")
            spk_idx = self._get_device_index(direct_speaker, "output")

            logger.info("============================================================")
            logger.info("[Direct Audio Bridge] Starting Direct Headset Call Pass-through")
            logger.info("  - Headset Mic Device: [%s] -> Phone Input Device: [%s]", mic_idx, phone_out_idx)
            logger.info("  - Phone Call Output: [%s] -> Headset Speaker Device: [%s]", phone_in_idx, spk_idx)
            logger.info("============================================================")

            samplerate = 16000
            blocksize = 1024

            stream_upstream = None
            if mic_idx is not None or phone_out_idx is not None:
                try:
                    def upstream_callback(indata, outdata, frames, time_info, status):
                        if not self._bridge_active:
                            outdata.fill(0)
                            return
                        outdata[:] = indata
                        audio_float = indata.astype(np.float32) / 32768.0
                        self._energy_mic = float(np.sqrt(np.mean(audio_float**2)))

                    stream_upstream = sd.Stream(
                        device=(mic_idx, phone_out_idx),
                        samplerate=samplerate,
                        channels=1,
                        dtype='int16',
                        blocksize=blocksize,
                        callback=upstream_callback
                    )
                    stream_upstream.start()
                    logger.info("[Direct Audio Bridge] Upstream Mic -> Phone Output active.")
                except Exception as e:
                    logger.error("Failed to start upstream audio stream (Mic -> Phone): %s", e)

            stream_downstream = None
            if phone_in_idx is not None or spk_idx is not None:
                if phone_in_idx != spk_idx:
                    try:
                        def downstream_callback(indata, outdata, frames, time_info, status):
                            if not self._bridge_active:
                                outdata.fill(0)
                                return
                            outdata[:] = indata
                            audio_float = indata.astype(np.float32) / 32768.0
                            self._energy_caller = float(np.sqrt(np.mean(audio_float**2)))
                            if recorded_chunks_list is not None:
                                recorded_chunks_list.append(indata.copy())

                        stream_downstream = sd.Stream(
                            device=(phone_in_idx, spk_idx),
                            samplerate=samplerate,
                            channels=1,
                            dtype='int16',
                            blocksize=blocksize,
                            callback=downstream_callback
                        )
                        stream_downstream.start()
                        logger.info("[Direct Audio Bridge] Downstream Phone -> Speaker active.")
                    except Exception as e:
                        logger.error("Failed to start downstream audio stream (Phone -> PC Speaker): %s", e)

            while self._bridge_active:
                time.sleep(0.1)

            if stream_upstream:
                try:
                    stream_upstream.stop()
                    stream_upstream.close()
                except Exception:
                    pass
            if stream_downstream:
                try:
                    stream_downstream.stop()
                    stream_downstream.close()
                except Exception:
                    pass

            logger.info("[Direct Audio Bridge] Bridge stopped cleanly.")

        self._bridge_thread = threading.Thread(target=bridge_worker, daemon=True)
        self._bridge_thread.start()
        return True

    def stop_direct_audio_bridge(self):
        """Stops the real-time audio bridge thread."""
        self._bridge_active = False
        self._energy_mic = 0.0
        self._energy_caller = 0.0

    def is_bridge_active(self) -> bool:
        """Returns True if direct audio bridge is active."""
        return self._bridge_active

    def get_direct_audio_energy(self) -> Tuple[float, float]:
        """Returns (mic_energy, caller_energy)."""
        return self._energy_mic, self._energy_caller

