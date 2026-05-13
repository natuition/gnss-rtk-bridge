from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass

try:
    from protos import gnss_fix_pb2
except ImportError:
    gnss_fix_pb2 = None


@dataclass(frozen=True)
class GpsFixSample:
    # valid: bool
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_m: float | None
    heading_deg: float | None = None
    fix_quality: int | None = None
    # packets: int
    # last_rx_monotonic: float


class UdpGpsConsumer(threading.Thread):
    """Receive GNSS fixes over UDP and expose the latest parsed position."""

    def __init__(self, host: str, port: int, timeout_s: float = 0.25):
        super().__init__(daemon=True, name="UdpGpsConsumer")
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        self._packets = 0
        self._last_rx_monotonic = time.monotonic()
        self._last_fix = GpsFixSample(
            # valid=False,
            latitude_deg=None,
            longitude_deg=None,
            altitude_m=None,
            heading_deg=None,
            fix_quality=None,
            # packets=0,
            # last_rx_monotonic=self._last_rx_monotonic,
        )

        self._sock: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self._host, self._port))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        sock.settimeout(self._timeout_s)
        self._sock = sock

        try:
            while not self._stop_event.is_set():
                try:
                    data, _ = sock.recvfrom(65535)
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    continue

                latest_data = data
                packets_inc = 1

                sock.settimeout(0.0)
                try:
                    while True:
                        data, _ = sock.recvfrom(65535)
                        latest_data = data
                        packets_inc += 1
                except (TimeoutError, BlockingIOError):
                    pass
                finally:
                    sock.settimeout(self._timeout_s)

                lat, lon, alt, head, qual = self._parse_payload(latest_data)
                now_monotonic = time.monotonic()

                with self._lock:
                    self._packets += packets_inc
                    self._last_rx_monotonic = now_monotonic
                    self._last_fix = GpsFixSample(
                        heading_deg=head,
                        fix_quality=qual,
                        latitude_deg=lat,
                        longitude_deg=lon,
                        altitude_m=alt,
                    )
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None

    def _parse_payload(
        self, payload: bytes
    ) -> tuple[float | None, float | None, float | None, float | None, int | None]:
        msg = gnss_fix_pb2.GnssFix()
        try:
            msg.ParseFromString(payload)
            valid = bool(getattr(msg, "valid", False))
            if not valid:
                return None, None, None
            return (
                float(getattr(msg, "latitude_deg", 0.0)),
                float(getattr(msg, "longitude_deg", 0.0)),
                float(getattr(msg, "altitude_m", 0.0)),
                float(getattr(msg, "heading_deg", 0.0)),
                int(getattr(msg, "fix_quality", 0)),
            )
        except Exception:
            print(
                f"[{self.__class__.__name__}] {time.strftime('%Y-%m-%d %H:%M:%S')} - Failed to parse GNSS fix payload: {payload!r}"
            )
            pass

    def get_last(self) -> GpsFixSample:
        with self._lock:
            return GpsFixSample(
                valid=self._last_fix.valid,
                latitude_deg=self._last_fix.latitude_deg,
                longitude_deg=self._last_fix.longitude_deg,
                altitude_m=self._last_fix.altitude_m,
                packets=self._last_fix.packets,
                last_rx_monotonic=self._last_fix.last_rx_monotonic,
            )

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
