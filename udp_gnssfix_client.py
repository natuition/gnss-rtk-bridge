from __future__ import annotations

import argparse
import sys
import time

try:
    from protos import gnss_fix_pb2
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Missing generated protobuf module. Generate protos/gnss_fix_pb2.py with protoc first."
    ) from exc

from gnss_rtk_bridge.udp_consumer import UdpGpsConsumer


def _optional_present(msg, field: str) -> bool:
    try:
        return msg.HasField(field)
    except ValueError:
        # proto3 scalar fallback when HasField is unavailable for this build
        return bool(getattr(msg, field, 0))


def _fmt_float(value: float, fmt: str) -> str:
    return format(value, fmt)


def _render(msg, addr: tuple[str, int], packets: int, last_rx: float) -> str:
    now_ns = time.monotonic_ns()
    bridge_age_s = (
        (now_ns - msg.timestamp_monotonic_ns) / 1e9
        if msg.timestamp_monotonic_ns
        else 0.0
    )
    lines = [
        "GNSS UDP Monitor (Ctrl+C to quit)",
        "",
        f"source={addr[0]}:{addr[1]} packets={packets}",
        f"last_packet_age_s={time.monotonic() - last_rx:.2f}",
        f"bridge_age_s={bridge_age_s:.3f}",
        "",
        f"valid={msg.valid}",
        f"lat_deg={_fmt_float(msg.latitude_deg, '.7f')}",
        f"lon_deg={_fmt_float(msg.longitude_deg, '.7f')}",
        f"alt_m={_fmt_float(msg.altitude_m, '.2f')}",
        f"speed_mps={_fmt_float(msg.speed_mps, '.3f')}",
        f"heading_deg={_fmt_float(msg.heading_deg, '.2f')}",
        f"fix_quality={msg.fix_quality}",
        f"satellites={msg.satellites}",
        f"timestamp_monotonic_ns={msg.timestamp_monotonic_ns}",
    ]

    if hasattr(msg, "last_rtcm_received_monotonic_ns") and _optional_present(
        msg, "last_rtcm_received_monotonic_ns"
    ):
        lines.append(
            f"last_rtcm_received_monotonic_ns={msg.last_rtcm_received_monotonic_ns}"
        )
    else:
        lines.append("last_rtcm_received_monotonic_ns=-")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read GnssFix protobuf over UDP")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=5010, help="Bind UDP port")
    args = parser.parse_args()

    consumer = UdpGpsConsumer(args.host, args.port)
    consumer.start()

    # Clear screen once, then repaint in place.
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()
    waiting_printed = False

    try:
        while True:
            sample = consumer.get_last()
            if sample.packets == 0:
                if not waiting_printed:
                    sys.stdout.write("\x1b[H\x1b[J")
                    sys.stdout.write("GNSS UDP Monitor (Ctrl+C to quit)\n\n")
                    sys.stdout.write("Waiting for first UDP packet...\n")
                    sys.stdout.flush()
                    waiting_printed = True
                time.sleep(0.1)
                continue

            waiting_printed = False
            view = _render(
                sample.msg,
                sample.addr,
                sample.packets,
                sample.last_rx_monotonic,
            )
            # Move cursor to top-left and clear to end of screen
            # so shorter new values don't keep trailing chars.
            sys.stdout.write("\x1b[H\x1b[J")
            sys.stdout.write(view)
            sys.stdout.write("\n")
            sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped.\n")
        sys.stdout.flush()
    finally:
        consumer.stop()
        consumer.join(timeout=1.0)


if __name__ == "__main__":
    main()
