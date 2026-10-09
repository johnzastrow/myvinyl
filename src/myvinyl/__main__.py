"""Run the myvinyl server: `uv run myvinyl` (binds to localhost only by default)."""

import argparse
import logging

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="myvinyl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("myvinyl.main:app_from_env", factory=True, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
