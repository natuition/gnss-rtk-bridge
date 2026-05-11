from __future__ import annotations

import logging
import queue
import threading
import time

from pygnssutils import GNSSNTRIPClient

from gnss_rtk_bridge.state import SharedGnssState


class _LiveCoordinatesProvider:
    def __init__(self, state: SharedGnssState):
        self._state = state
        self._logger = logging.getLogger("NtripCoordinates")
        self._last_missing_fix_log_monotonic = 0.0

    def get_coordinates(self) -> dict:
        snap = self._state.snapshot()
        if snap.latitude_deg is None or snap.longitude_deg is None:
            return {}

        return {
            "lat": snap.latitude_deg,
            "lon": snap.longitude_deg,
            "alt": snap.altitude_m or 0.0,
            "sep": 0.0,
            "sip": snap.satellites or 0,
            "fix": "3D" if snap.valid else "NO FIX",
            "hdop": 0.9,
            "diffage": 0,
            "diffstation": 0,
        }


class NtripClientThread(threading.Thread):
    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        username: str,
        password: str,
        state: SharedGnssState,
        gnss_write_rtcm,
        gga_interval_s: float = 5.0,
        max_failures_before_stop: int = 20,
        initial_backoff_s: float = 2.0,
        max_backoff_s: float = 60.0,
    ):
        super().__init__(daemon=True, name="NtripClientThread")
        self._logger = logging.getLogger(self.name)

        self._host = host
        self._port = port
        self._mountpoint = mountpoint
        self._username = username
        self._password = password
        self._state = state
        self._gnss_write_rtcm = gnss_write_rtcm

        self._gga_interval_s = gga_interval_s
        self._max_failures_before_stop = max_failures_before_stop
        self._initial_backoff_s = initial_backoff_s
        self._max_backoff_s = max_backoff_s

        self._stop_event = threading.Event()
        self._client_stop_event = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._rtcm_packets = 0
        self._rtcm_bytes = 0
        self._last_rtcm_log_monotonic = 0.0
        self._last_rtcm_wait_log_monotonic = 0.0

    def run(self) -> None:
        fail_count = 0
        backoff_s = self._initial_backoff_s

        # Wait until the GNSS receiver provides real coordinates before connecting.
        self._logger.info("NTRIP waiting for live GNSS coordinates before connecting")
        while not self._stop_event.is_set():
            snap = self._state.snapshot()
            if snap.latitude_deg is not None and snap.longitude_deg is not None:
                break
            self._stop_event.wait(1.0)
        if self._stop_event.is_set():
            return
        self._logger.info("GNSS coordinates available, connecting to NTRIP caster")

        while not self._stop_event.is_set():
            provider = _LiveCoordinatesProvider(self._state)
            client = GNSSNTRIPClient(app=provider)
            self._client_stop_event.clear()
            self._queue = queue.Queue()

            try:
                self._logger.info(
                    "Connecting to NTRIP caster %s:%d/%s",
                    self._host,
                    self._port,
                    self._mountpoint,
                )
                client.run(
                    server=self._host,
                    port=self._port,
                    https=1 if self._port == 443 else 0,
                    mountpoint=self._mountpoint,
                    datatype="RTCM",
                    ntripuser=self._username,
                    ntrippassword=self._password,
                    ggainterval=max(1, int(self._gga_interval_s)),
                    ggamode=0,
                    output=self._queue,
                    stopevent=self._client_stop_event,
                )
                self._logger.info(
                    "NTRIP client started; waiting for RTCM stream from %s:%d/%s",
                    self._host,
                    self._port,
                    self._mountpoint,
                )

                self._drain_rtcm_queue_until_disconnect(client)

                if self._stop_event.is_set():
                    client.stop()
                    break

                fail_count += 1
                if fail_count >= self._max_failures_before_stop:
                    self._logger.error(
                        "NTRIP stopped after %d consecutive failures",
                        fail_count,
                    )
                    self._stop_event.set()
                    client.stop()
                    break

                self._logger.warning(
                    "NTRIP disconnected; retrying in %.1fs (failure %d/%d)",
                    backoff_s,
                    fail_count,
                    self._max_failures_before_stop,
                )
                client.stop()
                self._stop_event.wait(backoff_s)
                backoff_s = min(backoff_s * 2.0, self._max_backoff_s)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                fail_count += 1
                self._logger.error("NTRIP client error: %s", exc)
                client.stop()
                if fail_count >= self._max_failures_before_stop:
                    self._logger.error(
                        "NTRIP stopped after %d consecutive failures",
                        fail_count,
                    )
                    self._stop_event.set()
                    break
                self._stop_event.wait(backoff_s)
                backoff_s = min(backoff_s * 2.0, self._max_backoff_s)
            else:
                fail_count = 0
                backoff_s = self._initial_backoff_s

    def stop(self) -> None:
        self._stop_event.set()
        self._client_stop_event.set()

    def _drain_rtcm_queue_until_disconnect(self, client: GNSSNTRIPClient) -> None:
        while not self._stop_event.is_set() and client.connected:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                now = time.monotonic()
                if now - self._last_rtcm_wait_log_monotonic >= 5.0:
                    self._logger.debug(
                        "NTRIP RTCM waiting for corrections from %s:%d/%s",
                        self._host,
                        self._port,
                        self._mountpoint,
                    )
                    self._last_rtcm_wait_log_monotonic = now
                continue

            raw_data: bytes | None
            if isinstance(item, tuple) and len(item) == 2:
                raw_data, _parsed = item
            else:
                raw_data = item

            if isinstance(raw_data, (bytes, bytearray)) and raw_data:
                payload = bytes(raw_data)
                self._rtcm_packets += 1
                self._rtcm_bytes += len(payload)
                self._state.mark_rtcm_received()
                self._gnss_write_rtcm(payload)
                now = time.monotonic()
                if now - self._last_rtcm_log_monotonic >= 1.0:
                    self._logger.debug(
                        "NTRIP RTCM -> GNSS wrote packet=%d bytes=%d total_packets=%d total_bytes=%d",
                        self._rtcm_packets,
                        len(payload),
                        self._rtcm_packets,
                        self._rtcm_bytes,
                    )
                    self._last_rtcm_log_monotonic = now

        self._logger.warning("NTRIP stream loop ended; connected=%s", client.connected)
