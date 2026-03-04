"""
Miximus Custodial Mixing Service — Flask entry point.
"""

import os
import logging
from dotenv import load_dotenv

# Load .env BEFORE anything reads os.environ (config, order_processor, etc.)
load_dotenv(os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')), '.env'))

from flask import Flask
from flask_cors import CORS
from models import db
from config import DevelopmentConfig, ProductionConfig


def create_app(config_class=None):
    if config_class is None:
        config_class = ProductionConfig if os.environ.get('FLASK_ENV') == 'production' else DevelopmentConfig

    app = Flask(__name__)
    app.config.from_object(config_class)

    CORS(app, origins=[
        'http://localhost:5173',
        'http://localhost:3000',
        'http://127.0.0.1:5173',
    ])

    db.init_app(app)

    # Register route blueprints
    from routes.assets import assets_bp
    from routes.pool import pool_bp
    from routes.mix import mix_bp
    from routes.admin import admin_bp

    app.register_blueprint(assets_bp, url_prefix='/api')
    app.register_blueprint(pool_bp, url_prefix='/api')
    app.register_blueprint(mix_bp, url_prefix='/api')
    app.register_blueprint(admin_bp, url_prefix='/api')

    with app.app_context():
        db.create_all()

    # Start background order processor in the right process:
    # - debug mode: only in the reloader child (WERKZEUG_RUN_MAIN == 'true')
    # - non-debug mode: always (WERKZEUG_RUN_MAIN is not set)
    should_start = not app.config.get('TESTING')
    if app.debug:
        should_start = should_start and os.environ.get('WERKZEUG_RUN_MAIN') == 'true'

    if should_start:
        try:
            from order_processor import OrderProcessor
            processor = OrderProcessor(app)
            processor.start()
            app.order_processor = processor
        except Exception as e:
            print(f"Warning: Order processor not started: {e}", flush=True)

    return app


if __name__ == '__main__':
    # Ensure OrderProcessor logs are visible in the console
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
