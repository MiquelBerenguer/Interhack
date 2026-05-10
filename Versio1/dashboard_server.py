# -*- coding: utf-8 -*-
"""
Sirve dashboard.html + output/, y lanza el pipeline con progreso vía SSE.

  cd Versio1
  pip install flask
  set GOOGLE_MAPS_API_KEY=...
  python dashboard_server.py
  → http://127.0.0.1:8765
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time

from flask import Flask, Response, jsonify, request, send_from_directory

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)

app = Flask(__name__, static_folder=_HERE)
progress_queues: dict[str, queue.Queue] = {}


def _scope_norm(raw: str | None) -> str:
    s = (raw or "day").strip().lower()
    if s == "all":
        return "full"
    return s


@app.route("/")
def index():
    return send_from_directory(_HERE, "dashboard.html")


@app.route("/output/<path:filename>")
def output_files(filename):
    outp = os.path.join(_HERE, "output")
    return send_from_directory(outp, filename)


@app.route("/run", methods=["POST"])
def run_pipeline():
    dest_hackaton = os.path.join(_HERE, "Hackaton.xlsx")
    ctype = request.content_type or ""

    scope = "day"
    date_start: str | None = None
    date_end: str | None = None

    if "multipart/form-data" in ctype:
        file = request.files.get("file")
        if file and getattr(file, "filename", ""):
            file.save(dest_hackaton)
        scope = _scope_norm(request.form.get("scope"))
        date_start = request.form.get("date") or request.form.get("fecha")
        date_end = request.form.get("date_end")
    else:
        data = request.get_json(silent=True) or {}
        scope = _scope_norm(data.get("scope"))
        date_start = data.get("date")
        date_end = data.get("date_end")

    if not os.path.isfile(dest_hackaton):
        return (
            jsonify(
                {
                    "ok": False,
                    "error": (
                        "No existe Hackaton.xlsx en Versio1. "
                        "Sube el archivo o colócalo manualmente antes de ejecutar."
                    ),
                }
            ),
            400,
        )

    job_id = str(int(time.time() * 1000))
    q: queue.Queue = queue.Queue(maxsize=0)
    progress_queues[job_id] = q

    def runner() -> None:
        try:
            from run_full_pipeline import run_pipeline_with_progress

            run_pipeline_with_progress(
                scope=scope,
                date_start=date_start,
                date_end=date_end,
                hackaton_path=dest_hackaton,
                progress_callback=lambda msg, pct: q.put({"msg": msg, "pct": pct}),
            )
            q.put({"msg": "Completado", "pct": 100, "done": True})
        except Exception as e:
            import traceback

            q.put(
                {
                    "msg": str(e),
                    "pct": 0,
                    "error": True,
                    "done": True,
                    "trace": traceback.format_exc(),
                }
            )

    threading.Thread(target=runner, daemon=True).start()

    def _purge_later(jid: str) -> None:
        time.sleep(120)
        progress_queues.pop(jid, None)

    threading.Thread(target=lambda: _purge_later(job_id), daemon=True).start()

    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id):
    def generate():
        q = progress_queues.get(job_id)
        if q is None:
            yield (
                "data: "
                + json.dumps(
                    {"error": True, "done": True, "msg": "Job desconocido o expirado", "pct": 0}
                )
                + "\n\n"
            )
            return
        while True:
            try:
                event = q.get(timeout=30)
            except queue.Empty:
                yield "data: " + json.dumps({"keepalive": True}) + "\n\n"
                continue
            chunk = {"msg": event.get("msg"), "pct": event.get("pct", 0), "done": event.get("done")}
            if event.get("error"):
                chunk["error"] = True
            if event.get("trace"):
                chunk["trace"] = event["trace"]
            yield "data: " + json.dumps(chunk) + "\n\n"
            if event.get("done"):
                break

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    os.makedirs(os.path.join(_HERE, "output", "routes"), exist_ok=True)
    os.makedirs(os.path.join(_HERE, "output", "loading"), exist_ok=True)
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
