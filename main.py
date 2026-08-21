import sys
import time
import argparse
import os
from typing import List, Dict, Any

from config import logger, GEMINI_API_KEY
from database import DatabaseManager
from phone_controller import PhoneController
from audio_router import AudioRouter, list_audio_devices
from ai_engine import AIEngine

__version__ = "1.0.0"

def print_banner():
    print("=" * 60)
    print(f"      SIMVox AI v{__version__} (Android + PC Telephony)")
    print("=" * 60)

def check_preflight_environment(require_api_key: bool = True) -> bool:
    """Validates required environment configurations before initiating calls."""
    if require_api_key:
        if not GEMINI_API_KEY or GEMINI_API_KEY.strip() in ("your_gemini_api_key_here", "AIzaSyYourGeminiApiKeyHere", ""):
            print("\n[!] CONFIGURATION WARNING: GEMINI_API_KEY is missing or unconfigured.")
            print("    Please add your Google Gemini API key to your .env file.")
            print("    Get a free key from: https://aistudio.google.com/\n")
            return False
    return True


def handle_list_audio():
    print("\n--- Available Audio Input/Output Devices ---")
    devices = list_audio_devices()
    for dev in devices:
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]
        kind = []
        if in_ch > 0:
            kind.append(f"INPUT ({in_ch} ch)")
        if out_ch > 0:
            kind.append(f"OUTPUT ({out_ch} ch)")
        print(f"[{dev['index']}] {dev['name']} -> {', '.join(kind)}")
    print("-------------------------------------------\n")

def handle_check_adb(phone: PhoneController):
    print("\n--- ADB Device & Telephony Check ---")
    target_id = phone.get_target_device_id() or "Default USB Device"
    print(f"Active Mode: {phone.mode} | Target: {target_id}")
    if phone.is_connected():
        state = phone.get_call_state()
        print(f"Status: CONNECTED | Current Call State: {state}")
    else:
        print("Status: DISCONNECTED. Check USB cable or Wireless ADB IP/Port.")
    print("------------------------------------\n")

def handle_connect_wireless(phone: PhoneController, target: str):
    print(f"\n[+] Connecting to Wireless ADB target: {target}...")
    parts = target.split(":")
    ip = parts[0]
    port = parts[1] if len(parts) > 1 else "5555"

    success = phone.connect_wireless(ip=ip, port=port)
    if success:
        print(f"[✓] Successfully connected to Wireless ADB: {ip}:{port}")
    else:
        print(f"[✗] Failed to connect to {ip}:{port}. Make sure Wireless Debugging is ON in Android Developer Options.")

def handle_pair_wireless(phone: PhoneController, ip: str, port: str, code: str):
    print(f"\n[+] Pairing Wireless ADB with {ip}:{port} using code {code}...")
    success = phone.pair_wireless(ip=ip, pairing_port=port, pairing_code=code)
    if success:
        print(f"[✓] Successfully paired with {ip}:{port}!")
    else:
        print(f"[✗] Pairing failed for {ip}:{port}.")

def run_call_session(phone: PhoneController, db: DatabaseManager, router: AudioRouter, ai: AIEngine, phone_number: str, call_type: str):
    """Executes a real-time AI conversation during an active call."""
    call_id = db.start_call(phone_number=phone_number, call_type=call_type)
    print(f"\n[+] Active Call Session #{call_id} with {phone_number}")

    # Initial AI greeting
    initial_greeting = "Hello! I am an AI assistant calling on behalf of my user. How can I help you today?"
    if call_type == "INCOMING":
        initial_greeting = "Hello! Thank you for calling. How can I assist you today?"

    # Play initial greeting
    db.add_transcript(call_id, "AI", initial_greeting)
    print(f"[AI]: {initial_greeting}")
    greeting_audio = ai.synthesize_speech(initial_greeting)
    if greeting_audio:
        router.play_audio_file(greeting_audio)
        try:
            os.remove(greeting_audio)
        except Exception:
            pass

    history = [{"speaker": "AI", "message": initial_greeting}]
    silence_count = 0

    try:
        while True:
            # Check if phone call is still active
            current_state = phone.get_call_state()
            if current_state == "IDLE":
                print("\n[-] Call disconnected by remote party.")
                break

            # 1. Listen to caller audio stream
            print("\n[Listening to caller...]")
            audio_wav, speech_detected = router.record_audio_chunk(duration_seconds=4.0)

            if not speech_detected:
                silence_count += 1
                if silence_count >= 5: # If silence for ~20 seconds
                    print("[Silence detected. Asking caller if still on line...]")
                    recheck_msg = "Are you still there?"
                    db.add_transcript(call_id, "AI", recheck_msg)
                    history.append({"speaker": "AI", "message": recheck_msg})
                    recheck_audio = ai.synthesize_speech(recheck_msg)
                    if recheck_audio:
                        router.play_audio_file(recheck_audio)
                        try:
                            os.remove(recheck_audio)
                        except Exception:
                            pass
                    silence_count = 0
                continue

            silence_count = 0

            # 2. Transcribe caller audio (STT)
            caller_text = ai.transcribe_audio(audio_wav)
            if not caller_text:
                continue

            print(f"[CALLER]: {caller_text}")
            db.add_transcript(call_id, "CALLER", caller_text)
            history.append({"speaker": "CALLER", "message": caller_text})

            # Check for hangup keywords
            if any(w in caller_text.lower() for w in ["bye", "goodbye", "hang up", "catch you later"]):
                farewell = "Thank you. Have a great day! Goodbye."
                print(f"[AI]: {farewell}")
                db.add_transcript(call_id, "AI", farewell)
                farewell_audio = ai.synthesize_speech(farewell)
                if farewell_audio:
                    router.play_audio_file(farewell_audio)
                    try:
                        os.remove(farewell_audio)
                    except Exception:
                        pass
                phone.end_call()
                break

            # 3. Generate AI response (LLM)
            ai_reply = ai.generate_response(caller_text, history)
            print(f"[AI]: {ai_reply}")
            db.add_transcript(call_id, "AI", ai_reply)
            history.append({"speaker": "AI", "message": ai_reply})

            # 4. Synthesize AI voice & stream to phone mic (TTS)
            reply_audio = ai.synthesize_speech(ai_reply)
            if reply_audio:
                router.play_audio_file(reply_audio)
                try:
                    os.remove(reply_audio)
                except Exception:
                    pass

    except KeyboardInterrupt:
        print("\n[!] Call manually interrupted by user.")
        phone.end_call()

    # Post-call processing: summarize call and save
    print("\n[+] Summarizing call details...")
    transcripts = db.get_call_transcripts(call_id)
    summary, intent = ai.summarize_call(transcripts)
    db.end_call(call_id, summary=summary, intent=intent)

    print("\n" + "=" * 50)
    print(f"CALL SUMMARY [ID #{call_id}]")
    print(f"Phone Number: {phone_number}")
    print(f"Intent Label: {intent}")
    print(f"Summary: {summary}")
    print("=" * 50 + "\n")

