"""Die Rollen als echte Hermes-Agenten.

Jede Runde ist ein Agentenlauf in einem eigenen Arbeitsordner: `hermes chat --oneshot`
mit begrenzten Zügen (`--max-turns`) und begrenzter Zeit (`--run-budget`). Der Agent liest
das Briefing, schreibt die Dateien mit seinen Werkzeugen, und was er dabei verbraucht, wird
aus dem `result`-Ereignis des Streams mitgeschrieben — Tokens, Dauer, Exit-Code.

Kein Agent ohne Grenze: Züge, Zeit und (über die Rundenzahl) die Gesamtzahl der Läufe sind
Kappungsgrößen. Ein Lauf, der sie überschreitet, endet und wird als solcher protokolliert.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import brief as brief_modul
from . import config, qa

STANDARD_MODELL = ""
MAX_TURNS = 30
BUDGET_SEKUNDEN = 900

#: Was der Agent in jedem Lauf als letztes tun soll — sonst hört er mitten im Satz auf.
ABSCHLUSS = (
    "Arbeite vollständig und ohne Rückfragen. Lege alle Dateien im aktuellen Ordner an. "
    "Antworte am Ende in höchstens fünf Zeilen: was du gebaut hast und was offen blieb."
)


@dataclass
class AgentRun:
    rolle: str
    runde: str
    ok: bool
    dauer_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    zuege: int = 0
    exit_code: int = 0
    text: str = ""
    fehler: str = ""

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def as_dict(self) -> dict:
        daten = asdict(self)
        daten["tokens"] = self.tokens
        return daten


def verfuegbar() -> bool:
    return shutil.which("hermes") is not None


# ---------------------------------------------------------------- Prompting

GATE_REGELN = """  1. Statisches HTML und CSS. Kein Framework, kein Build-Schritt, kein JavaScript nötig.
  2. Jede Seite: <title> (20–70 Zeichen), <meta name="description"> (50–165), genau ein <h1>,
     keine Sprünge in der Überschriftenhierarchie, <html lang="de">, viewport-Meta.
  3. Jedes <img> mit sinnvollem alt; Dekoration mit alt="".
  4. Keine toten internen Links: jede verlinkte Datei muss existieren.
  5. Farben als CSS-Variablen. Text auf Fläche mindestens 4.5:1 Kontrast, Akzentfarbe
     mindestens 3:1 — nachrechnen, nicht schätzen.
  6. Höchstens drei externe Abhängigkeiten, besser keine.
  7. HTML unter 140 KB, CSS unter 45 KB."""


def _briefkopf(brief: brief_modul.Brief) -> str:
    seiten = "\n".join(f"  - {s}" for s in brief.seiten)
    ziele = "\n".join(f"  - {z}" for z in brief.ziele)
    einschraenkungen = "\n".join(f"  - {e}" for e in brief.einschraenkungen) or "  - keine"
    fakten = "\n".join(f"  - {f}" for f in brief.fakten) or "  - keine"
    return (f"  Kunde:       {brief.kunde}\n"
            f"  Produkt:     {brief.produkt}\n"
            f"  Zielgruppe:  {brief.zielgruppe}\n"
            f"  Ton:         {brief.ton}\n"
            f"  Sprache:     {brief.sprache}\n"
            f"  Ziele:\n{ziele}\n"
            f"  Seiten:\n{seiten}\n"
            f"  Einschränkungen:\n{einschraenkungen}\n"
            f"  Fakten (verbindlich, nichts davon erfinden):\n{fakten}")


def rollenprompt(brief: brief_modul.Brief, rolle: str) -> str:
    """Der Auftrag an eine Vorbau-Rolle: Strategie, Art Direction oder Text."""
    kopf = _briefkopf(brief)
    if rolle == "strategie":
        return f"""Du bist die Strategie-Rolle eines Designstudios. Auftrag:

{kopf}

