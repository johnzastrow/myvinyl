"""Run the myvinyl server: `uv run myvinyl` (binds to localhost only by default)."""

import argparse
import logging
import re

import uvicorn


class RedactLinkTokens(logging.Filter):
    """Keep one-time invite/reset tokens out of the access log."""

    _token = re.compile(r"/link/[^\s\"?]+")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                self._token.sub("/link/[redacted]", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="myvinyl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("uvicorn.access").addFilter(RedactLinkTokens())
    uvicorn.run(
        "myvinyl.main:app_from_env",
        factory=True,
        host=args.host,
        port=args.port,
        server_header=False,  # don't advertise the server software
        proxy_headers=True,  # trusted proxies: FORWARDED_ALLOW_IPS (default 127.0.0.1)
    )


if __name__ == "__main__":
    main()