def handle_monitor_mode(phone: PhoneController, db: DatabaseManager, router: AudioRouter, ai: AIEngine):
    """Listens in a continuous loop for incoming Android calls."""
    check_preflight_environment(require_api_key=True)
    print(f"\n[+] Monitoring for incoming calls in [{phone.mode}] mode... (Press Ctrl+C to stop)")
    if not phone.is_connected():
        print(f"[!] Warning: ADB target [{phone.get_target_device_id() or 'USB'}] not detected. Please verify connection.")

    try:
        while True:
            state = phone.get_call_state()
            if state == "RINGING":
                caller_number = phone.get_incoming_number()
                print(f"\n[!] INCOMING CALL DETECTED from: {caller_number}")
                time.sleep(1)
                if phone.answer_call():
                    run_call_session(phone, db, router, ai, phone_number=caller_number, call_type="INCOMING")
                else:
                    print("[-] Failed to answer call.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n[+] Stopped monitoring.")

def handle_outbound_call(phone: PhoneController, db: DatabaseManager, router: AudioRouter, ai: AIEngine, number: str):
    """Initiates an outbound call and runs AI session."""
    if not check_preflight_environment(require_api_key=True):
        return

    print(f"\n[+] Initiating outbound call to {number} via [{phone.mode}] ADB...")
    if not phone.is_connected():
        print(f"[!] Error: ADB target [{phone.get_target_device_id() or 'USB'}] not connected.")
        return

    success = phone.make_call(number)
    if success:
        run_call_session(phone, db, router, ai, phone_number=number, call_type="OUTGOING")
    else:
        print("[-] Could not establish call.")

def handle_view_history(db: DatabaseManager):
    print("\n--- Recent Call Records ---")
    records = db.get_call_history(limit=10)
    if not records:
        print("No calls recorded yet.")
        return

    for rec in records:
        print(f"Call #{rec['id']} | {rec['call_type']} | Number: {rec['phone_number']} | Duration: {rec['duration_seconds'] or 0}s")
        print(f"  Intent: {rec['intent'] or 'N/A'}")
        print(f"  Summary: {rec['summary'] or 'N/A'}")
        print("-" * 50)

def main():
    print_banner()

    parser = argparse.ArgumentParser(description="Automated AI Calling System (Android + PC)")
    parser.add_argument("-v", "--version", action="version", version=f"SIMVox AI v{__version__} (Automated Android + PC Telephony Agent)")
    parser.add_argument("--mode", choices=["usb", "wireless"], help="Switch ADB connection mode (usb or wireless).")
    parser.add_argument("--connect-wireless", type=str, metavar="IP:PORT", help="Connect to Wireless ADB (e.g. 192.168.1.50:5555).")
    parser.add_argument("--pair-wireless", nargs=3, metavar=("IP", "PORT", "CODE"), help="Pair Wireless ADB (e.g. 192.168.1.50 37123 123456).")
    parser.add_argument("--list-audio", action="store_true", help="List all sound input and output devices.")
    parser.add_argument("--check-adb", action="store_true", help="Check ADB connection and phone call state.")
    parser.add_argument("--monitor", action="store_true", help="Start automatic incoming call listener loop.")
    parser.add_argument("--outbound", type=str, help="Initiate an outbound call to specified phone number.")
    parser.add_argument("--history", action="store_true", help="View recent call transcripts and AI summaries.")
    parser.add_argument("--gui", action="store_true", help="Launch native pywebview Desktop UI Panel.")

    args = parser.parse_args()

    # Initialize modules
    db = DatabaseManager()
    phone = PhoneController()
    router = AudioRouter()
    ai = AIEngine()

    # Switch ADB mode if passed
    if args.mode:
        phone.set_mode(args.mode)

    if args.gui:
        from app import create_app
        create_app()
    elif args.connect_wireless:
        handle_connect_wireless(phone, args.connect_wireless)
    elif args.pair_wireless:
        handle_pair_wireless(phone, args.pair_wireless[0], args.pair_wireless[1], args.pair_wireless[2])
    elif args.list_audio:
        handle_list_audio()
    elif args.check_adb:
        handle_check_adb(phone)
    elif args.monitor:
        handle_monitor_mode(phone, db, router, ai)
    elif args.outbound:
        handle_outbound_call(phone, db, router, ai, args.outbound)
    elif args.history:
        handle_view_history(db)
    else:
        # Default behavior with no args: launch GUI
        from app import create_app
        create_app()

if __name__ == "__main__":
    main()


