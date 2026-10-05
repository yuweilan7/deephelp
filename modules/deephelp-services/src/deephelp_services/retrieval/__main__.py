from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_services.retrieval.api import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError, ConfigurationError:
        print("Retrieval service configuration/dependency failure")
        raise SystemExit(1) from None
