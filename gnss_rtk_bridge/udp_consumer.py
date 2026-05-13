from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass

try:
    from gnss_rtk_bridge.protos import GnssFix
except ImportError as exc:  # pragma: no cover
    print(exc)
    raise RuntimeError(
        "Missing generated protobuf module. Generate protos/gnss_fix_pb2.py with protoc first."
    ) from exc


@dataclass(frozen=True)
class GnssFixSample:
    msg: GnssFix
    addr: tuple[str, int]
    packets: int
    last_rx_monotonic: float


class UdpGnssFixConsumer(threading.Thread):
    def __init__(self, host: str, port: int, timeout_s: float = 0.25):
        super().__init__(daemon=True, name="UdpGnssFixConsumer")
        self._host = host
        self._port = port
        self._timeout_s = timeout_s

        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        self._packets = 0
        self._last_rx_monotonic = time.monotonic()
        self._last_addr = (host, port)
        self._last_msg = GnssFix()

        self._sock: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self._host, self._port))
        # Keep the receive buffer small and always keep freshest frame.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        sock.settimeout(self._timeout_s)
        self._sock = sock

        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    continue

                latest_data = data
                latest_addr = addr
                packets_inc = 1

                # Drain queued datagrams and keep only the newest one.
                sock.settimeout(0.0)
                try:
                    while True:
                        data, addr = sock.recvfrom(65535)
                        latest_data = data
                        latest_addr = addr
                        packets_inc += 1
                except (BlockingIOError, socket.timeout):
                    pass
                finally:
                    sock.settimeout(self._timeout_s)

                msg = GnssFix()
                try:
                    msg.ParseFromString(latest_data)
                except Exception:
                    continue

                with self._lock:
                    self._packets += packets_inc
                    self._last_rx_monotonic = time.monotonic()
                    self._last_addr = latest_addr
                    self._last_msg = msg
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None

    def get_last(self) -> GnssFixSample:
        with self._lock:
            msg = GnssFix()
            msg.CopyFrom(self._last_msg)
            return GnssFixSample(
                msg=msg,
                addr=self._last_addr,
                packets=self._packets,
                last_rx_monotonic=self._last_rx_monotonic,
            )

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