Lies das Briefing und schreibe `konzept.md` (höchstens eine Seite):
  1. Angebot in einem Satz — was der Kunde wirklich verkauft.
  2. Zielgruppe: wer entscheidet, wer bezahlt, was sie vorher wissen wollen.
  3. Seitenstruktur: nummerierte Abschnitte in Reihenfolge, je ein Satz Zweck.
  4. Tonalität: drei Adjektive, ein Beispielsatz.
  5. Was NICHT auf die Seite gehört (Behauptungen, die niemand belegen kann).

Schreibe keine anderen Dateien. {ABSCHLUSS}"""

    if rolle == "art":
        return f"""Du bist die Art-Direction-Rolle eines Designstudios. Auftrag:

{kopf}

Lies `konzept.md`. Dann zwei Dateien:

  a) `design/tokens.css` — nur CSS-Variablen in `:root`:
     --bg, --surface, --fg, --muted, --accent, --line,
     --size-1 … --size-5 (Typo-Skala), --space-1 … --space-6, --radius.
     Kontrast verbindlich: --fg auf --bg mindestens 7:1, --muted auf --bg mindestens 4.5:1,
     --accent auf --bg mindestens 3:1. Rechne die Werte nach, bevor du sie einsetzt.
  b) `design/artdirection.md` — Schriftwahl und warum, Raster, Abstände, drei Regeln, die
     der Bau einhalten muss, und was du bewusst weglässt.

Keine Seite bauen. {ABSCHLUSS}"""

    if rolle == "text":
        return f"""Du bist die Text-Rolle eines Designstudios. Auftrag:

{kopf}

Lies `konzept.md` und schreibe `inhalte.md`:
  - `<title>` und `<meta name="description">` (Längen beachten: 20–70 / 50–165 Zeichen)
  - H1 und je Abschnitt der Struktur: Überschrift und Text, 2–4 Sätze
  - ein Call-to-Action mit genauem Wortlaut und Ziel
  - Fußzeile mit Kontaktweg

