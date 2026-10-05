"""Business entry point: initialize, migrate, serve, or ask the existing application."""

from deephelp_app.bootstrap.commands.mvp_cli import main

# -m只在显式执行时进入main；import不会打开PG、模型或stdio进程。
if __name__ == "__main__":
    raise SystemExit(main())
