import os

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            revision=os.getenv("APP_REVISION", "local"),
        )

    return app
