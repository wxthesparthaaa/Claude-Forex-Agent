"""
Run locally with:
    python app.py
On Render, gunicorn runs this via `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
-- workers MUST stay at 1, matching the sibling options-agent project's
reasoning: a second worker would start its own background scheduler and
duplicate every Telegram send.

Skeleton stage: serves /health for UptimeRobot and a placeholder dashboard.
Strategy modules (OANDA adapters, signals, risk engine, scan/approval
workflow) land in src/ in subsequent passes -- see PROJECT_LOG.md.
"""
import os

from flask import Flask, render_template

app = Flask(__name__)


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
def dashboard():
    return render_template("dashboard.html", status="Skeleton running")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
