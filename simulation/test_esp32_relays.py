#!/usr/bin/env python3
"""
test_esp32_relays.py — Interactive MQTT Relay Pin Simulator
=============================================================

Publishes relay commands to room/relays/state over MQTT, exactly the
same payloads the rule engine produces.  Use this to manually test
that the ESP32 relay controller responds correctly — type a mode
and watch the ESP32 Serial Monitor change pins.

Also subscribes to room/relays/esp32_status to confirm the ESP32 is
online and receiving commands.

Usage:
    cd PROJECT_CODE
    source venv/bin/activate
    python simulation/test_esp32_relays.py

Commands (type at the > prompt):
    A / B / C       — Apply a full mode (same as rule engine)
    1 / 2 / 3       — Toggle an individual relay ON↔OFF
    on  <relay>     — Force a relay ON   (e.g.  on 2)
    off <relay>     — Force a relay OFF  (e.g.  off 3)
    all on          — Force all relays ON
    all off         — Force all relays OFF
    status          — Print current relay state table
    sweep           — Cycle through A → B → C → A with 2s pauses
    stress <N>      — Rapid-fire N random mode changes (load test)
    help            — Show this command list
    q / quit        — Publish Mode C (safe) and exit
"""

import json
import os
import random
import readline  # noqa: F401  — enables arrow-key history at the prompt
import sys
import time
import threading

from datetime import datetime, timezone

# ── MQTT ──
try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt is not installed.")
    print("  pip install paho-mqtt")
    sys.exit(1)

# ── .env support (optional) ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────────
# Configuration (reads from .env or env vars, with sane defaults)
# ─────────────────────────────────────────────────────────────────
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT   = int(os.environ.get("MQTT_PORT", 1883))

TOPIC_RELAY_STATE   = "room/relays/state"
TOPIC_ESP32_STATUS  = "room/relays/esp32_status"

# ─────────────────────────────────────────────────────────────────
# ANSI Colours
# ─────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ─────────────────────────────────────────────────────────────────
# Relay State Tracker
# ─────────────────────────────────────────────────────────────────
relay_states = {1: False, 2: False, 3: False}
current_mode = "C"
esp32_online = False


# ─────────────────────────────────────────────────────────────────
# Mode mapping (mirrors rule_engine.py apply_mode)
# ─────────────────────────────────────────────────────────────────
MODE_MAP = {
    "A": (True,  True,  True),     # Peak Demand — all ON
    "B": (True,  True,  False),    # Average Load — P1+P2
    "C": (True,  False, False),    # Baseline — P1 only
}

MODE_NAMES = {
    "A": "Peak Demand",
    "B": "Average Load",
    "C": "Baseline Load",
}


