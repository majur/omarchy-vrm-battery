#!/usr/bin/env python3
"""Read-only VRM MQTT bridge for the Omarchy VRM Battery widget.

This module deliberately uses only the Python standard library.  It owns no
HTTP listener, stores no token in files, and never emits MQTT write topics.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import os
import secrets
import select
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

API = "https://vrmapi.victronenergy.com/v2"
SERVICE = "omarchy-vrm-battery"
PROFILE = "default"
FRESH_SECONDS = 90
KEEPALIVE_SECONDS = 30


def xdg(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


CONFIG_DIR = xdg("XDG_CONFIG_HOME", str(Path.home() / ".config")) / SERVICE
STATE_DIR = xdg("XDG_STATE_HOME", str(Path.home() / ".local/state")) / SERVICE
RUNTIME_DIR = xdg("XDG_RUNTIME_DIR", f"/tmp/{SERVICE}-{os.getuid()}") / SERVICE
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = STATE_DIR / "status.json"
PID_FILE = RUNTIME_DIR / "bridge.pid"
LOCK_FILE = RUNTIME_DIR / "bridge.lock"


def atomic_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    with open(temp, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), mode)
        json.dump(data, handle, separators=(",", ":"), sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    os.chmod(path, mode)


def read_json(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def status_template(connection: str = "unconfigured", error: str = "") -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "connection": connection,
        "error": error,
        "installationName": "",
        "siteId": None,
        "dashboardUrl": "",
        "soc": {"value": None, "unit": "%", "validity": "missing", "confirmedAt": None},
        "solar": {"value": None, "unit": "W", "validity": "missing", "confirmedAt": None},
        "home": {"value": None, "unit": "W", "validity": "missing", "confirmedAt": None},
        "consumptionScope": "monitored-loads",
        "snapshotConfirmedAt": None,
        "updatedAt": time.time(),
    }


def write_status(state: dict[str, Any]) -> None:
    state["updatedAt"] = time.time()
    atomic_json(STATE_FILE, state)


def api_get(path: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        API + path,
        headers={"x-authorization": f"Token {token}", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise RuntimeError("VRM token nemá prístup alebo bol odvolaný.") from None
        if error.code == 429:
            raise RuntimeError("VRM API je dočasne limitované; skús to o chvíľu.") from None
        raise RuntimeError(f"VRM API odpovedalo HTTP {error.code}.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError("VRM API sa nepodarilo bezpečne načítať.") from error
    if not isinstance(parsed, dict) or parsed.get("success") is False:
        raise RuntimeError("VRM API nepotvrdilo požiadavku.")
    return parsed


def save_secret(token: str) -> None:
    try:
        completed = subprocess.run(
            ["secret-tool", "store", "--label=Omarchy VRM Battery", "service", SERVICE, "profile", PROFILE],
            input=token + "\n", text=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, check=False,
        )
    except FileNotFoundError:
        raise RuntimeError("Secret Service (secret-tool) nie je nainštalovaný.") from None
    if completed.returncode != 0:
        raise RuntimeError("Token sa nepodarilo uložiť do systémového keyringu. Odomkni keyring a skús znova.")


def lookup_secret() -> str:
    try:
        completed = subprocess.run(
            ["secret-tool", "lookup", "service", SERVICE, "profile", PROFILE],
            text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return ""
    return completed.stdout.rstrip("\n") if completed.returncode == 0 else ""


def clear_secret() -> None:
    subprocess.run(["secret-tool", "clear", "service", SERVICE, "profile", PROFILE],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def configure() -> int:
    print("VRM setup uses a separate personal access token. Your VRM password is never requested.")
    print("Create it in VRM: Preferences → Integrations → Access tokens.")
    email = input("VRM email for MQTT: ").strip()
    if not email or "@" not in email:
        print("A valid VRM email is required.", file=sys.stderr)
        return 2
    token = getpass.getpass("VRM access token (input is hidden): ").strip()
    if not token:
        print("No token supplied.", file=sys.stderr)
        return 2
    try:
        me = api_get("/users/me", token).get("user", {})
        user_id = int(me["id"])
        records = api_get(f"/users/{user_id}/installations?extended=1", token).get("records", [])
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    if not isinstance(records, list) or not records:
        print("Tento VRM účet nemá dostupnú žiadnu inštaláciu.", file=sys.stderr)
        return 1
    print("\nDostupné inštalácie:")
    for index, site in enumerate(records, 1):
        print(f"  {index}. {site.get('name', 'Bez názvu')} (ID {site.get('idSite', '?')})")
    answer = input("Vyber číslo [1]: ").strip() or "1"
    try:
        chosen = records[int(answer) - 1]
        site_id = int(chosen["idSite"])
        portal_id = str(chosen["identifier"]).strip()
        mqtt_host = str(chosen["mqtt_host"]).strip()
    except (ValueError, IndexError, KeyError, TypeError):
        print("Neplatný výber alebo VRM neposkytlo MQTT údaje.", file=sys.stderr)
        return 2
    if not portal_id or not mqtt_host:
        print("VRM neposkytlo portal ID alebo MQTT broker. Skontroluj prístup k inštalácii.", file=sys.stderr)
        return 1
    save_secret(token)
    config = {
        "schemaVersion": 1, "email": email, "userId": user_id, "siteId": site_id,
        "installationName": str(chosen.get("name") or "VRM"), "portalId": portal_id,
        "mqttHost": mqtt_host, "dashboardUrl": f"https://vrm.victronenergy.com/installation/{site_id}/dashboard",
    }
    atomic_json(CONFIG_FILE, config)
    state = status_template("connecting")
    state.update({key: config[key] for key in ("installationName", "siteId", "dashboardUrl")})
    write_status(state)
    print("Hotovo. Widget sa pripojí počas najbližšieho načítania lišty.")
    return 0


def utf8(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("!H", len(encoded)) + encoded


def remaining_length(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value % 128
        value //= 128
        if value:
            byte |= 0x80
        out.append(byte)
        if not value:
            return bytes(out)


def mqtt_packet(header: int, payload: bytes) -> bytes:
    return bytes([header]) + remaining_length(len(payload)) + payload


class MqttClient:
    def __init__(self, host_url: str, email: str, token: str) -> None:
        parsed = urllib.parse.urlparse(host_url if "://" in host_url else "ssl://" + host_url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 8883
        self.email, self.token = email, token
        self.socket: ssl.SSLSocket | None = None
        self.packet_id = 1
        self.last_ping = time.monotonic()

    def connect(self) -> None:
        if not self.host or not self.host.endswith(".victronenergy.com"):
            raise RuntimeError("VRM vrátil neočakávaný MQTT broker.")
        context = ssl.create_default_context()
        raw = socket.create_connection((self.host, self.port), timeout=15)
        self.socket = context.wrap_socket(raw, server_hostname=self.host)
        client_id = f"omarchy-vrm-{secrets.token_hex(6)}"
        flags = 0xC2  # username, password, clean session
        body = utf8("MQTT") + bytes([4, flags]) + struct.pack("!H", 45) + utf8(client_id)
        body += utf8(self.email) + utf8("Token " + self.token)
        self.send(0x10, body)
        kind, reply = self.recv(timeout=15)
        if kind != 2:
            raise RuntimeError("MQTT broker neodpovedal na prihlásenie.")
        # CONNACK: acknowledge flags, return code 0
        if len(reply) < 2 or reply[1] != 0:
            raise RuntimeError("VRM MQTT odmietol prístupový token.")

    def send(self, header: int, payload: bytes) -> None:
        if not self.socket:
            raise RuntimeError("MQTT spojenie nie je otvorené.")
        self.socket.sendall(mqtt_packet(header, payload))

    def recv(self, timeout: float = 1.0) -> tuple[int, bytes]:
        if not self.socket:
            raise RuntimeError("MQTT spojenie nie je otvorené.")
        self.socket.settimeout(timeout)
        try:
            first = self.socket.recv(1)
        except socket.timeout:
            return 0, b""
        if not first:
            raise RuntimeError("MQTT spojenie bolo ukončené.")
        multiplier, size = 1, 0
        while True:
            byte = self.socket.recv(1)
            if not byte:
                raise RuntimeError("MQTT spojenie bolo ukončené.")
            size += (byte[0] & 127) * multiplier
            if not byte[0] & 128:
                break
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise RuntimeError("Neplatná MQTT správa.")
        data = bytearray()
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise RuntimeError("MQTT spojenie bolo ukončené.")
            data.extend(chunk)
        return first[0] >> 4, bytes(data)

    def subscribe(self, topics: list[str]) -> None:
        packet_id = self.next_id()
        body = struct.pack("!H", packet_id) + b"".join(utf8(topic) + b"\x00" for topic in topics)
        self.send(0x82, body)

    def publish(self, topic: str, value: bytes = b"") -> None:
        self.send(0x30, utf8(topic) + value)

    def ping(self) -> None:
        self.send(0xC0, b"")
        self.last_ping = time.monotonic()

    def next_id(self) -> int:
        self.packet_id = self.packet_id % 65535 + 1
        return self.packet_id

    def close(self) -> None:
        if self.socket:
            with contextlib.suppress(OSError):
                self.send(0xE0, b"")
                self.socket.close()
        self.socket = None


@dataclass
class Measurements:
    state: dict[str, Any]
    phases: int | None = None
    phase_values: dict[int, float | None] = field(default_factory=lambda: {1: None, 2: None, 3: None})

    def set_metric(self, key: str, value: Any) -> bool:
        metric = self.state[key]
        try:
            numeric = float(value)
            if not (numeric == numeric and abs(numeric) != float("inf")):
                raise ValueError
            if key == "soc" and not 0 <= numeric <= 100:
                raise ValueError
        except (TypeError, ValueError):
            metric.update({"value": None, "validity": "missing", "confirmedAt": None})
            return True
        metric.update({"value": numeric, "validity": "fresh", "confirmedAt": time.time()})
        return True

    def set_phase_count(self, value: Any) -> bool:
        try:
            phases = int(float(value))
            if phases not in (1, 2, 3):
                raise ValueError
        except (TypeError, ValueError):
            self.phases = None
            return self.update_home()
        self.phases = phases
        return self.update_home()

    def set_phase(self, number: int, value: Any) -> bool:
        try:
            self.phase_values[number] = float(value) if value is not None else None
        except (TypeError, ValueError):
            self.phase_values[number] = None
        return self.update_home()

    def update_home(self) -> bool:
        home = self.state["home"]
        if not self.phases or any(self.phase_values[i] is None for i in range(1, self.phases + 1)):
            home.update({"value": None, "validity": "missing", "confirmedAt": None})
            return True
        home.update({"value": sum(self.phase_values[i] or 0 for i in range(1, self.phases + 1)),
                     "validity": "fresh", "confirmedAt": time.time()})
        return True

    def expire(self) -> bool:
        changed = False
        now = time.time()
        for key in ("soc", "solar", "home"):
            metric = self.state[key]
            at = metric.get("confirmedAt")
            if metric.get("value") is not None and at and now - at > FRESH_SECONDS and metric.get("validity") != "stale":
                metric["validity"] = "stale"
                changed = True
        return changed


def payload_value(data: bytes) -> Any:
    if not data:
        return None
    try:
        decoded = json.loads(data.decode("utf-8"))
        return decoded.get("value") if isinstance(decoded, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def parse_publish(payload: bytes) -> tuple[str, bytes] | None:
    if len(payload) < 2:
        return None
    size = struct.unpack("!H", payload[:2])[0]
    if len(payload) < 2 + size:
        return None
    try:
        return payload[2:2 + size].decode("utf-8"), payload[2 + size:]
    except UnicodeDecodeError:
        return None


def configure_state(config: dict[str, Any], connection: str, error: str = "") -> dict[str, Any]:
    state = status_template(connection, error)
    for key in ("installationName", "siteId", "dashboardUrl"):
        state[key] = config.get(key, state[key])
    return state


def run_bridge() -> int:
    config = read_json(CONFIG_FILE)
    required = ("email", "siteId", "portalId", "mqttHost", "dashboardUrl")
    if not all(config.get(key) for key in required):
        write_status(status_template("unconfigured", "VRM účet ešte nie je pripojený."))
        return 0
    token = lookup_secret()
    if not token:
        write_status(configure_state(config, "auth-required", "VRM token nie je dostupný v systémovom keyringu."))
        return 0
    state = configure_state(config, "connecting")
    measurements = Measurements(state)
    portal = str(config["portalId"])
    base = f"N/{portal}/system/0"
    topics = [
        f"{base}/Dc/Battery/Soc", f"{base}/Dc/Pv/Power",
        f"{base}/Ac/Consumption/NumberOfPhases",
        f"{base}/Ac/Consumption/L1/Power", f"{base}/Ac/Consumption/L2/Power", f"{base}/Ac/Consumption/L3/Power",
        f"N/{portal}/full_publish_completed",
    ]
    next_retry = 1.0
    while True:
        client: MqttClient | None = None
        try:
            write_status(state)
            client = MqttClient(str(config["mqttHost"]), str(config["email"]), token)
            client.connect()
            state["connection"] = "live"
            state["error"] = ""
            client.subscribe(topics)
            client.publish(f"R/{portal}/keepalive")
            last_keepalive = time.monotonic()
            next_retry = 1.0
            while True:
                kind, packet = client.recv(1.0)
                changed = False
                if kind == 3:
                    message = parse_publish(packet)
                    if message:
                        topic, payload = message
                        value = payload_value(payload)
                        if topic == f"{base}/Dc/Battery/Soc": changed = measurements.set_metric("soc", value)
                        elif topic == f"{base}/Dc/Pv/Power": changed = measurements.set_metric("solar", value)
                        elif topic == f"{base}/Ac/Consumption/NumberOfPhases": changed = measurements.set_phase_count(value)
                        elif topic.startswith(f"{base}/Ac/Consumption/L") and topic.endswith("/Power"):
                            phase = int(topic.rsplit("/L", 1)[1].split("/", 1)[0])
                            changed = measurements.set_phase(phase, value)
                        elif topic == f"N/{portal}/full_publish_completed":
                            state["snapshotConfirmedAt"] = time.time()
                            changed = True
                now = time.monotonic()
                if now - last_keepalive >= KEEPALIVE_SECONDS:
                    # Full republish validates unchanged values as well. We only publish
                    # the documented read/keepalive topic; no W/ topic is ever constructed.
                    client.publish(f"R/{portal}/keepalive")
                    last_keepalive = now
                if now - client.last_ping >= 20:
                    client.ping()
                if measurements.expire(): changed = True
                if changed:
                    write_status(state)
        except KeyboardInterrupt:
            return 0
        except RuntimeError as error:
            message = str(error)
            state["connection"] = "auth-required" if "token" in message.lower() or "prístup" in message.lower() else "offline"
            state["error"] = message
            if measurements.expire():
                pass
            write_status(state)
            time.sleep(next_retry + secrets.randbelow(300) / 1000)
            next_retry = min(next_retry * 2, 60)
        except (OSError, ssl.SSLError) as error:
            state["connection"] = "offline"
            state["error"] = "MQTT spojenie sa prerušilo."
            write_status(state)
            time.sleep(next_retry + secrets.randbelow(300) / 1000)
            next_retry = min(next_retry * 2, 60)
        finally:
            if client:
                client.close()


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_bridge() -> int:
    RUNTIME_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(RUNTIME_DIR, 0o700)
    with open(LOCK_FILE, "a+", encoding="utf-8") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            pid = int(PID_FILE.read_text().strip())
        except (FileNotFoundError, ValueError):
            pid = 0
        if pid and process_alive(pid):
            return 0
        with open(os.devnull, "wb") as null:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "run"],
                                       stdin=subprocess.DEVNULL, stdout=null, stderr=null,
                                       start_new_session=True, close_fds=True)
        PID_FILE.write_text(str(process.pid) + "\n", encoding="ascii")
        os.chmod(PID_FILE, 0o600)
    return 0


def disconnect() -> int:
    try:
        pid = int(PID_FILE.read_text().strip())
        if process_alive(pid):
            os.kill(pid, 15)
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass
    clear_secret()
    with contextlib.suppress(FileNotFoundError): CONFIG_FILE.unlink()
    with contextlib.suppress(FileNotFoundError): PID_FILE.unlink()
    write_status(status_template("unconfigured", "VRM účet bol odpojený."))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Omarchy VRM Battery bridge")
    parser.add_argument("command", choices=("configure", "ensure", "run", "disconnect", "status"))
    args = parser.parse_args()
    if args.command == "configure": return configure()
    if args.command == "ensure": return ensure_bridge()
    if args.command == "run": return run_bridge()
    if args.command == "disconnect": return disconnect()
    print(json.dumps(read_json(STATE_FILE) or status_template()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