Deutsch, in der Stimme des Kunden. Keine Floskeln („innovativ", „maßgeschneidert",
„Leidenschaft"), keine erfundenen Zahlen, Referenzen oder Preise. Keine Seite bauen.
{ABSCHLUSS}"""

    raise ValueError(f"unbekannte Rolle: {rolle}")


def bauprompt(brief: brief_modul.Brief, runde: str, findings: list[dict] | None = None) -> str:
    """Der Auftrag an den Agenten — Briefing, Regeln des Gates, Abgabedateien."""
    seiten = "\n".join(f"  - {s}" for s in brief.seiten)
    ziele = "\n".join(f"  - {z}" for z in brief.ziele)
    einschraenkungen = "\n".join(f"  - {e}" for e in brief.einschraenkungen) or "  - keine"

    revision = ""
    if findings:
        zeilen = []
        for f in findings:
            zeilen.append(f'  - [{f["severity"]}] {f["title"]} — {f["detail"]} '
                          f'({f["file"] or "Projekt"})')
        revision = (
            "\nDas ist eine REVISION. Diese Findings der letzten Runde müssen weg, jedes "
            "einzeln, ohne neue einzubauen:\n" + "\n".join(zeilen) + "\n")

    return f"""Du bist das Studio für {brief.kunde}: Strategie, Art Direction, Text und Bau in
einem Durchlauf. Du arbeitest im Ordner dieser Runde ({runde}) und legst die Abgabe dort ab.

BRIEFING
  Kunde:       {brief.kunde}
  Produkt:     {brief.produkt}
  Zielgruppe:  {brief.zielgruppe}
  Ton:         {brief.ton}
  Sprache:     {brief.sprache}
  Ziele:
{ziele}
  Seiten:
{seiten}
  Einschränkungen:
{einschraenkungen}
{revision}
REGELN (ein Gate prüft sie anschließend, maschinell)
  1. Statisches HTML und CSS. Kein Framework, kein Build-Schritt, kein JavaScript nötig.
  2. Jede Seite: <title> (20–70 Zeichen), <meta name="description"> (50–165), genau ein <h1>,
     keine Sprünge in der Überschriftenhierarchie, <html lang="de">, viewport-Meta.
  3. Jedes <img> mit sinnvollem alt; Dekoration mit alt="".
  4. Keine toten internen Links: jede verlinkte Datei muss existieren.
  5. Farben als CSS-Variablen in style.css. Text auf Fläche mindestens 4.5:1 Kontrast,
     Akzentfarbe mindestens 3:1 — nachrechnen, nicht schätzen.
  6. Höchstens drei externe Abhängigkeiten, besser keine.
  7. HTML unter 140 KB, CSS unter 45 KB.
  8. Keine Platzhalter. Keine eckigen Klammern, kein „TODO", keine Musteradresse.
     Fehlt eine Angabe, formuliere ohne sie oder lass den Abschnitt weg — erfunden wird
     nichts: keine Adresse, keine Öffnungszeit, kein Preis, keine Referenz.
  9. Lege ZUERST NOTIZ.md an (fünf Zeilen: was du baust und warum) und ergänze sie am
     Ende. Ein Lauf kann gekappt werden; die Notiz gehört dann trotzdem dazu.

QUALITÄT
  Das ist eine echte Kundenarbeit, kein Demo. Eigene Typografie-Entscheidung, ein klares
  Raster, großzügiger Weißraum, echte deutsche Texte in der Stimme des Kunden — keine
  Platzhalter, kein Lorem ipsum, keine Floskeln wie „innovativ" oder „maßgeschneidert".
  Keine erfundenen Referenzen, Preise oder Auszeichnungen.

{ABSCHLUSS}
"""


def kriktikprompt(brief: brief_modul.Brief, ergebnis: qa.Ergebnis, runde: str) -> str:
    findings = "\n".join(f'  - [{f.severity}] {f.area}: {f.title} — {f.detail}'
                         for f in ergebnis.findings) or "  - keine"
    return f"""Du bist die Kritik-Rolle im Studio. Der Auftrag ist {brief.kunde}
({brief.produkt}), Zielgruppe: {brief.zielgruppe}. Die Runde {runde} wurde mechanisch
gemessen:

  Score {ergebnis.score}/100 · {ergebnis.kritisch} kritisch · {ergebnis.warnungen} Warnungen
{findings}

Lies die Dateien im aktuellen Ordner und schreibe `kritik.md`:
  1. Was trägt, in zwei Sätzen.
  2. Was dem Briefing widerspricht — mit Datei und Stelle.
  3. Was das Gate nicht sieht (Hierarchie, Rhythmus, Tonalität, Vertrauen), in drei bis
     fünf Punkten, jeder mit konkretem Vorschlag.
  4. Priorität: was zuerst geändert werden muss.

Keine Gefälligkeiten, keine Note. {ABSCHLUSS}
"""


# ---------------------------------------------------------------- Laufen

def _stream_auswerten(zeilen: list[str]) -> tuple[dict, str, int]:
    """Letztes result-Ereignis, gesammelter Text und Anzahl der Züge."""
    ergebnis: dict = {}
    texte: list[str] = []
    zuege = 0
    for zeile in zeilen:
        zeile = zeile.strip()
        if not zeile.startswith("{"):
            continue
        try:
            ereignis = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        art = ereignis.get("type")
        if art == "result":
            ergebnis = ereignis
        elif art == "text" and ereignis.get("text"):
            texte.append(ereignis["text"])
        elif art in ("tool_use", "assistant", "tool"):
            zuege += 1
    return ergebnis, "\n".join(texte).strip(), zuege


def lauf(prompt: str, cwd: Path, rolle: str, runde: str,
         modell: str | None = None, max_turns: int = MAX_TURNS,
         budget: int = BUDGET_SEKUNDEN) -> AgentRun:
    """Einen Hermes-Agenten einmal laufen lassen — mit Grenzen und Protokoll."""
    if not verfuegbar():
        return AgentRun(rolle=rolle, runde=runde, ok=False,
                        fehler="hermes nicht im PATH")

    cwd.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     dir=str(cwd)) as datei:
        datei.write(prompt)
        prompt_datei = Path(datei.name)

    # `--run-budget` kennt die CLI nicht (nur config.yaml), also kappt die Zeit das
    # subprocess-Timeout; die Züge kappt `--max-turns`.
    argv = ["hermes", "chat", "--query-file", str(prompt_datei), "--oneshot", "-Q",
            "--format", "stream-json", "--in", str(cwd), "--max-turns", str(max_turns)]
    if modell:
        argv += ["-m", modell]

    start = time.monotonic()
    try:
        fertig = subprocess.run(argv, capture_output=True, text=True, timeout=budget)
        dauer = int((time.monotonic() - start) * 1000)
        ergebnis, text, zuege = _stream_auswerten(fertig.stdout.splitlines())
        tokens = ergebnis.get("tokens") or {}
        return AgentRun(
            rolle=rolle, runde=runde, ok=fertig.returncode == 0,
            dauer_ms=ergebnis.get("duration_ms", dauer),
            tokens_in=int(tokens.get("input", 0) or 0),
            tokens_out=int(tokens.get("output", 0) or 0),
            zuege=zuege, exit_code=fertig.returncode, text=text,
            fehler="" if fertig.returncode == 0 else (fertig.stderr or "")[-400:])
    except subprocess.TimeoutExpired:
        return AgentRun(rolle=rolle, runde=runde, ok=False,
                        dauer_ms=int((time.monotonic() - start) * 1000),
                        fehler=f"Zeitbudget von {budget}s überschritten")
    finally:
        prompt_datei.unlink(missing_ok=True)


# ---------------------------------------------------------------- Rollen

VORBAU = ("strategie", "art", "text")


def rolle_ausfuehren(brief: brief_modul.Brief, rolle: str, runde: str,
                     modell: str | None = None, max_turns: int = 14,
                     budget: int = 420) -> AgentRun:
    pfad = brief_modul.runde_pfad(brief, runde)
    pfad.mkdir(parents=True, exist_ok=True)
    return lauf(rollenprompt(brief, rolle), pfad, rolle, runde, modell, max_turns, budget)


def rollen_parallel(brief: brief_modul.Brief, runde: str, modell: str | None = None,
                    budget: int = 420) -> list[AgentRun]:
    """Strategie zuerst, dann Art Direction und Text gleichzeitig.

    Die beiden hängen nur von `konzept.md` ab, nicht voneinander — sie können also
    parallel laufen. Ein echter Auftrag braucht die Struktur vor der Gestaltung; alles
    gleichzeitig laufen zu lassen hieße, dass der Text die Struktur raten muss.
    """
    from concurrent.futures import ThreadPoolExecutor

    laeufe: list[AgentRun] = []
    strategie = rolle_ausfuehren(brief, "strategie", runde, modell, budget=budget)
    laeufe.append(strategie)
    if not strategie.ok and not (brief_modul.runde_pfad(brief, runde) / "konzept.md").exists():
        return laeufe

    with ThreadPoolExecutor(max_workers=2) as pool:
        aufgaben = {rolle: pool.submit(rolle_ausfuehren, brief, rolle, runde, modell,
                                       budget) for rolle in ("art", "text")}
        for rolle in ("art", "text"):
            laeufe.append(aufgaben[rolle].result())
    return laeufe


def bauprompt_nach_rollen(brief: brief_modul.Brief, runde: str) -> str:
    return f"""Du bist die Bau-Rolle eines Designstudios. Auftrag:

{_briefkopf(brief)}

Im Ordner liegen bereits die Ergebnisse der anderen Rollen:
  - `konzept.md` — Struktur und Tonalität (verbindlich)
  - `design/tokens.css` — die Farben, Schriftgrößen und Abstände (verbindlich, nicht neu erfinden)
  - `design/artdirection.md` — Schrift, Raster, drei Regeln
  - `inhalte.md` — die Texte (Wortlaut übernehmen, nicht umschreiben)

Baue daraus die Abgabe: die Seiten aus dem Briefing, `style.css`, und verweise auf
`design/tokens.css`. Alle Regeln des Gates:

{GATE_REGELN}
  8. Keine Platzhalter. Keine eckigen Klammern, kein „TODO", keine Musteradresse.
     Fehlt eine Angabe, formuliere ohne sie oder lass den Abschnitt weg — erfunden wird
     nichts: keine Adresse, keine Öffnungszeit, kein Preis, keine Referenz.
  9. Lege ZUERST NOTIZ.md an (fünf Zeilen) und ergänze sie am Ende.

Nimm die Tokens wörtlich. Wenn dir eine Farbe fehlt, ergänze sie in `style.css`, aber
ändere keine vorhandene. {ABSCHLUSS}
"""


# ---------------------------------------------------------------- Runden

def baue(brief: brief_modul.Brief, runde: str, modell: str | None = None,
         max_turns: int = MAX_TURNS, budget: int = BUDGET_SEKUNDEN,
         findings: list[dict] | None = None) -> AgentRun:
    """Eine Runde vom Agenten bauen lassen (v1 oder Revision)."""
    pfad = brief_modul.runde_pfad(brief, runde)
    if pfad.exists() and any(pfad.iterdir()) and not findings:
        raise RuntimeError(f"{runde} existiert bereits und ist nicht leer")
    pfad.mkdir(parents=True, exist_ok=True)
    prompt = bauprompt(brief, runde, findings)
    run = lauf(prompt, pfad, "bau", runde, modell, max_turns, budget)
    if not run.ok and not any(pfad.iterdir()):
        pfad.rmdir()          # ein gescheiterter Lauf hinterlässt keine leere Runde
    return run


def kritik(brief: brief_modul.Brief, ergebnis: qa.Ergebnis,
           modell: str | None = None) -> AgentRun:
    pfad = brief_modul.runde_pfad(brief, ergebnis.runde)
    return lauf(kriktikprompt(brief, ergebnis, ergebnis.runde), pfad, "kritik",
                ergebnis.runde, modell, max_turns=18, budget=600)


def automatik(brief: brief_modul.Brief, max_runden: int = config.MIN_ROUNDS + 1,
              modell: str | None = None, budget: int = BUDGET_SEKUNDEN,
              protokoll=None, rollen: bool = True) -> list[AgentRun]:
    """Bauen, messen, revidieren — bis das Gate grün ist oder die Runden ausgehen.

    `protokoll(run, ergebnis)` wird nach jedem Schritt aufgerufen; CLI und Web hängen daran
    ihre Ausgabe. Der Motor selbst druckt nichts.
    """
    from . import engine  # spät, damit agents nicht von engine abhängt

    laeufe: list[AgentRun] = []
    # Rollen nur beim ersten Aufbau eines Auftrags — danach ist jede Runde eine Revision.
    frisch = not brief_modul.runden(brief)
    runde = brief_modul.naechste_runde(brief)
    ergebnis: qa.Ergebnis | None = None

    for nummer in range(max_runden):
        if rollen and nummer == 0 and frisch:
            # Erste Runde: Rolls, dann Bau — Revisionen laufen als ein Durchgang.
            for teil in rollen_parallel(brief, runde, modell, budget):
                laeufe.append(teil)
                if protokoll:
                    protokoll(teil, None)
            run = lauf(bauprompt_nach_rollen(brief, runde),
                       brief_modul.runde_pfad(brief, runde), "bau", runde, modell,
                       MAX_TURNS, budget)
        else:
            run = baue(brief, runde, modell, budget=budget,
                       findings=[f.as_dict() for f in ergebnis.findings] if ergebnis else None)
        laeufe.append(run)
        pfad = brief_modul.runde_pfad(brief, runde)
        if not run.ok and not (pfad.exists() and any(pfad.iterdir())):
            if protokoll:
                protokoll(run, None)
            break

        ergebnis = engine.messen(brief, runde)
        if protokoll:
            protokoll(run, ergebnis)
        if ergebnis.bestanden and len(brief_modul.runden(brief)) >= config.MIN_ROUNDS:
            break
        if nummer + 1 >= max_runden:
            break
        runde = brief_modul.naechste_runde(brief)

    return laeufe
