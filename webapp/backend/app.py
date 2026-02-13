"""
Miximus Custodial Mixing Service — Flask entry point.
"""

import os
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

    app.register_blueprint(assets_bp, url_prefix='/api')
    app.register_blueprint(pool_bp, url_prefix='/api')
    app.register_blueprint(mix_bp, url_prefix='/api')

    with app.app_context():
        db.create_all()

    # Start background order processor (skip in debug reloader child)
    if not app.config.get('TESTING') and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        try:
            from order_processor import OrderProcessor
            processor = OrderProcessor(app)
            processor.start()
            app.order_processor = processor
        except Exception as e:
            print(f"Warning: Order processor not started: {e}")

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
