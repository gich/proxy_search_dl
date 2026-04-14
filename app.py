import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

app = Flask(__name__)

FIND_PROXY_BIN = "/usr/local/bin/find-proxy"
RESULTS_DIR = Path(tempfile.gettempdir()) / "find-proxy-web"
RESULTS_DIR.mkdir(exist_ok=True)
RESULT_TTL_SECONDS = 10 * 60
TOKEN_RE = re.compile(r"^[\w\.\-:@/+%]+$")
MAX_TOKEN_LEN = 200
DEFAULT_PER_PAGE = 200
UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def cleanup_old_results():
    now = time.time()
    for p in RESULTS_DIR.iterdir():
        try:
            if now - p.stat().st_mtime > RESULT_TTL_SECONDS:
                p.unlink()
        except OSError:
            pass


def validate_tokens(raw):
    tokens = raw.split()
    if not tokens:
        return None, "Пустой запрос"
    for t in tokens:
        if len(t) > MAX_TOKEN_LEN or not TOKEN_RE.match(t):
            return None, f"Недопустимый токен: {t!r}"
    return tokens, None


def result_path(rid):
    if not UUID_RE.match(rid):
        abort(404)
    p = RESULTS_DIR / f"{rid}.txt"
    if not p.exists():
        abort(404)
    return p


@app.route("/")
def root():
    return redirect(url_for("index"))


@app.route("/searchnginxlog")
def index():
    return render_template("index.html", query="", error=None)


@app.route("/search", methods=["POST"])
def search():
    cleanup_old_results()
    raw = request.form.get("q", "").strip()
    tokens, err = validate_tokens(raw)
    if err:
        return render_template("index.html", query=raw, error=err), 400

    started = time.time()
    try:
        proc = subprocess.run(
            ["sudo", "-n", FIND_PROXY_BIN, *tokens],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        return render_template("index.html", query=raw, error=f"Не найден бинарь: {e}"), 500

    elapsed = time.time() - started
    app.logger.info("find-proxy tokens=%s rc=%s time=%.2fs", tokens, proc.returncode, elapsed)

    if proc.returncode != 0 and not proc.stdout:
        return render_template(
            "index.html",
            query=raw,
            error=f"find-proxy rc={proc.returncode}: {proc.stderr.strip()}",
        ), 500

    rid = uuid.uuid4().hex
    out_path = RESULTS_DIR / f"{rid}.txt"
    out_path.write_text(proc.stdout, encoding="utf-8", errors="replace")

    return redirect(url_for("results", rid=rid, page=1, q=raw))


@app.route("/results/<rid>")
def results(rid):
    path = result_path(rid)
    try:
        per_page = max(10, min(2000, int(request.args.get("per_page", DEFAULT_PER_PAGE))))
    except ValueError:
        per_page = DEFAULT_PER_PAGE
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    query = request.args.get("q", "")

    with path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

    total = len(lines)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    chunk = lines[start:start + per_page]

    return render_template(
        "results.html",
        rid=rid,
        query=query,
        lines=chunk,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        start_index=start,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
