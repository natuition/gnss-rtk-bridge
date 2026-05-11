from __future__ import annotations

import argparse
import logging
import signal
import threading
import time

from rtk_bridge.config import load_config
from rtk_bridge.gnss_thread import GnssThread
from rtk_bridge.ntrip_thread import NtripClientThread
from rtk_bridge.state import SharedGnssState
from rtk_bridge.udp_publisher import UdpProtobufPublisher


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GNSS-RTK-bridge GNSS <-> NTRIP service")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="Path to INI configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger = logging.getLogger("gnss_rtk_bridge")

    cfg = load_config(args.config)

    state = SharedGnssState()
    publisher = UdpProtobufPublisher(cfg.udp.host, cfg.udp.port)
    serial_lock = threading.Lock()

    gnss_thread = GnssThread(
        serial_port=cfg.serial.port,
        baudrate=cfg.serial.baudrate,
        state=state,
        publisher=publisher,
        serial_lock=serial_lock,
        timeout_s=cfg.serial.timeout_s,
    )

    ntrip_thread: NtripClientThread | None = None
    if cfg.ntrip.enabled:
        ntrip_thread = NtripClientThread(
            host=cfg.ntrip.host,
            port=cfg.ntrip.port,
            mountpoint=cfg.ntrip.mountpoint,
            username=cfg.ntrip.username,
            password=cfg.ntrip.password,
            state=state,
            gnss_write_rtcm=gnss_thread.write_rtcm,
            gga_interval_s=cfg.ntrip.gga_interval_s,
            max_failures_before_stop=cfg.ntrip.max_failures_before_stop,
            initial_backoff_s=cfg.ntrip.initial_backoff_s,
            max_backoff_s=cfg.ntrip.max_backoff_s,
        )

    stop_event = threading.Event()

    def _handle_signal(_signum, _frame):
        logger.info("Shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    gnss_thread.start()
    if ntrip_thread is not None:
        ntrip_thread.start()

    logger.info("GNSS-RTK-bridge service started")

    try:
        while not stop_event.is_set():
            if ntrip_thread is not None and not ntrip_thread.is_alive():
                logger.error("NTRIP thread stopped unexpectedly, shutting down")
                stop_event.set()
                break
            time.sleep(0.5)
    finally:
        gnss_thread.stop()
        if ntrip_thread is not None:
            ntrip_thread.stop()

        gnss_thread.join(timeout=5.0)
        if ntrip_thread is not None:
            ntrip_thread.join(timeout=5.0)

        publisher.close()
        logger.info("GNSS-RTK-bridge service stopped")


if __name__ == "__main__":
    main()
