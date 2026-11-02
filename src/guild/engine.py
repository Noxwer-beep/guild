"""Der Ablauf der Firma: messen, vergleichen, abgeben.

Der Motor kennt kein Terminal und kein HTML — er liest Runden von der Platte, prüft sie mit
dem Gate, schreibt das Ergebnis in den Store und gibt ein Ergebnisobjekt zurück. CLI und
Web sind nur zwei Sichten darauf.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import brief as brief_modul
from . import config, qa
from .store import Store


@dataclass
class Stand:
    brief: brief_modul.Brief
    runden: list[str]
    letzte: str | None
    ergebnis: qa.Ergebnis | None
    offen: list[dict]
    bereit: bool
    grund: str


def _hash(pfad: Path) -> str:
    return hashlib.sha1(pfad.read_bytes()).hexdigest()[:12]


def messen(brief: brief_modul.Brief, runde: str | None = None,
           store: Store | None = None, speichern: bool = True) -> qa.Ergebnis:
    """Eine Runde durch das Gate schicken."""
    store = store or Store()
    runde = runde or (brief_modul.runden(brief) or ["v1"])[-1]
    ergebnis = qa.pruefe_runde(brief_modul.runde_pfad(brief, runde), runde)
    if speichern:
        store.save_qa(brief.slug, ergebnis)
        level = "ok" if ergebnis.bestanden else "warn"
        store.log(brief.slug, level,
                  f"{runde}: {ergebnis.score}/100 · {ergebnis.kritisch} kritisch · "
                  f"{ergebnis.warnungen} Warnungen")
    return ergebnis


def stand(brief: brief_modul.Brief, store: Store | None = None) -> Stand:
    """Wo das Projekt steht und ob es abgegeben werden darf."""
    store = store or Store()
    vorhanden = brief_modul.runden(brief)
    letzte = vorhanden[-1] if vorhanden else None
    ergebnis = None
    if letzte:
        gespeichert = store.round(brief.slug, letzte)
        ergebnis = qa.pruefe_runde(brief_modul.runde_pfad(brief, letzte), letzte)
        if gespeichert:
            # gespeicherte Messung ist die Wahrheit für den Verlauf, nicht die Neuberechnung
            ergebnis.findings = [qa.Finding(**f) for f in
                                 __import__("json").loads(gespeichert["qa"])["findings"]]

    if not vorhanden:
        bereit, grund = False, "noch keine Runde gebaut"
    elif len(vorhanden) < config.MIN_ROUNDS:
        bereit, grund = False, f"erst {len(vorhanden)} Runde(n) — mindestens " \
                               f"{config.MIN_ROUNDS} mit Revision"
    elif ergebnis is None or not ergebnis.bestanden:
        bereit, grund = False, "das Gate ist rot"
    else:
        bereit, grund = True, "Gate grün, Revision vorhanden"

    offen = [f.as_dict() for f in ergebnis.findings] if ergebnis else []
    return Stand(brief, vorhanden, letzte, ergebnis, offen, bereit, grund)


def dateien(brief: brief_modul.Brief, runde: str) -> dict[str, str]:
    wurzel = brief_modul.runde_pfad(brief, runde)
    if not wurzel.exists():
        return {}
    return {str(p.relative_to(wurzel)): _hash(p)
            for p in sorted(wurzel.rglob("*")) if p.is_file()}


def vergleiche(brief: brief_modul.Brief, a: str, b: str, store: Store | None = None) -> dict:
    """Was sich zwischen zwei Runden geändert hat — Dateien und Findings."""
    store = store or Store()
    neu, alt = dateien(brief, b), dateien(brief, a)
    hinzugekommen = sorted(set(neu) - set(alt))
    entfernt = sorted(set(alt) - set(neu))
    geaendert = sorted(k for k in set(neu) & set(alt) if neu[k] != alt[k])

    ergebnis_neu = messen(brief, b, store, speichern=False)
    ergebnis_alt = messen(brief, a, store, speichern=False)

    def nach_key(erg: qa.Ergebnis) -> dict[str, qa.Finding]:
        return {f.key: f for f in erg.findings}

    alt_f, neu_f = nach_key(ergebnis_alt), nach_key(ergebnis_neu)
    return {
        "von": a, "nach": b,
        "dateien": {"hinzu": hinzugekommen, "weg": entfernt, "geaendert": geaendert},
        "score": {"von": ergebnis_alt.score, "nach": ergebnis_neu.score},
        "kritisch": {"von": ergebnis_alt.kritisch, "nach": ergebnis_neu.kritisch},
        "behoben": [alt_f[k].as_dict() for k in sorted(set(alt_f) - set(neu_f))],
        "neu": [neu_f[k].as_dict() for k in sorted(set(neu_f) - set(alt_f))],
        "gleich": sorted(set(alt_f) & set(neu_f)),
    }


def abgeben(brief: brief_modul.Brief, store: Store | None = None) -> Path:
    """Die letzte Runde freigeben — nur bei grünem Gate und vorhandener Revision."""
    store = store or Store()
    zustand = stand(brief, store)
    if not zustand.bereit:
        raise RuntimeError(f"keine Abgabe: {zustand.grund}")

    quelle = brief_modul.runde_pfad(brief, zustand.letzte or "v1")
    ziel = brief.ordner / config.ABGABE
    if ziel.exists():
        shutil.rmtree(ziel)
    shutil.copytree(quelle, ziel)
    vermerk = ziel / "ABGABE.md"
    ergebnis = zustand.ergebnis
    vermerk.write_text(
        f"# Abgabe — {brief.kunde}\n\n"
        f"- Stand: {zustand.letzte}\n"
        f"- Gate: {ergebnis.score}/100 · {ergebnis.kritisch} kritisch · "
        f"{ergebnis.warnungen} Warnungen\n"
        f"- Runden: {', '.join(zustand.runden)}\n\n"
        f"{brief.produkt}\n")
    store.log(brief.slug, "ok", f"{zustand.letzte} abgegeben → {ziel}")
    return ziel


def rollenplan(brief: brief_modul.Brief) -> list[dict]:
    """Welche Rolle was in dieser Runde liefert — die Checkliste für den Bau."""
    return [
        {"rolle": r.name, "aufgabe": r.aufgabe, "ergebnis": r.ergebnis}
        for r in config.ROLLEN
    ]
