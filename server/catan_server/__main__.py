"""Run the built application directly, without a Node/npm parent process."""

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Host one Catan lobby")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    if args.data_dir:
        os.environ["CATAN_DATA_DIR"] = args.data_dir
    uvicorn.run(
        "catan_server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=1,
        ws_max_size=65536,
        ws_ping_interval=15,
        ws_ping_timeout=15,
    )


if __name__ == "__main__":
    main()
