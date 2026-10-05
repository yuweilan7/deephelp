from deephelp_app.bootstrap.commands.mvp_cli import main
from deephelp_tools.learning.workbench.app import install

if __name__ == "__main__":
    raise SystemExit(main(configure_app=install))
