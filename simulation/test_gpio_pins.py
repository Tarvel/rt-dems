#!/usr/bin/env python3
"""
test_gpio_pins.py — Interactive GPIO Pin Simulator & Tester
=============================================================

Simulates or TESTS relay-LED reactions for the 3 relay pins used by
rule_engine.py using gpiozero.

On a Raspberry Pi (including Pi 5):
    → Uses REAL GPIO pins via lgpio/RPi.GPIO (gpiozero auto-selects).
    → You can verify pin states with:  pinctrl get 17
    → Or with a multimeter on the physical header pins.

On a development machine (laptop/desktop):
    → Falls back to MockFactory (virtual pins, no hardware needed).

Usage:
    python simulation/test_gpio_pins.py

Interactive Commands:
    A / B / C       — Apply a relay mode (same logic as rule_engine.py)
    1 / 2 / 3       — Toggle an individual relay pin ON/OFF
    on  <pin>       — Force a specific pin ON   (e.g. 'on 17')
    off <pin>       — Force a specific pin OFF  (e.g. 'off 27')
    blink <pin>     — Blink a pin 3 times       (e.g. 'blink 22')
    status          — Print the current pin-state table
    verify          — (Pi only) Run pinctrl to show actual hardware states
    q / quit        — Clean up and exit
"""

import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# gpiozero — auto-detect real Pi or fall back to mock
# ---------------------------------------------------------------------------
from gpiozero import Device, LED

ON_PI = False

try:
    # On a real Pi, the default pin factory (lgpio on Pi 5, RPi.GPIO on Pi 4)
    # will succeed. We probe with a throwaway pin to confirm.
    _probe = LED(0, initial_value=False)
    _probe.close()
    ON_PI = True
except Exception:
    # Not on a Pi — use virtual pins.
    from gpiozero.pins.mock import MockFactory
    Device.pin_factory = MockFactory()
    ON_PI = False

# ---------------------------------------------------------------------------
# Pin definitions (match rule_engine.py defaults)
# ---------------------------------------------------------------------------
RELAY_PIN_1 = int(os.environ.get("RELAY_PIN_1", 17))   # Priority 1 – Critical
RELAY_PIN_2 = int(os.environ.get("RELAY_PIN_2", 27))   # Priority 2 – Medium
RELAY_PIN_3 = int(os.environ.get("RELAY_PIN_3", 22))   # Priority 3 – Luxury

PIN_LABELS = {
    RELAY_PIN_1: "P1 (Critical)",
    RELAY_PIN_2: "P2 (Medium)",
    RELAY_PIN_3: "P3 (Luxury)",
}

# Relay number → pin mapping
RELAY_NUM_TO_PIN = {
    1: RELAY_PIN_1,
    2: RELAY_PIN_2,
    3: RELAY_PIN_3,
}

# ---------------------------------------------------------------------------
# Virtual/Real LEDs
# ---------------------------------------------------------------------------
leds: dict[int, LED] = {
    RELAY_PIN_1: LED(RELAY_PIN_1, initial_value=False),
    RELAY_PIN_2: LED(RELAY_PIN_2, initial_value=False),
    RELAY_PIN_3: LED(RELAY_PIN_3, initial_value=False),
}

# ---------------------------------------------------------------------------
# Colour helpers (ANSI)
# ---------------------------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
DIM    = "\033[2m"


def _state_str(led: LED) -> str:
    """Return a coloured ON/OFF string for a LED."""
    if led.is_lit:
        return f"{GREEN}●  ON  (HIGH){RESET}"
    return f"{RED}○  OFF (LOW){RESET}"


def print_status(highlight_pin: int | None = None) -> None:
    """Print a formatted table of all pin states."""
    print()
    print(f"  {BOLD}{'Pin':>6}  {'Relay':>14}   {'State'}{RESET}")
    print(f"  {'─' * 42}")
    for pin, led in leds.items():
        marker = f"{CYAN}→{RESET} " if pin == highlight_pin else "  "
        print(f"{marker}{pin:>6}  {PIN_LABELS[pin]:>14}   {_state_str(led)}")
    print()


