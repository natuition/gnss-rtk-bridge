from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any

KNOTS_TO_MPS = 0.514444


@dataclass(frozen=True)
class GnssState:
    timestamp_monotonic_ns: int

    valid: bool

    latitude_deg: float | None
    longitude_deg: float | None
    altitude_m: float | None

    speed_mps: float | None
    heading_deg: float | None

    fix_quality: int | None
    satellites: int | None
    last_rtcm_received_monotonic_ns: int | None

    last_gga_sentence: bytes | None


class SharedGnssState:
    def __init__(self):
        self._lock = threading.RLock()
        self._state = GnssState(
            timestamp_monotonic_ns=time.monotonic_ns(),
            valid=False,
            latitude_deg=None,
            longitude_deg=None,
            altitude_m=None,
            speed_mps=None,
            heading_deg=None,
            fix_quality=None,
            satellites=None,
            last_rtcm_received_monotonic_ns=None,
            last_gga_sentence=None,
        )

    def update_from_gga(self, msg: Any, raw_line: bytes) -> None:
        lat = _float_or_none(_first_attr(msg, "lat", "latitude"))
        lon = _float_or_none(_first_attr(msg, "lon", "longitude"))
        alt = _float_or_none(_first_attr(msg, "alt", "altitude"))
        quality = _int_or_none(_first_attr(msg, "quality", "fix_quality"))
        satellites = _int_or_none(_first_attr(msg, "numSV", "siv", "satellites"))
        valid = bool(quality and quality > 0 and lat is not None and lon is not None)

        with self._lock:
            self._state = replace(
                self._state,
                timestamp_monotonic_ns=time.monotonic_ns(),
                valid=valid,
                latitude_deg=lat,
                longitude_deg=lon,
                altitude_m=alt,
                fix_quality=quality,
                satellites=satellites,
                last_gga_sentence=bytes(raw_line) if raw_line else None,
            )

    def update_from_rmc(self, msg: Any) -> None:
        status = _normalize_status(_first_attr(msg, "status", "posStatus", default=""))
        speed_knots = _float_or_none(_first_attr(msg, "spd", "speed", "speed_knots"))
        speed_mps = speed_knots * KNOTS_TO_MPS if speed_knots is not None else None

        rmc_valid: bool | None
        if status in {"A", "VALID"}:
            rmc_valid = True
        elif status in {"V", "INVALID"}:
            rmc_valid = False
        else:
            rmc_valid = None

        with self._lock:
            if rmc_valid is None:
                new_valid = self._state.valid
            else:
                has_fix = bool(
                    self._state.fix_quality
                    and self._state.fix_quality > 0
                    and self._state.latitude_deg is not None
                    and self._state.longitude_deg is not None
                )
                new_valid = bool(rmc_valid and has_fix)

            self._state = replace(
                self._state,
                timestamp_monotonic_ns=time.monotonic_ns(),
                valid=new_valid,
                speed_mps=speed_mps,
            )

    def snapshot(self) -> GnssState:
        with self._lock:
            return replace(self._state)

    def update_from_hdt(self, msg: Any) -> None:
        heading_deg = _float_or_none(_first_attr(msg, "headingT", "heading", "heading_deg"))
        with self._lock:
            self._state = replace(
                self._state,
                timestamp_monotonic_ns=time.monotonic_ns(),
                heading_deg=heading_deg,
            )

    def mark_rtcm_received(self) -> None:
        with self._lock:
            self._state = replace(
                self._state,
                last_rtcm_received_monotonic_ns=time.monotonic_ns(),
            )

    def get_last_gga(self) -> bytes | None:
        with self._lock:
            return bytes(self._state.last_gga_sentence) if self._state.last_gga_sentence else None


def _first_attr(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_status(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii", errors="ignore")
        except Exception:  # pylint: disable=broad-exception-caught
            value = ""
    return str(value).strip().upper()
