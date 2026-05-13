from __future__ import annotations

import socket

from gnss_rtk_bridge.state import GnssState

try:
    from .protos import GnssFix
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "Missing generated protobuf module. Run protoc before starting the service. "
        "See README for the exact command."
    ) from exc


class UdpProtobufPublisher:
    def __init__(self, host: str, port: int):
        self._target = (host, port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def publish(self, state: GnssState) -> None:
        msg = GnssFix()
        msg.timestamp_monotonic_ns = int(state.timestamp_monotonic_ns)
        msg.valid = bool(state.valid)

        if state.latitude_deg is not None:
            msg.latitude_deg = float(state.latitude_deg)
        if state.longitude_deg is not None:
            msg.longitude_deg = float(state.longitude_deg)
        if state.altitude_m is not None:
            msg.altitude_m = float(state.altitude_m)
        if state.speed_mps is not None:
            msg.speed_mps = float(state.speed_mps)
        if state.heading_deg is not None:
            msg.heading_deg = float(state.heading_deg)
        if state.fix_quality is not None:
            msg.fix_quality = int(state.fix_quality)
        if state.satellites is not None:
            msg.satellites = int(state.satellites)
        if (
            state.last_rtcm_received_monotonic_ns is not None
            and hasattr(msg, "last_rtcm_received_monotonic_ns")
        ):
            msg.last_rtcm_received_monotonic_ns = int(
                state.last_rtcm_received_monotonic_ns
            )

        try:
            self._sock.sendto(msg.SerializeToString(), self._target)
        except OSError:
            # UDP telemetry is best effort by design.
            return

    def close(self) -> None:
        self._sock.close()
