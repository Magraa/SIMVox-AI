import subprocess
import re
import time
from typing import Optional, Dict, Any, List
from config import ADB_PATH, ADB_DEVICE_SERIAL, ADB_MODE, ADB_WIRELESS_IP, ADB_WIRELESS_PORT, logger

class PhoneController:
    """Controls an Android device over USB or Wireless ADB (Android Debug Bridge) for call automation."""

    def __init__(
        self,
        adb_path: str = ADB_PATH,
        serial: str = ADB_DEVICE_SERIAL,
        mode: str = ADB_MODE,
        wireless_ip: str = ADB_WIRELESS_IP,
        wireless_port: str = ADB_WIRELESS_PORT
    ):
        self.adb_path = adb_path
        self.serial = serial
        self.mode = mode.upper() # "USB" or "WIRELESS"
        self.wireless_ip = wireless_ip
        self.wireless_port = str(wireless_port)

        if self.mode == "WIRELESS" and self.wireless_ip:
            self.connect_wireless()

    def set_mode(self, mode: str, ip: Optional[str] = None, port: Optional[str] = None) -> None:
        """Dynamically switches ADB connection mode between USB and WIRELESS."""
        self.mode = mode.upper()
        if ip:
            self.wireless_ip = ip
        if port:
            self.wireless_port = str(port)

        logger.info("ADB Mode set to: %s", self.mode)
        if self.mode == "WIRELESS":
            self.connect_wireless()

    def get_target_device_id(self) -> Optional[str]:
        """Returns target serial / address depending on active mode."""
        if self.mode == "WIRELESS" and self.wireless_ip:
            return f"{self.wireless_ip}:{self.wireless_port}"
        return self.serial if self.serial else None

    def _build_cmd(self, command: str) -> List[str]:
        cmd = [self.adb_path]
        target = self.get_target_device_id()
        
        # Don't add -s flag for global commands like connect / pair / devices
        first_word = command.split()[0] if command else ""
        if target and first_word not in ("connect", "disconnect", "pair", "devices"):
            cmd.extend(["-s", target])
            
        cmd.extend(command.split())
        return cmd

    def run_adb(self, command: str) -> str:
        """Executes an ADB shell command and returns output."""
        cmd = self._build_cmd(command)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=12, check=True)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error("ADB command failed: %s | Error: %s", command, e.stderr)
            return e.stdout.strip() if e.stdout else ""
        except Exception as e:
            logger.error("Error executing ADB command '%s': %s", command, str(e))
            return ""

    def connect_wireless(self, ip: Optional[str] = None, port: Optional[str] = None) -> bool:
        """Connects to an Android phone over Wi-Fi using ADB Wireless Debugging."""
        target_ip = ip or self.wireless_ip
        target_port = port or self.wireless_port

        if not target_ip:
            logger.error("Wireless ADB IP address is not specified in config or arguments.")
            return False

        target_address = f"{target_ip}:{target_port}"
        logger.info("Connecting to Wireless ADB target: %s...", target_address)
        output = self.run_adb(f"connect {target_address}")
        logger.info("ADB Connect Output: %s", output)

        if "connected to" in output.lower() or "already connected" in output.lower():
            self.mode = "WIRELESS"
            self.wireless_ip = target_ip
            self.wireless_port = target_port
            return True
        return False

    def pair_wireless(self, ip: str, pairing_port: str, pairing_code: str) -> bool:
        """Pairs with Android Wireless Debugging using a 6-digit pairing code (Android 11+)."""
        logger.info("Pairing Wireless ADB target %s:%s with code %s...", ip, pairing_port, pairing_code)
        output = self.run_adb(f"pair {ip}:{pairing_port} {pairing_code}")
        logger.info("ADB Pair Output: %s", output)
        return "successfully paired" in output.lower()

    def disconnect_wireless(self) -> bool:
        """Disconnects wireless ADB session."""
        target = self.get_target_device_id()
        if target:
            output = self.run_adb(f"disconnect {target}")
            logger.info("ADB Disconnect Output: %s", output)
            return True
        return False

    def restart_adb(self) -> bool:
        """Restarts the ADB server daemon to clear hung Windows USB driver connections."""
        logger.info("Restarting ADB Server...")
        self.run_adb("kill-server")
        time.sleep(1)
        res = self.run_adb("start-server")
        return "daemon started" in res.lower() or res == ""


    def select_device(self, serial: str) -> None:
        """Sets a specific target device serial."""
        self.serial = serial.strip()
        logger.info("Target ADB device serial set to: %s", self.serial)

    def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Parses `adb devices -l` and returns detailed list of all connected devices."""
        output = self.run_adb("devices -l")
        devices = []
        
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("List of") or line.startswith("*"):
                continue

            parts = line.split()
            if len(parts) >= 2:
                serial = parts[0]
                state = parts[1] # "device", "unauthorized", "offline"

                # Extract model from metadata if available
                model = "Unknown Device"
                for part in parts[2:]:
                    if part.startswith("model:"):
                        model = part.split(":", 1)[1].replace("_", " ")

                is_emulator = serial.startswith("emulator-") or "sdk_gphone" in model.lower() or "vbox" in serial.lower()

                devices.append({
                    "serial": serial,
                    "state": state,
                    "model": model,
                    "is_emulator": is_emulator,
                    "is_target": (serial == self.get_target_device_id())
                })

        return devices

    def is_connected(self) -> bool:
        """Checks if a valid, authorized Android phone is connected via ADB."""
        status = self.get_connection_status()
        return status == "CONNECTED"

    def get_connection_status(self) -> str:
        """
        Returns precise ADB status:
        - 'CONNECTED': Target or physical device is online & authorized.
        - 'UNAUTHORIZED': Device detected, but user must accept USB Debugging prompt on phone.
        - 'DISCONNECTED': No device connected or cable unplugged.
        """
        devices = self.get_connected_devices()
        if not devices:
            return "DISCONNECTED"

        target_id = self.get_target_device_id()

        # If a specific target ID is set, check it directly
        if target_id:
            for dev in devices:
                if dev["serial"] == target_id:
                    if dev["state"] == "device":
                        return "CONNECTED"
                    elif dev["state"] == "unauthorized":
                        return "UNAUTHORIZED"

        # If in USB mode and no target set (or target not found), look for physical phone devices
        if self.mode == "USB":
            physical_devices = [d for d in devices if not d["is_emulator"]]
            if physical_devices:
                for dev in physical_devices:
                    if dev["state"] == "device":
                        # Auto-lock target to first physical phone
                        if not self.serial:
                            self.serial = dev["serial"]
                        return "CONNECTED"
                    elif dev["state"] == "unauthorized":
                        return "UNAUTHORIZED"

            # If user explicitly selected an emulator serial as target, honor it
            if self.serial:
                for dev in devices:
                    if dev["serial"] == self.serial:
                        if dev["state"] == "device":
                            return "CONNECTED"
                        elif dev["state"] == "unauthorized":
                            return "UNAUTHORIZED"

        return "DISCONNECTED"


    def get_call_state(self) -> str:
        """
        Returns the current call state:
        'IDLE' (0), 'RINGING' (1), 'IN_CALL' (2), or 'DISCONNECTED'
        """
        if not self.is_connected():
            return "DISCONNECTED"

        output = self.run_adb("shell dumpsys telephony.registry")
        match = re.search(r"mCallState\s*=\s*(\d)", output)
        if match:
            state_code = match.group(1)
            states = {"0": "IDLE", "1": "RINGING", "2": "IN_CALL"}
            return states.get(state_code, "IDLE")
        
        # Fallback check via telecom
        output_telecom = self.run_adb("shell dumpsys telecom")
        if "State: RINGING" in output_telecom:
            return "RINGING"
        elif "State: ACTIVE" in output_telecom:
            return "IN_CALL"
        return "IDLE"


    def answer_call(self) -> bool:
        """Answers an incoming phone call."""
        logger.info("Answering incoming call...")
        res = self.run_adb("shell telecom accept-call")
        if not res:
            self.run_adb("shell input keyevent 5")
        
        time.sleep(1)
        state = self.get_call_state()
        return state == "IN_CALL"

    def end_call(self) -> bool:
        """Ends/hangs up the current active or ringing call."""
        logger.info("Ending call...")
        res = self.run_adb("shell telecom end-call")
        if not res:
            self.run_adb("shell input keyevent 6")
        time.sleep(1)
        return self.get_call_state() == "IDLE"

    def make_call(self, phone_number: str) -> bool:
        """Initiates an outbound phone call to the specified number."""
        clean_number = re.sub(r"[^\d+]", "", phone_number)
        logger.info("Dialing phone number: %s", clean_number)
        cmd = f"shell am start -a android.intent.action.CALL -d tel:{clean_number}"
        self.run_adb(cmd)
        
        for _ in range(10):
            time.sleep(1)
            state = self.get_call_state()
            if state == "IN_CALL":
                logger.info("Call established with %s", clean_number)
                return True
        return False

    def get_incoming_number(self) -> str:
        """Attempts to extract incoming caller phone number from telephony dump."""
        output = self.run_adb("shell dumpsys telecom")
        match = re.search(r"tel:([\d\+\-\(\)\s]+)", output)
        if match:
            return match.group(1).strip()
        return "Unknown Caller"
