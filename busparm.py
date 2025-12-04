#!/usr/bin/env python3
"""
Bus Pirate 6 I2C sniffer helper.

- Connects to the **terminal** serial port (NOT the BPIO2 port).
- Handles the VT100 prompt if needed.
- Switches to I2C mode using `m i2c`.
- Accepts default speed / clock stretch options.
- Runs `sniff` and streams output to the terminal (and optional log file).

Usage examples:
  python bp6_i2c_sniffer.py -p /dev/ttyACM0
  python bp6_i2c_sniffer.py -p COM7 -o i2c_log.txt
  python bp6_i2c_sniffer.py -p /dev/ttyACM0 -s settings.json
"""

import argparse
import json
import sys
import time
from pathlib import Path
from parmesean.parmesean import *
import serial  # pyserial

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEVICES_DIR = os.path.join(CURRENT_DIR, "devices")
SETTINGS_FILE = os.path.join(CURRENT_DIR, "settings.json")
OUT_DIR = os.path.join(CURRENT_DIR, "output")

def load_settings(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Settings file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def open_serial(port: str, baud: int, timeout: float = 0.1) -> serial.Serial:
    return serial.Serial(port=port, baudrate=baud, timeout=timeout)


def read_all(ser: serial.Serial, delay: float = 0.1) -> str:
    """Read everything currently available, return as decoded string."""
    time.sleep(delay)
    data = ser.read(ser.in_waiting or 1)

    if not data:
        return ""
    try:
        return data.decode(errors="replace")
    except Exception:
        return ""


def wait_for(ser: serial.Serial, substring: str, timeout: float = 5.0) -> str:
    """Wait until substring appears in the incoming text or timeout."""
    end = time.monotonic() + timeout
    buffer = ""
    while time.monotonic() < end:
        chunk = read_all(ser, delay=0.05)
        if chunk:
            buffer += chunk
            if substring in buffer:
                return buffer
    return buffer  # may not contain substring


def maybe_handle_vt100_prompt(ser: serial.Serial):
    """
    If we see the VT100 prompt, answer with 'n' (ASCII mode) by default
    so the output is simple to parse.
    """
    # Send a couple newlines to wake it up
    ser.write(b"x\r\r\r")
    ser.flush()
    text = wait_for(ser, "VT100 compatible color mode", timeout=1.0)
    if "VT100 compatible color mode" in text:
        # Choose ASCII: 'n' + enter
        ser.write(b"n\r")
        ser.flush()
        # Read until we see a prompt like 'HiZ>' or 'I2C>'
        wait_for(ser, "HiZ>", timeout=1.0)


def ensure_i2c_mode(ser: serial.Serial):
    """
    From any mode, switch to I2C using 'm i2c' and accept default options.

    settings (optional, from JSON) can later be used to customize speed or
    clock stretching if you want. For now we just hit ENTER to use defaults.
    """
    # Wake the prompt
    ser.write(b"\n")
    ser.flush()
    buf = wait_for(ser, ">", timeout=1.0)

    # If we are already in I2C>, just return
    if "I2C>" in buf:
        return

    # From HiZ> or other modes, send 'm i2c'
    ser.write(b"m i2c \r")
    ser.flush()
    chunk = read_all(ser, delay=0.05)
    ser.write(b"n\r")
    ser.flush()
    chunk = read_all(ser, delay=0.05)
    ser.write(b"400\r")
    ser.flush()
    chunk = read_all(ser, delay=0.05)
    ser.write(b"1\r")
    ser.flush()
    chunk = read_all(ser, delay=0.05)
    # ser.write(b"n\r")
    # ser.flush()
    # Two possible flows:
    #  - initial config: asks for speed, then clock stretching
    #  - "Use previous settings?" prompt
    #
    # Easiest robust approach: whenever we see a '>' prompt and we are
    # not yet at 'I2C>', send '\n' to accept the default, until we do see 'I2C>'.
    #
    # We'll loop for a few seconds and bail if we never reach I2C>.
    end = time.monotonic() + 30.0
    buffer = ""
    ser.write(b"\r")
    ser.flush()
    while time.monotonic() < end:
        chunk = read_all(ser, delay=0.05)
        if chunk:
            buffer += chunk
            # Debug: you could print(buffer) here if you need to see the menus
            if "I2C>" in buffer:
                return
            # Look for a generic '>' prompt that expects an answer
            if buffer.strip().endswith(">"):
                # For now always accept defaults
                ser.write(b"\n")
                ser.flush()

    # If we got here, we never saw I2C>. Not fatal, but warn.
    sys.stderr.write("Warning: did not detect 'I2C>' prompt; continuing anyway.\n")


def start_sniffer(ser: serial.Serial):
    """
    Run the I2C sniffer command.

    In I2C mode, the command is literally:
        I2C> sniff
    """
    ser.write(b"sniff\n")
    ser.flush()


def stream_output(parm: Parmesean, ser: serial.Serial, log_file: Path | None = None):
    """
    Stream Bus Pirate output to stdout and optionally to a log file.

    Ctrl-C stops the script; the Bus Pirate will stop sniffing when it
    sees a keypress or when the port is closed (depending on firmware).
    """
    log_fp = None
    try:
        if log_file:
            log_fp = log_file.open("a", encoding="utf-8")

        print("=== I2C sniffer running. Press Ctrl-C to stop. ===\n")
        read_all(ser, delay=0.5)
        while True:
            data = ser.read(ser.in_waiting or 1)
            if not data:
                continue

            try:
                text = data.decode(errors="replace")
            except Exception:
                text = ""

            if text:
                # Print to terminal
                print(text)
                result = parm.parse(text)
                if result is not None:
                    for r in result:
                        parm.printc_result(r)
                # And log to file
                if log_fp:
                    log_fp.write(text)
                    log_fp.flush()

    except KeyboardInterrupt:
        sys.stdout.write("\nStopping sniffer and closing port...\n")
        sys.stdout.flush()
    finally:
        if log_fp:
            log_fp.close()


def main():
    parser = argparse.ArgumentParser(
        description="Bus Pirate 6 I2C sniffer helper (text UI, works with BP6)."
    )
    parser.add_argument(
        "-p",
        "--port",
        required=True,
        help="Serial port of the Bus Pirate terminal (e.g. COM7, /dev/ttyACM0)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Optional log file to write sniffer output",
    )
    parser.add_argument(
        "-s",
        "--settings",
        type=str,
        help="Optional JSON settings file (currently reserved for future use)",
    )

    args = parser.parse_args()

    settings_file = SETTINGS_FILE
    if args.settings:
        settings_file = Path(args.settings)
    
    try:
        settings = load_settings(settings_file)
    except Exception as e:
        sys.stderr.write(f"Failed to load settings file: {e}\n")

    log_path = Path(args.output) if args.output else None

    try:
        ser = open_serial(args.port, 115200)
    except Exception as e:
        sys.stderr.write(f"Failed to open serial port {args.port}: {e}\n")
        sys.exit(1)

    try:
        parm = Parmesean(settings_file=settings_file )
    
    except Exception as e:
        sys.stderr.write(f"Failed to grate the parmesean {e}")
    try:
        # Handle potential VT100 prompt and get to a 'HiZ>' or similar prompt
        maybe_handle_vt100_prompt(ser)

        # Switch into I2C mode
        ensure_i2c_mode(ser)

        # Start sniffer
        start_sniffer(ser)

        # Stream everything coming back
        stream_output(parm, ser, log_file=log_path)

    finally:
        ser.close()


if __name__ == "__main__":
    main()
