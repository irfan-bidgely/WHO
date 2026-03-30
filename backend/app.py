import logging
from flask import Flask, render_template

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    logger.info("Starting Flask on http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
