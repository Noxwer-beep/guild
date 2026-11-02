"""Pfade, Rollen und die Schwellen des Gates.

Alles, was von der Umgebung abhängt, steht hier und nirgends sonst: ein Test darf nie
davon ausgehen, dass im Home-Verzeichnis ein bestimmter Ordner liegt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CLIENTS = Path(os.environ.get("GUILD_CLIENTS", str(Path.home() / "clients"))).expanduser()
STATE = Path(os.environ.get("GUILD_STATE", str(Path.home() / ".local" / "share" / "guild")))
DB = STATE / "guild.db"

#: Ab dieser Runde ist eine Abgabe möglich (mindestens zwei Durchläufe: Bau und Revision).
MIN_ROUNDS = 2

#: Größenbudget in Byte. Eine Seite, die 300 KB HTML hat, ist keine Seite mehr.
MAX_HTML = 140_000
MAX_CSS = 45_000
MAX_JS = 60_000

#: Kontrast nach WCAG: AA für Text, AA für große Schrift.
CONTRAST_TEXT = 4.5
CONTRAST_LARGE = 3.0

#: Höchstens so viele externe Abhängigkeiten (Schriften, Skripte, CDNs).
MAX_EXTERNAL = 3

#: Ein einzelnes Bild darf so viel wiegen, bevor es die Seite ausbremst.
MAX_BILD = 400_000


@dataclass(frozen=True)
class Rolle:
    key: str
    name: str
    aufgabe: str
    ergebnis: str


#: Die Firma. Reihenfolge = Reihenfolge der Arbeit.
ROLLEN = (
    Rolle("stratege", "Strategie",
          "Briefing lesen, Ziel und Zielgruppe schärfen, Seitenstruktur und Erfolgskriterien",
          "konzept.md"),
    Rolle("art", "Art Direction",
          "Typografie, Farbsystem, Raster, Abstände, Komponenten — als Tokens",
          "design/tokens.css"),
    Rolle("text", "Text",
          "Überschriften und Texte in der Stimme des Kunden, ohne Floskeln",
          "inhalte.md"),
    Rolle("bau", "Bau",
          "HTML und CSS umsetzen, semantisch, ohne Framework, ohne Build-Schritt",
          "seite/"),
    Rolle("kritik", "Kritik",
          "Gegen das Gate und gegen das Briefing lesen, Findings mit Schweregrad",
          "kritik.json"),
    Rolle("revision", "Revision",
          "Findings abarbeiten, nicht kaschieren; jede Änderung begründen",
          "runde/"),
    Rolle("qa", "Qualitätssicherung",
          "Titel, Sprache, Struktur, Alt-Texte, Links, Kontrast, Budget messen",
          "qa.json"),
)

ROLLEN_NACH_KEY = {rolle.key: rolle for rolle in ROLLEN}

#: So heißt die Abgabe im Kundenordner.
ABGABE = "abgabe"
