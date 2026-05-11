from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass


@dataclass(frozen=True)
class SerialConfig:
    port: str
    baudrate: int
    timeout_s: float


@dataclass(frozen=True)
class UdpConfig:
    host: str
    port: int


@dataclass(frozen=True)
class NtripConfig:
    enabled: bool
    host: str
    port: int
    mountpoint: str
    username: str
    password: str
    gga_interval_s: float
    max_failures_before_stop: int
    initial_backoff_s: float
    max_backoff_s: float


@dataclass(frozen=True)
class BridgeConfig:
    serial: SerialConfig
    udp: UdpConfig
    ntrip: NtripConfig


def load_config(path: str = "config.ini") -> BridgeConfig:
    parser = ConfigParser()
    read_files = parser.read(path)
    if not read_files:
        raise FileNotFoundError(f"Configuration file not found: {path}")

    serial = SerialConfig(
        port=parser["serial"]["port"],
        baudrate=int(parser["serial"]["baudrate"]),
        timeout_s=float(parser["serial"]["timeout_s"]),
    )

    udp = UdpConfig(
        host=parser["udp"]["host"],
        port=int(parser["udp"]["port"]),
    )

    ntrip = NtripConfig(
        enabled=parser.getboolean("ntrip", "enabled", fallback=True),
        host=parser["ntrip"]["host"],
        port=int(parser["ntrip"]["port"]),
        mountpoint=parser["ntrip"]["mountpoint"],
        username=parser["ntrip"]["username"],
        password=parser["ntrip"]["password"],
        gga_interval_s=float(parser["ntrip"]["gga_interval_s"]),
        max_failures_before_stop=int(
            parser.get("ntrip", "max_failures_before_stop", fallback="20")
        ),
        initial_backoff_s=float(
            parser.get("ntrip", "initial_backoff_s", fallback="2.0")
        ),
        max_backoff_s=float(parser.get("ntrip", "max_backoff_s", fallback="60.0")),
    )

    return BridgeConfig(serial=serial, udp=udp, ntrip=ntrip)
