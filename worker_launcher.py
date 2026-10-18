import sys

import browser_open


def configure_utf8_stdio() -> None:
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "strict"), (sys.stderr, "replace")):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors=errors)


if __name__ == "__main__":
    configure_utf8_stdio()
    if sys.argv[1:2] == ["--browser-open"]:
        raise SystemExit(browser_open.main(sys.argv[2:]))
    from codex_worker_process import main
    raise SystemExit(main())