def verify_hardware() -> None:
    """On a real Pi, use pinctrl to show actual hardware pin states."""
    if not ON_PI:
        print(f"  {YELLOW}Not on a Pi — hardware verification skipped (using MockFactory).{RESET}")
        print(f"  {DIM}The virtual states above are accurate for logic testing.{RESET}\n")
        return

    print(f"\n  {BOLD}Hardware Pin Verification (pinctrl){RESET}")
    print(f"  {'─' * 42}")
    for pin in (RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3):
        try:
            result = subprocess.run(
                ["pinctrl", "get", str(pin)],
                capture_output=True, text=True, timeout=5,
            )
            hw_state = result.stdout.strip()
            print(f"  GPIO {pin:>2} ({PIN_LABELS[pin]:>14}): {hw_state}")
        except FileNotFoundError:
            print(f"  {RED}pinctrl not found. Install with: sudo apt install raspi-utils{RESET}")
            break
        except Exception as e:
            print(f"  {RED}Error reading pin {pin}: {e}{RESET}")
    print()


# ---------------------------------------------------------------------------
# Mode logic (mirrors rule_engine.apply_mode)
# ---------------------------------------------------------------------------
MODE_TABLE: dict[str, tuple[bool, bool, bool]] = {
    "A": (True, True, True),     # Peak Demand   — all ON
    "B": (True, True, False),    # Average Load  — P1+P2 ON, P3 OFF
    "C": (True, False, False),   # Baseline Load — P1 ON only
}


def apply_mode(mode: str) -> None:
    """Set all 3 relay pins according to the mode letter."""
    states = MODE_TABLE[mode]
    for (pin, led), state in zip(leds.items(), states):
        if state:
            led.on()
        else:
            led.off()

    names = {"A": "Peak Demand", "B": "Average Load", "C": "Baseline Load"}
    r1, r2, r3 = states
    print(f"\n  {BOLD}⚡ Mode {mode} — {names[mode]}{RESET}")
    print(f"     Relay 1 (Pin {RELAY_PIN_1}): {'ON' if r1 else 'OFF'}")
    print(f"     Relay 2 (Pin {RELAY_PIN_2}): {'ON' if r2 else 'OFF'}")
    print(f"     Relay 3 (Pin {RELAY_PIN_3}): {'ON' if r3 else 'OFF'}")
    print_status()


def toggle_pin(pin: int) -> None:
    """Toggle a single pin ON↔OFF."""
    led = leds[pin]
    led.toggle()
    action = "ON" if led.is_lit else "OFF"
    print(f"\n  Toggled pin {pin} ({PIN_LABELS[pin]}) → {action}")
    print_status(highlight_pin=pin)


def set_pin(pin: int, state: bool) -> None:
    """Force a pin to a specific state."""
    led = leds[pin]
    if state:
        led.on()
    else:
        led.off()
    action = "ON" if state else "OFF"
    print(f"\n  Set pin {pin} ({PIN_LABELS[pin]}) → {action}")
    print_status(highlight_pin=pin)


def blink_pin(pin: int, times: int = 3, interval: float = 0.4) -> None:
    """Blink a pin on/off several times."""
    led = leds[pin]
    original = led.is_lit
    print(f"\n  Blinking pin {pin} ({PIN_LABELS[pin]}) {times} times...")
    for i in range(times):
        led.on()
        print(f"    [{i + 1}/{times}] {GREEN}ON{RESET}", end="", flush=True)
        time.sleep(interval)
        led.off()
        print(f"  →  {RED}OFF{RESET}")
        time.sleep(interval)
    # Restore original state
    if original:
        led.on()
    print(f"  Done. Restored pin {pin} to {'ON' if original else 'OFF'}.")
    print_status(highlight_pin=pin)


def resolve_pin(raw: str) -> int | None:
    """Turn user input into a valid pin number, or None."""
    try:
        val = int(raw.strip())
    except ValueError:
        return None
    # Accept relay numbers (1, 2, 3) or BCM pin numbers (17, 27, 22)
    if val in RELAY_NUM_TO_PIN:
        return RELAY_NUM_TO_PIN[val]
    if val in leds:
        return val
    return None