# ─────────────────────────────────────────────────────────────────
# MQTT Helpers
# ─────────────────────────────────────────────────────────────────
def build_payload(mode: str, reason: str = "") -> str:
    """Build a JSON payload identical to what rule_engine.py publishes."""
    r1, r2, r3 = relay_states[1], relay_states[2], relay_states[3]
    payload = {
        "mode": mode,
        "relay_1": r1,
        "relay_2": r2,
        "relay_3": r3,
        "reason": reason or f"Manual test — {MODE_NAMES.get(mode, mode)}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload)


def publish(client: mqtt.Client, mode: str, reason: str = "") -> None:
    """Publish the current relay state to MQTT."""
    payload = build_payload(mode, reason)
    result = client.publish(TOPIC_RELAY_STATE, payload, qos=1)
    if result.rc == mqtt.MQTT_ERR_SUCCESS:
        print(f"  {DIM}Published to {TOPIC_RELAY_STATE}{RESET}")
    else:
        print(f"  {RED}Publish failed (rc={result.rc}){RESET}")


# ─────────────────────────────────────────────────────────────────
# Display
# ─────────────────────────────────────────────────────────────────
def print_status():
    """Print a formatted table of current relay states."""
    mode_color = {"A": GREEN, "B": YELLOW, "C": RED}.get(current_mode, RESET)
    mode_label = MODE_NAMES.get(current_mode, "Unknown")

    print()
    print(f"  {BOLD}Current Mode: {mode_color}{current_mode} — {mode_label}{RESET}")
    print()
    print("  ┌─────────┬─────────────┬────────┐")
    print("  │  Relay  │  Priority   │ State  │")
    print("  ├─────────┼─────────────┼────────┤")
    for i, label in [(1, "Critical"), (2, "Comfort "), (3, "Luxury  ")]:
        state = relay_states[i]
        s = f"{GREEN} ON {RESET}" if state else f"{RED} OFF{RESET}"
        print(f"  │ Relay {i} │  {label}  │ {s} │")
    print("  └─────────┴─────────────┴────────┘")

    esp_status = f"{GREEN}● online{RESET}" if esp32_online else f"{RED}● offline{RESET}"
    print(f"  ESP32 status: {esp_status}")
    print()


def print_help():
    """Print the full command menu."""
    print(f"""
  {BOLD}{CYAN}━━ ESP32 Relay Pin Simulator — Commands ━━{RESET}

  {BOLD}Mode Commands{RESET} (same as rule engine):
    {GREEN}A{RESET}            Apply Mode A — All relays ON   (Peak Demand)
    {YELLOW}B{RESET}            Apply Mode B — P1+P2 ON, P3 OFF (Average Load)
    {RED}C{RESET}            Apply Mode C — P1 ON only       (Baseline)

  {BOLD}Individual Relay Control{RESET}:
    {CYAN}1{RESET} / {CYAN}2{RESET} / {CYAN}3{RESET}      Toggle that relay ON↔OFF
    on  <1|2|3>   Force a relay ON
    off <1|2|3>   Force a relay OFF
    all on        Force all relays ON
    all off       Force all relays OFF

  {BOLD}Testing{RESET}:
    sweep         Cycle A → B → C → A  (2s pauses)
    stress <N>    Rapid-fire N random mode changes
    status        Print current relay state table

  {BOLD}Other{RESET}:
    help          Show this menu
    q / quit      Publish Mode C (safe) and exit
""")


# ─────────────────────────────────────────────────────────────────
# MQTT Callbacks
# ─────────────────────────────────────────────────────────────────
def on_connect(client, userdata, flags, reason_code, properties=None):
    """Subscribe to ESP32 status topic on connect."""
    client.subscribe(TOPIC_ESP32_STATUS)


def on_message(client, userdata, msg):
    """Handle incoming messages (ESP32 status pings)."""
    global esp32_online
    if msg.topic == TOPIC_ESP32_STATUS:
        try:
            data = json.loads(msg.payload.decode())
            if data.get("status") == "online":
                esp32_online = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass


# ─────────────────────────────────────────────────────────────────
# Command Handlers
# ─────────────────────────────────────────────────────────────────
def cmd_apply_mode(client: mqtt.Client, mode: str) -> None:
    """Apply a full mode and publish."""
    global current_mode
    r1, r2, r3 = MODE_MAP[mode]
    relay_states[1], relay_states[2], relay_states[3] = r1, r2, r3
    current_mode = mode
    publish(client, mode)
    print_status()


def cmd_toggle(client: mqtt.Client, relay_num: int) -> None:
    """Toggle a single relay and publish."""
    global current_mode
    relay_states[relay_num] = not relay_states[relay_num]
    current_mode = "*"  # custom state, not a clean mode
    publish(client, current_mode, f"Manual toggle relay {relay_num}")
    print_status()


def cmd_force(client: mqtt.Client, relay_num: int, state: bool) -> None:
    """Force a single relay ON or OFF and publish."""
    global current_mode
    relay_states[relay_num] = state
    current_mode = "*"
    action = "ON" if state else "OFF"
    publish(client, current_mode, f"Manual force relay {relay_num} {action}")
    print_status()


def cmd_all(client: mqtt.Client, state: bool) -> None:
    """Force all relays ON or OFF and publish."""
    global current_mode
    for i in (1, 2, 3):
        relay_states[i] = state
    current_mode = "A" if state else "*"
    action = "ON" if state else "OFF"
    publish(client, current_mode, f"Manual force all relays {action}")
    print_status()


def cmd_sweep(client: mqtt.Client) -> None:
    """Cycle through all modes with pauses."""
    print(f"\n  {CYAN}Starting mode sweep: A → B → C → A{RESET}\n")
    for mode in ["A", "B", "C", "A"]:
        print(f"  {BOLD}→ Mode {mode}{RESET}")
        cmd_apply_mode(client, mode)
        if mode != "A" or mode == "A":
            time.sleep(2)
    print(f"  {GREEN}Sweep complete.{RESET}\n")


def cmd_stress(client: mqtt.Client, count: int) -> None:
    """Rapid-fire random mode changes."""
    print(f"\n  {CYAN}Stress test: {count} rapid mode changes{RESET}\n")
    modes = ["A", "B", "C"]
    for i in range(count):
        mode = random.choice(modes)
        r1, r2, r3 = MODE_MAP[mode]
        relay_states[1], relay_states[2], relay_states[3] = r1, r2, r3
        global current_mode
        current_mode = mode
        publish(client, mode, f"Stress test #{i+1}/{count}")
        time.sleep(0.15)  # 150ms between publishes
    print(f"\n  {GREEN}Stress test complete. Final state:{RESET}")
    print_status()


# ─────────────────────────────────────────────────────────────────
# Main REPL
# ─────────────────────────────────────────────────────────────────
def main():
    global current_mode

    # ── Banner ──
    print(f"""
{BOLD}{'=' * 60}
  ESP32 Relay Pin Simulator
  Smart Room Energy Management System
{'=' * 60}{RESET}
  Broker:  {MQTT_BROKER}:{MQTT_PORT}
  Topic:   {TOPIC_RELAY_STATE}
  Type {CYAN}help{RESET} for commands, {CYAN}q{RESET} to quit.
{'=' * 60}
""")

    # ── Connect MQTT ──
    client = mqtt.Client(
        client_id="relay-pin-simulator",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except OSError as exc:
        print(f"  {RED}Cannot connect to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}{RESET}")
        print(f"  {RED}{exc}{RESET}")
        print(f"\n  Make sure Mosquitto is running:")
        print(f"    mosquitto -c systemd/mosquitto.conf -v\n")
        sys.exit(1)

    client.loop_start()
    print(f"  {GREEN}Connected to MQTT broker.{RESET}")
    time.sleep(0.5)  # Give time for on_connect to fire

    # ── Start in Mode C (safe) ──
    relay_states[1], relay_states[2], relay_states[3] = True, False, False
    current_mode = "C"
    publish(client, "C", "Simulator startup — Mode C (safe)")
    print_status()

    # ── Interactive loop ──
    try:
        while True:
            try:
                raw = input(f"  {BOLD}>{RESET} ").strip()
            except EOFError:
                break

            if not raw:
                continue

            cmd = raw.upper()

            # ── Mode commands ──
            if cmd in ("A", "B", "C"):
                cmd_apply_mode(client, cmd)

            # ── Toggle relays ──
            elif cmd in ("1", "2", "3"):
                cmd_toggle(client, int(cmd))

            # ── Force ON/OFF ──
            elif cmd.startswith("ON "):
                try:
                    n = int(raw.split()[1])
                    if n in (1, 2, 3):
                        cmd_force(client, n, True)
                    else:
                        print(f"  {RED}Relay must be 1, 2, or 3{RESET}")
                except (IndexError, ValueError):
                    print(f"  {RED}Usage: on <1|2|3>{RESET}")

            elif cmd.startswith("OFF "):
                try:
                    n = int(raw.split()[1])
                    if n in (1, 2, 3):
                        cmd_force(client, n, False)
                    else:
                        print(f"  {RED}Relay must be 1, 2, or 3{RESET}")
                except (IndexError, ValueError):
                    print(f"  {RED}Usage: off <1|2|3>{RESET}")

            elif cmd == "ALL ON":
                cmd_all(client, True)
            elif cmd == "ALL OFF":
                cmd_all(client, False)

            # ── Testing ──
            elif cmd == "SWEEP":
                cmd_sweep(client)
            elif cmd.startswith("STRESS"):
                try:
                    n = int(raw.split()[1])
                    cmd_stress(client, max(1, min(n, 500)))
                except (IndexError, ValueError):
                    print(f"  {RED}Usage: stress <count>  (e.g. stress 20){RESET}")

            # ── Info ──
            elif cmd == "STATUS":
                print_status()
            elif cmd in ("HELP", "?"):
                print_help()
            elif cmd in ("Q", "QUIT", "EXIT"):
                break
            else:
                print(f"  {RED}Unknown command: {raw}{RESET}")
                print(f"  Type {CYAN}help{RESET} to see available commands.")

    except KeyboardInterrupt:
        print()

    # ── Shutdown: publish Mode C for safety ──
    print(f"\n  {YELLOW}Shutting down — publishing Mode C (safe) …{RESET}")
    relay_states[1], relay_states[2], relay_states[3] = True, False, False
    current_mode = "C"
    publish(client, "C", "Simulator shutdown — Mode C (safe)")
    time.sleep(0.5)
    client.loop_stop()
    client.disconnect()
    print(f"  {GREEN}Done.{RESET}\n")


if __name__ == "__main__":
    main()
