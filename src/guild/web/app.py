"""Das Studio im Browser: Aufträge, Runden, Findings, Vorschau.

Bindet auf Loopback, keine Anmeldung: das ist ein Fenster auf den Kundenordner, kein
Dienst. Jeder Pfad aus der URL wird gegen den Kundenordner aufgelöst und verlassen
verweigert.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, send_from_directory, url_for

from .. import engine, ui
from .. import brief as brief_modul
from ..store import Store

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
RUNDE_RE = re.compile(r"^v\d{1,3}$")


def _brief(slug: str):
    if not SLUG_RE.match(slug):
        abort(404)
    try:
        return brief_modul.load_slug(slug)
    except brief_modul.BriefError:
        abort(404)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["GUILD_STORE"] = Store()

    @app.template_filter("datum")
    def datum(wert: str) -> str:
        return (wert or "").replace("T", " ")[:16]

    @app.route("/")
    def index():
        store: Store = app.config["GUILD_STORE"]
        auftraege = []
        for brief in brief_modul.alle():
            zustand = engine.stand(brief, store)
            auftraege.append({
                "brief": brief,
                "zustand": zustand,
                "gate": ("gruen" if zustand.bereit else
                         ("rot" if zustand.ergebnis and not zustand.ergebnis.bestanden
                          else "leer")),
            })
        return render_template("index.html", auftraege=auftraege, titel="Aufträge")

    @app.route("/job/<slug>")
    def job(slug: str):
        brief = _brief(slug)
        store: Store = app.config["GUILD_STORE"]
        zustand = engine.stand(brief, store)
        return render_template("job.html", brief=brief, zustand=zustand,
                               verlauf=store.rounds(slug),
                               ereignisse=store.events(slug, limit=12),
                               rollen=engine.rollenplan(brief),
                               titel=brief.kunde)

    @app.post("/job/<slug>/run")
    def run(slug: str):
        brief = _brief(slug)
        store: Store = app.config["GUILD_STORE"]
        runde = brief_modul.runden(brief)
        if runde:
            engine.messen(brief, runde[-1], store)
        return redirect(url_for("job", slug=brief.slug))

    @app.route("/job/<slug>/compare/<a>/<b>")
    def compare(slug: str, a: str, b: str):
        brief = _brief(slug)
        for runde in (a, b):
            if not RUNDE_RE.match(runde):
                abort(404)
        diff = engine.vergleiche(brief, a, b, app.config["GUILD_STORE"])
        return render_template("compare.html", brief=brief, diff=diff,
                               titel=f"{a} → {b}")

    @app.route("/job/<slug>/preview/")
    @app.route("/job/<slug>/preview/<path:rest>")
    def preview(slug: str, rest: str = "index.html"):
        brief = _brief(slug)
        runden = brief_modul.runden(brief)
        if not runden:
            abort(404)
        wurzel = (brief.ordner / "runden" / runden[-1]).resolve()
        ziel = (wurzel / rest).resolve()
        if not str(ziel).startswith(str(wurzel)) or not ziel.is_file():
            abort(404)
        return send_from_directory(ziel.parent, ziel.name)

    @app.get("/api/jobs")
    def api_jobs():
        store: Store = app.config["GUILD_STORE"]
        auftraege = []
        for brief in brief_modul.alle():
            eintrag = store.job(brief.slug) or {"slug": brief.slug, "kunde": brief.kunde}
            auftraege.append({**eintrag, "runden": len(brief_modul.runden(brief))})
        return jsonify(auftraege)

    @app.get("/api/job/<slug>")
    def api_job(slug: str):
        brief = _brief(slug)
        zustand = engine.stand(brief, app.config["GUILD_STORE"])
        return jsonify({
            "slug": brief.slug,
            "kunde": brief.kunde,
            "runden": zustand.runden,
            "bereit": zustand.bereit,
            "grund": zustand.grund,
            "gate": zustand.ergebnis.as_dict() if zustand.ergebnis else None,
        })

    @app.errorhandler(404)
    def nicht_gefunden(_):
        return render_template("fehlt.html"), 404

    return app
