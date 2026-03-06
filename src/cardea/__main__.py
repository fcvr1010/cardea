"""Entry point: python -m cardea  OR  cardea (when installed)."""

import argparse
import sys

import uvicorn

_LOCALHOST_ADDRESSES = frozenset(["127.0.0.1", "::1", "localhost"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cardea — credential-injecting reverse proxy"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)"
    )
    parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev mode)"
    )
    parser.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"]
    )
    args = parser.parse_args()

    if args.host not in _LOCALHOST_ADDRESSES:
        print(
            f"Error: Cardea must bind to localhost only (got '{args.host}'). "
            f"Accepted values: {', '.join(sorted(_LOCALHOST_ADDRESSES))}",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run(
        "cardea.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
