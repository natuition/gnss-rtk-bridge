from __future__ import annotations

import logging
import threading
import time

import serial
from pynmeagps import NMEAReader

from gnss_rtk_bridge.state import SharedGnssState
from gnss_rtk_bridge.udp_publisher import UdpProtobufPublisher


class GnssThread(threading.Thread):
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        state: SharedGnssState,
        publisher: UdpProtobufPublisher,
        serial_lock: threading.Lock,
        timeout_s: float = 0.1,
    ):
        super().__init__(daemon=True, name="GnssThread")
        self._logger = logging.getLogger(self.name)
        self._serial_port_name = serial_port
        self._baudrate = baudrate
        self._timeout_s = timeout_s
        self._state = state
        self._publisher = publisher
        self.serial_lock = serial_lock

        self._stop_event = threading.Event()
        self.serial: serial.Serial | None = None
        self._last_gga_rx_monotonic: float | None = None

    def run(self) -> None:
        backoff_s = 1.0

        while not self._stop_event.is_set():
            if self.serial is None or not self.serial.is_open:
                if not self._open_serial():
                    self._stop_event.wait(backoff_s)
                    backoff_s = min(backoff_s * 2.0, 10.0)
                    continue
                backoff_s = 1.0

            try:
                reader = NMEAReader(self.serial)
                self._read_loop(reader)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("NMEA reader reset after error: %s", exc)
                self._close_serial()
                self._stop_event.wait(backoff_s)
                backoff_s = min(backoff_s * 2.0, 10.0)

        self._close_serial()

    def stop(self) -> None:
        self._stop_event.set()

    def write_rtcm(self, data: bytes) -> None:
        if not data:
            return
        with self.serial_lock:
            if self.serial is None or not self.serial.is_open:
                self._logger.debug("Dropping RTCM frame because serial is unavailable")
                return
            try:
                self.serial.write(data)
                self.serial.flush()
            except serial.SerialException as exc:
                self._logger.warning("Failed to write RTCM to serial: %s", exc)
                self._close_serial()

    def _read_loop(self, reader: NMEAReader) -> None:
        while not self._stop_event.is_set():
            try:
                raw_data, parsed = reader.read()
            except serial.SerialException as exc:
                self._logger.warning("Serial read error: %s", exc)
                self._close_serial()
                return
            except TimeoutError:
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.debug("Skipping malformed NMEA frame: %s", exc)
                continue

            if parsed is None:
                continue

            talker = str(getattr(parsed, "talker", ""))
            msg_id = str(getattr(parsed, "msgID", ""))
            if talker not in {"GP", "GN"}:
                continue

            if msg_id == "GGA":
                rx_now = time.monotonic()
                rx_now_ns = time.monotonic_ns()
                gga_dt_s = (
                    None
                    if self._last_gga_rx_monotonic is None
                    else rx_now - self._last_gga_rx_monotonic
                )
                self._last_gga_rx_monotonic = rx_now
                in_waiting = self.serial.in_waiting if self.serial is not None else -1
                self._state.update_from_gga(parsed, raw_data or b"")
                snap = self._state.snapshot()
                publish_start = time.monotonic()
                self._publisher.publish(snap)
                publish_ms = (time.monotonic() - publish_start) * 1000.0
                self._logger.debug(
                    "GGA rx_mono_ns=%d gga_dt_s=%s in_waiting=%d publish_ms=%.2f lat=%.7f lon=%.7f",
                    rx_now_ns,
                    "-" if gga_dt_s is None else f"{gga_dt_s:.3f}",
                    in_waiting,
                    publish_ms,
                    snap.latitude_deg if snap.latitude_deg is not None else 0.0,
                    snap.longitude_deg if snap.longitude_deg is not None else 0.0,
                )
            elif msg_id == "RMC":
                self._state.update_from_rmc(parsed)
                self._publisher.publish(self._state.snapshot())
            elif msg_id == "HDT":
                self._state.update_from_hdt(parsed)
                self._publisher.publish(self._state.snapshot())

    def _open_serial(self) -> bool:
        try:
            self.serial = serial.Serial(
                self._serial_port_name,
                self._baudrate,
                timeout=self._timeout_s,
            )
            # Discard stale buffered NMEA data so the first frame read is fresh.
            self.serial.reset_input_buffer()
            self._logger.info(
                "Connected to GNSS serial port %s @ %d baud",
                self._serial_port_name,
                self._baudrate,
            )
            return True
        except (serial.SerialException, OSError) as exc:
            self._logger.warning("Unable to open serial port %s: %s", self._serial_port_name, exc)
            return False

    def _close_serial(self) -> None:
        with self.serial_lock:
            if self.serial is None:
                return
            try:
                if self.serial.is_open:
                    self.serial.close()
            except serial.SerialException:
                pass
            finally:
                self.serial = None
