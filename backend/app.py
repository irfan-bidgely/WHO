import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template

from insights import insights_bp

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.register_blueprint(insights_bp)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    logger.info("Starting Flask on http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
