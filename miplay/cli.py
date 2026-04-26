"""CLI entrypoint for MiPlay."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from miplay.app import MiPlay
from miplay.config import Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiPlay - AirPlay bridge for Xiaomi speakers")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Start the MiPlay runtime")
    serve.add_argument("--conf-path", default="conf", help="Configuration directory")
    serve.add_argument("--host", default="", help="Advertised LAN IP or hostname")
    serve.add_argument("--web-port", type=int, default=0, help="Web UI port")
    serve.add_argument("--verbose", action="store_true", help="Enable debug logging")

    return parser.parse_args()


def main():
    args = parse_args()
    command = args.command or "serve"
    if command != "serve":
        raise SystemExit(f"Unsupported command: {command}")

    config = Config.load(args.conf_path)
    if args.host:
        config.host = args.host
    if args.web_port:
        config.web_port = args.web_port
    if args.verbose:
        config.verbose = True
    config.save()

    app = MiPlay(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(app.run_forever())

    def shutdown():
        main_task.cancel()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, shutdown)
        loop.add_signal_handler(signal.SIGTERM, shutdown)

    try:
        loop.run_until_complete(main_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        if not loop.is_closed():
            try:
                loop.run_until_complete(asyncio.wait_for(app.stop(), timeout=5.0))
            except Exception:
                pass
            finally:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    try:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except Exception:
                        pass
                loop.close()


if __name__ == "__main__":
    main()

