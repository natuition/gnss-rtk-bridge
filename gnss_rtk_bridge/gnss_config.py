import serial
import time

PORT = "/dev/ttyUSB0"
BAUDRATE = 460800
DELAY_S = 2.0

COMMANDS = [
    "GPHDT COM3 1",
    "GNHDT COM3 1",
    "SAVECONFIG",
]


def main() -> None:
    s = serial.Serial(PORT, BAUDRATE, timeout=1)
    print(f"Connected to {PORT} @ {BAUDRATE}")

    for cmd in COMMANDS:
        payload = (cmd + "\r\n").encode()
        s.write(payload)
        print(f">> {cmd}")
        time.sleep(DELAY_S)
        response = s.read(s.in_waiting or 1)
        if response:
            print(f"<< {response.decode(errors='replace').strip()}")

    s.close()
    print("Done.")


if __name__ == "__main__":
    main()
