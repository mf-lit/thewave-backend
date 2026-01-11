"""Flask application initialization."""
import logging
import sys

from flask import Flask

from src.api.routes import bp
from src.utils.config import validate_config_file

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    # Validate config file at startup (unless auth is disabled)
    try:
        validate_config_file()
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Configuration validation failed: {e}")
        sys.exit(1)
    
    app = Flask(__name__)
    app.register_blueprint(bp)

    @app.route("/health")
    def health():
        """Health check endpoint."""
        return {"status": "ok"}, 200

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5001, debug=True)

