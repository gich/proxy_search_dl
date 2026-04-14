import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

import db

app = Flask(__name__)

FIND_PROXY_BIN = "/usr/local/bin/find-proxy"
RESULTS_DIR = Path(tempfile.gettempdir()) / "find-proxy-web"
RESULTS_DIR.mkdir(exist_ok=True)
RESULT_TTL_SECONDS = 10 * 60
TOKEN_RE = re.compile(r"^[\w\.\-:@/+%]+$")
MAX_TOKEN_LEN = 200
DEFAULT_PER_PAGE = 200
UUID_RE = re.compile(r"^[0-9a-f]{32}$")

DB_CACHE_MAX = 32
DB_CACHE_TTL = 10 * 60
_db_cache: "OrderedDict[str, tuple[float, list[dict]]]" = OrderedDict()


def db_cache_put(rid: str, rows: list[dict]) -> None:
    _db_cache[rid] = (time.time(), rows)
    _db_cache.move_to_end(rid)
    while len(_db_cache) > DB_CACHE_MAX:
        _db_cache.popitem(last=False)


def db_cache_get(rid: str) -> list[dict] | None:
    entry = _db_cache.get(rid)
    if not entry:
        return None
    ts, rows = entry
    if time.time() - ts > DB_CACHE_TTL:
        _db_cache.pop(rid, None)
        return None
    _db_cache.move_to_end(rid)
    return rows


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


@app.route("/requestfromsite", methods=["GET", "POST"])
def requestfromsite():
    if request.method == "POST":
        q = request.form.get("q", "").strip()
        if not q:
            return render_template("requestfromsite.html", query="", error="Пустой запрос", rid=None, rows=None), 400
        if len(q) > MAX_TOKEN_LEN:
            return render_template("requestfromsite.html", query=q, error="Слишком длинное значение", rid=None, rows=None), 400
        try:
            rows = db.fetch_rows(q)
        except Exception as e:
            app.logger.exception("db.fetch_rows failed")
            return render_template("requestfromsite.html", query=q, error=f"Ошибка БД: {e}", rid=None, rows=None), 500
        rid = uuid.uuid4().hex
        db_cache_put(rid, rows)
        return redirect(url_for("requestfromsite_results", rid=rid, q=q))

    return render_template("requestfromsite.html", query="", error=None, rid=None, rows=None)


@app.route("/requestfromsite/<rid>")
def requestfromsite_results(rid):
    if not UUID_RE.match(rid):
        abort(404)
    rows = db_cache_get(rid)
    if rows is None:
        return render_template(
            "requestfromsite.html",
            query=request.args.get("q", ""),
            error="Результаты истекли, выполните запрос снова",
            rid=None,
            rows=None,
        ), 404
    return render_template(
        "requestfromsite.html",
        query=request.args.get("q", ""),
        error=None,
        rid=rid,
        rows=rows,
    )


@app.route("/requestfromsite/<rid>/<int:idx>")
def requestfromsite_row(rid, idx):
    if not UUID_RE.match(rid):
        abort(404)
    rows = db_cache_get(rid)
    if rows is None:
        abort(404)
    if idx < 0 or idx >= len(rows):
        abort(404)
    row = rows[idx]
    body = row.get("body") or ""
    try:
        parsed = json.loads(body)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
        valid = True
    except (ValueError, TypeError):
        pretty = body
        valid = False
    return render_template(
        "json_view.html",
        rid=rid,
        idx=idx,
        row=row,
        pretty=pretty,
        valid=valid,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
