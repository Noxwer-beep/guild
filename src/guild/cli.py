"""Guild — Briefing, Runden, Gate, Abgabe.

    guild                       Übersicht aller Aufträge
    guild new <slug> …          Briefing anlegen
    guild run <slug> [runde]    Runde durch das Gate schicken
    guild show <slug>           Stand, Findings, Verlauf
    guild compare <slug> v1 v2  zwei Runden vergleichen
    guild ship <slug>           abgeben (nur bei grünem Gate)
    guild plan <slug>           was welche Rolle liefert
    guild web                   Studio im Browser (Port 8800)

Exit-Codes: 0 ok · 1 Fehler · 2 Aufruf-/Briefingfehler · 3 Gate rot (Abgabe verweigert)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, agents, brief as brief_modul, config, engine, ui
from .store import Store


def cmd_uebersicht(_: argparse.Namespace) -> int:
    ui.kopf("Studio-Übersicht")
    store = Store()
    briefings = brief_modul.alle()
    for b in briefings:
        store.upsert_job(b.slug, b.kunde, b.produkt)

    zeilen = []
    for b in briefings:
        zustand = engine.stand(b, store)
        zeilen.append({
            "slug": b.slug, "kunde": b.kunde, "runden": len(zustand.runden),
            "gate": ("grün" if zustand.bereit else
                     ("rot" if zustand.ergebnis and not zustand.ergebnis.bestanden else "—")),
            "stand": zustand.grund[:30],
        })
    ui.jobs(zeilen)
    console = ui.console
    if briefings:
        console.print()
        ui.hint(f"{len(briefings)} Auftrag/Aufträge in {config.CLIENTS}")
    console.print()
    ui.hint("guild new <slug> · guild run <slug> · guild ship <slug>")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    try:
        brief, pfad = brief_modul.neu(
            args.slug, kunde=args.kunde, produkt=args.produkt,
            zielgruppe=args.zielgruppe, ziele=args.ziel, seiten=args.seite,
            ton=args.ton, sprache=args.sprache,
            einschraenkungen=args.einschraenkung or [],
        )
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    Store().upsert_job(brief.slug, brief.kunde, brief.produkt)
    ui.ok(f"Briefing angelegt: {pfad}")
    ui.hint(f"Runden liegen unter {brief.ordner / 'runden'}/v1, v2, …")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    ergebnis = engine.messen(brief, args.runde)
    ui.head(f"{brief.slug} · {ergebnis.runde}",
            f"{ergebnis.score}/100 · {ergebnis.kritisch} kritisch · "
            f"{ergebnis.warnungen} Warnungen · {ergebnis.dateien} Dateien")
    ui.findings([f.as_dict() for f in ergebnis.findings])
    return 0 if ergebnis.bestanden else 3


def cmd_build(args: argparse.Namespace) -> int:
    """Eine oder mehrere Runden von Hermes-Agenten bauen lassen."""
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    if not agents.verfuegbar():
        ui.fail("hermes nicht im PATH — ohne Agent kann nichts gebaut werden")
        return 3

    store = Store()
    ui.head(f"{brief.slug} — Agenten bauen lassen",
            f"bis {args.runden} Runden · Modell {args.modell or 'Standard'} · "
            f"Zeitbudget {args.budget}s je Lauf · "
            f"{'Rollen nacheinander' if args.klassisch else 'Strategie → Art ∥ Text → Bau'}")

    def protokoll(run, ergebnis) -> None:
        store.save_run(brief.slug, run)
        # Ein gekappter Lauf (Züge oder Zeit) kann trotzdem abgeben — dann nicht "✗".
        marke = "✓" if run.ok else ("⚑" if ergebnis else "✗")
        ui.console.print(
            f"  {marke} {run.rolle:<7} {run.runde:<4} {run.dauer_ms / 1000:5.1f}s  "
            f"{run.tokens / 1000:>6.0f}k Tokens", end="")
        if ergebnis:
            farbe = "green" if ergebnis.bestanden else "red"
            zusatz = "" if run.ok else " · gekappt"
            ui.console.print(f"  · Gate [{farbe}]{ergebnis.score}/100[/] "
                             f"({ergebnis.kritisch} kritisch){zusatz}")
            ui.findings([f.as_dict() for f in ergebnis.findings], limit=6)
        elif run.ok:
            ui.console.print("  · [dim]Beitrag geliefert[/]")
        else:
            ui.console.print(f"  · [red]{run.fehler or 'kein Ergebnis'}[/]")

    laeufe = agents.automatik(brief, args.runden, args.modell or None,
                              args.budget, protokoll, rollen=not args.klassisch)
    if not laeufe:
        ui.fail("kein Lauf ausgeführt")
        return 1

    gesamt = sum(r.tokens for r in laeufe)
    ui.console.print()
    ui.ok(f"{len(laeufe)} Lauf/Läufe · {gesamt / 1000:.0f}k Tokens · "
          f"{sum(r.dauer_ms for r in laeufe) / 1000:.0f}s")

    zustand = engine.stand(brief, store)
    if zustand.bereit:
        ui.hint("Gate grün — `guild ship " + brief.slug + "` gibt frei")
        return 0
    ui.hint(f"nicht bereit: {zustand.grund}")
    return 3


def cmd_revise(args: argparse.Namespace) -> int:
    """Nächste Runde aus der Kritik bauen."""
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    ergebnis = agents.revidiere(brief, args.modell or None, args.budget)
    if ergebnis is None:
        ui.fail("keine Kritik gefunden — erst `guild build` laufen lassen")
        return 3
    runde, run = ergebnis
    store = Store()
    store.save_run(brief.slug, run)
    gemessen = engine.messen(brief, runde, store)
    marke = "✓" if run.ok else "⚑"
    ui.console.print(f"  {marke} revision {runde:<4} {run.dauer_ms / 1000:5.1f}s  "
                     f"{run.tokens / 1000:>6.0f}k Tokens  · Gate {gemessen.score}/100 "
                     f"({gemessen.kritisch} kritisch)")
    ui.findings([f.as_dict() for f in gemessen.findings], limit=8)
    return 0 if gemessen.bestanden else 3


def cmd_show(args: argparse.Namespace) -> int:
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    store = Store()
    zustand = engine.stand(brief, store)
    ui.head(f"{brief.kunde} — {brief.produkt}", f"{brief.slug} · {brief.zielgruppe}")
    ui.console.print(f"  Runden:  {', '.join(zustand.runden) or '—'}")
    ui.console.print(f"  Letzte:  {zustand.letzte or '—'}")
    if zustand.ergebnis:
        ui.console.print(f"  Gate:    {zustand.ergebnis.score}/100 · "
                         f"{zustand.ergebnis.kritisch} kritisch · "
                         f"{zustand.ergebnis.warnungen} Warnungen")
    ui.console.print(f"  Abgabe:  {'möglich' if zustand.bereit else 'nicht möglich'} — "
                     f"{zustand.grund}")
    ui.console.print()
    ui.head("Offene Findings")
    ui.findings(zustand.offen, limit=99 if args.all else 18)
    verlauf = store.rounds(brief.slug)
    if verlauf:
        ui.console.print()
        ui.head("Verlauf")
        ui.runden(verlauf)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    diff = engine.vergleiche(brief, args.von, args.nach)
    ui.head(f"{args.von} → {args.nach}",
            f"Score {diff['score']['von']} → {diff['score']['nach']} · "
            f"kritisch {diff['kritisch']['von']} → {diff['kritisch']['nach']}")
    for titel, schluessel in (("Neu", "hinzu"), ("Geändert", "geaendert"),
                              ("Entfernt", "weg")):
        dateien = diff["dateien"][schluessel]
        ui.console.print(f"  {titel:<10} {len(dateien):>2}  " +
                         ", ".join(dateien[:6]) + (" …" if len(dateien) > 6 else ""))
    ui.console.print()
    if diff["behoben"]:
        ui.head(f"Behoben ({len(diff['behoben'])})")
        ui.findings(diff["behoben"], limit=10)
    if diff["neu"]:
        ui.console.print()
        ui.head(f"Neu ({len(diff['neu'])})")
        ui.findings(diff["neu"], limit=10)
    if not diff["behoben"] and not diff["neu"]:
        ui.ok("keine Änderung an den Findings")
    return 0


def cmd_ship(args: argparse.Namespace) -> int:
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    try:
        ziel = engine.abgeben(brief)
    except RuntimeError as exc:
        ui.fail(str(exc))
        ui.hint("guild run <slug> zeigt, was fehlt")
        return 3
    ui.ok(f"abgegeben: {ziel}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2
    ui.head(f"Rollenplan — {brief.slug}", "in dieser Reihenfolge, jede Rolle liefert")
    ui.plan(engine.rollenplan(brief))
    ui.console.print()
    ui.hint(f"Arbeitsordner: {brief.ordner / 'runden'} → v1, v2, v3 …")
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    """Die Abgabe als statische Seite ausliefern — zum Ansehen, nicht zum Betrieb."""
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    try:
        brief = brief_modul.load_slug(args.slug)
    except brief_modul.BriefError as exc:
        ui.fail(str(exc))
        return 2

    quelle = brief.ordner / config.ABGABE
    if not quelle.exists():
        runden = brief_modul.runden(brief)
        if not runden:
            ui.fail("nichts zu zeigen — erst bauen oder abgeben")
            return 3
        quelle = brief_modul.runde_pfad(brief, runden[-1])
        ui.warn(f"keine Abgabe, zeige {runden[-1]}")

    class Handler(SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *_args) -> None:   # leise
            pass

    handler = functools.partial(Handler, directory=str(quelle))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    ui.ok(f"{brief.kunde}: http://{args.host}:{args.port}/")
    ui.hint(f"aus {quelle} — Strg+C beendet")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ui.hint("beendet")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web.app import create_app
    app = create_app()
    ui.ok(f"Studio auf http://{args.host}:{args.port}")
    ui.hint("loopback, keine Anmeldung — kein Dienst, nur ein Fenster")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


def cmd_cat(_: argparse.Namespace) -> int:
    ui.kopf()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="guild", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"guild {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    neu = sub.add_parser("new", help="Briefing anlegen")
    neu.add_argument("slug")
    neu.add_argument("--kunde", required=True)
    neu.add_argument("--produkt", required=True)
    neu.add_argument("--zielgruppe", required=True)
    neu.add_argument("--ziel", action="append", required=True)
    neu.add_argument("--seite", action="append", required=True)
    neu.add_argument("--ton", default="sachlich, klar, ohne Werbesprache")
    neu.add_argument("--sprache", default="de", choices=("de", "en"))
    neu.add_argument("--einschraenkung", action="append")
    neu.set_defaults(func=cmd_new)

    run = sub.add_parser("run", help="Runde durch das Gate schicken")
    run.add_argument("slug")
    run.add_argument("runde", nargs="?")
    run.set_defaults(func=cmd_run)

    build = sub.add_parser("build", help="Runden von Hermes-Agenten bauen lassen")
    build.add_argument("slug")
    build.add_argument("--runden", type=int, default=config.MIN_ROUNDS + 1)
    build.add_argument("--modell", default="")
    build.add_argument("--budget", type=int, default=900)
    build.add_argument("--klassisch", action="store_true",
                       help="alle Rollen in einem Durchgang statt parallel")
    build.set_defaults(func=cmd_build)

    revise = sub.add_parser("revise", help="Kritik abarbeiten, nächste Runde")
    revise.add_argument("slug")
    revise.add_argument("--modell", default="")
    revise.add_argument("--budget", type=int, default=420)
    revise.set_defaults(func=cmd_revise)

    show = sub.add_parser("show", help="Stand eines Auftrags")
    show.add_argument("slug")
    show.add_argument("--all", action="store_true", help="alle Findings")
    show.set_defaults(func=cmd_show)

    compare = sub.add_parser("compare", help="zwei Runden vergleichen")
    compare.add_argument("slug")
    compare.add_argument("von")
    compare.add_argument("nach")
    compare.set_defaults(func=cmd_compare)

    ship = sub.add_parser("ship", help="abgeben")
    ship.add_argument("slug")
    ship.set_defaults(func=cmd_ship)

    plan = sub.add_parser("plan", help="Rollenplan")
    plan.add_argument("slug")
    plan.set_defaults(func=cmd_plan)

    preview = sub.add_parser("preview", help="Abgabe als Seite ausliefern")
    preview.add_argument("slug")
    preview.add_argument("--host", default="127.0.0.1")
    preview.add_argument("--port", type=int, default=8090)
    preview.set_defaults(func=cmd_preview)

    web = sub.add_parser("web", help="Studio im Browser")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8800)
    web.set_defaults(func=cmd_web)

    cat = sub.add_parser("cat", help="die Marke")
    cat.set_defaults(func=cmd_cat)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_uebersicht(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
