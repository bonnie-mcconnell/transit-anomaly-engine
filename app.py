"""
Flask dashboard for the NX1/NX2 delay tracker.

Two routes:
  GET /             serves the HTML dashboard
  GET /api/status   returns current scored observations as JSON

The HTML page polls /api/status every 3 minutes via fetch() and
updates the table in place without a full reload.
"""

import os

from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

import score

load_dotenv()

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    result = score.get_current_status()
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)