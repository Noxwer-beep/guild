"""Eine Seite, die das Gate bestehen kann — und Bausteine, um sie zu brechen."""
from __future__ import annotations

from pathlib import Path

TOKENS = """
:root {
  --bg: #0c1014;
  --fg: #e9eef2;
  --muted: #94a4b1;
  --accent: #c9f24d;
  --surface: #121a21;
}
body { background: var(--bg); color: var(--fg); }
"""

SEITE = """<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>{titel}</title>
  <meta name="description" content="{beschreibung}">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Eine Überschrift, die trägt</h1>
  <p>Text mit einem <a href="kontakt.html">Link</a> und einem Bild.</p>
  <img src="bild.jpg" alt="{alt}">
  <h2>Zweite Ebene</h2>
  <p>Noch ein Absatz.</p>
</body>
</html>
"""

STANDARD = dict(
    titel="Nordlicht Coaching — Laufen mit Plan",
    beschreibung="Trainingspläne, Lauftechnik und Begleitung für Einsteiger in Hamburg. "
                 "Erstgespräch kostenlos, Termine online.",
    alt="Läuferin auf einer Straße im Morgenlicht",
)


def baue(wurzel: Path, **abweichungen) -> Path:
    """Eine vollständige, regelkonforme Runde anlegen."""
    werte = {**STANDARD, **abweichungen}
    wurzel.mkdir(parents=True, exist_ok=True)
    (wurzel / "index.html").write_text(SEITE.format(**werte))
    (wurzel / "kontakt.html").write_text(
        SEITE.format(**{**werte, "titel": "Kontakt — Nordlicht Coaching"})
        .replace('href="kontakt.html"', 'href="index.html"'))
    (wurzel / "style.css").write_text(TOKENS)
    (wurzel / "notiz.md").write_text("# Notiz\n\nAufbau und Entscheidungen.\n")
    return wurzel
