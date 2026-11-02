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
  8. Lege ZUERST NOTIZ.md an (fünf Zeilen: was du baust und warum) und ergänze sie am
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
              protokoll=None) -> list[AgentRun]:
    """Bauen, messen, revidieren — bis das Gate grün ist oder die Runden ausgehen.

    `protokoll(run, ergebnis)` wird nach jedem Schritt aufgerufen; CLI und Web hängen daran
    ihre Ausgabe. Der Motor selbst druckt nichts.
    """
    from . import engine  # spät, damit agents nicht von engine abhängt

    laeufe: list[AgentRun] = []
    runde = brief_modul.naechste_runde(brief)
    ergebnis: qa.Ergebnis | None = None

    for nummer in range(max_runden):
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
