from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_services.business.api import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AppError, ConfigurationError:
        print("Business service configuration/database failure")
        raise SystemExit(1) from None