# ---------------------------------------------------------------------------
# Help text
# ---------------------------------------------------------------------------
def get_help() -> str:
    hw_mode = f"{GREEN}REAL GPIO (Pi detected){RESET}" if ON_PI else f"{YELLOW}MOCK (virtual pins){RESET}"
    factory = str(Device.pin_factory)
    return f"""
  {BOLD}═══════════════════════════════════════════════════{RESET}
  {BOLD}       GPIO Pin Simulator — Interactive Menu{RESET}
  {BOLD}═══════════════════════════════════════════════════{RESET}
  Hardware: {hw_mode}
  Factory:  {DIM}{factory}{RESET}
  Pins:     {RELAY_PIN_1} (P1), {RELAY_PIN_2} (P2), {RELAY_PIN_3} (P3)

  {YELLOW}Mode Switching{RESET}
    {CYAN}A{RESET}               Apply Mode A  (all relays ON)
    {CYAN}B{RESET}               Apply Mode B  (P1 + P2 ON, P3 OFF)
    {CYAN}C{RESET}               Apply Mode C  (P1 ON only)

  {YELLOW}Manual Pin Control{RESET}  {DIM}(use relay # 1-3 or BCM pin #){RESET}
    {CYAN}1 / 2 / 3{RESET}       Toggle that relay pin ON↔OFF
    {CYAN}on  <pin>{RESET}       Force a pin ON   (e.g. {DIM}on 17{RESET})
    {CYAN}off <pin>{RESET}       Force a pin OFF  (e.g. {DIM}off 27{RESET})
    {CYAN}blink <pin>{RESET}     Blink a pin 3×   (e.g. {DIM}blink 3{RESET})

  {YELLOW}Verification{RESET}
    {CYAN}status{RESET}          Show current pin states
    {CYAN}verify{RESET}          Read actual hardware states (Pi only, uses pinctrl)

  {YELLOW}Other{RESET}
    {CYAN}help{RESET}            Show this menu
    {CYAN}q / quit{RESET}        Clean up and exit
"""


# ---------------------------------------------------------------------------
# Main interactive loop
# ---------------------------------------------------------------------------
def main() -> None:
    print(get_help())

    # Default to Mode C (safest) on startup, matching rule_engine behaviour
    print(f"  {DIM}Initialising all pins to Mode C (Baseline Load)...{RESET}")
    apply_mode("C")

    while True:
        try:
            raw = input(f"  {BOLD}>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = "q"

        if not raw:
            continue

        cmd = raw.lower()

        # ---- Mode switch ----
        if cmd in ("a", "b", "c"):
            apply_mode(cmd.upper())

        # ---- Relay toggle by number ----
        elif cmd in ("1", "2", "3"):
            pin = RELAY_NUM_TO_PIN[int(cmd)]
            toggle_pin(pin)

        # ---- on <pin> ----
        elif cmd.startswith("on "):
            parts = cmd.split()
            if len(parts) < 2:
                print(f"  {RED}Usage: on <pin>{RESET}")
            else:
                pin = resolve_pin(parts[1])
                if pin is None:
                    print(f"  {RED}Unknown pin. Use 1-3 or BCM pin number.{RESET}")
                else:
                    set_pin(pin, True)

        # ---- off <pin> ----
        elif cmd.startswith("off "):
            parts = cmd.split()
            if len(parts) < 2:
                print(f"  {RED}Usage: off <pin>{RESET}")
            else:
                pin = resolve_pin(parts[1])
                if pin is None:
                    print(f"  {RED}Unknown pin. Use 1-3 or BCM pin number.{RESET}")
                else:
                    set_pin(pin, False)

        # ---- blink <pin> ----
        elif cmd.startswith("blink"):
            parts = cmd.split()
            if len(parts) < 2:
                print(f"  {RED}Usage: blink <pin>{RESET}")
            else:
                pin = resolve_pin(parts[1])
                if pin is None:
                    print(f"  {RED}Unknown pin. Use 1-3 or BCM pin number.{RESET}")
                else:
                    blink_pin(pin)

        # ---- status ----
        elif cmd == "status":
            print_status()

        # ---- verify (hardware) ----
        elif cmd == "verify":
            verify_hardware()

        # ---- help ----
        elif cmd == "help":
            print(get_help())

        # ---- quit ----
        elif cmd in ("q", "quit", "exit"):
            print(f"\n  {DIM}Cleaning up pins...{RESET}")
            for led in leds.values():
                led.off()
                led.close()
            print(f"  {GREEN}All pins cleaned up. Goodbye!{RESET}\n")
            break

        else:
            print(f"  {RED}Unknown command: '{raw}'. Type 'help' for options.{RESET}")


if __name__ == "__main__":
    main()
