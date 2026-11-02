"""Briefing: lesen, prüfen, anlegen.

Ein Briefing ist eine Datei, kein Gesprächsverlauf. Was nicht im Briefing steht, wird
auch nicht gebaut — sonst kann niemand sagen, ob die Abgabe das Richtige war.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")

PFLICHT = ("kunde", "produkt", "zielgruppe", "ziele", "seiten")


class BriefError(ValueError):
    """Das Briefing ist unvollständig oder widersprüchlich."""


@dataclass
class Brief:
    slug: str
    kunde: str
    produkt: str
    zielgruppe: str
    ziele: list[str] = field(default_factory=list)
    seiten: list[str] = field(default_factory=list)
    ton: str = "sachlich, klar, ohne Werbesprache"
    sprache: str = "de"
    einschraenkungen: list[str] = field(default_factory=list)
    referenzen: list[str] = field(default_factory=list)
    abgabe: str = "statische Seite, kein Framework, kein Build-Schritt"

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def ordner(self) -> Path:
        return config.CLIENTS / self.slug


def _sauber(text: str) -> str:
    """Für TOML: Anführungszeichen und Steuerzeichen raus."""
    return str(text).replace('"', "'").replace("\n", " ").strip()


def to_toml(brief: Brief) -> str:
    def liste(name: str, werte: list[str]) -> str:
        if not werte:
            return f"{name} = []"
        innen = ", ".join(f'"{_sauber(w)}"' for w in werte)
        return f"{name} = [{innen}]"

    zeilen = [
        f"# Briefing {brief.slug}",
        f'slug = "{brief.slug}"',
        f'kunde = "{_sauber(brief.kunde)}"',
        f'produkt = "{_sauber(brief.produkt)}"',
        f'zielgruppe = "{_sauber(brief.zielgruppe)}"',
        liste("ziele", brief.ziele),
        liste("seiten", brief.seiten),
        f'ton = "{_sauber(brief.ton)}"',
        f'sprache = "{brief.sprache}"',
        liste("einschraenkungen", brief.einschraenkungen),
        liste("referenzen", brief.referenzen),
        f'abgabe = "{_sauber(brief.abgabe)}"',
        "",
    ]
    return "\n".join(zeilen)


def validate(brief: Brief) -> list[str]:
    """Alles, was ein Briefing unbrauchbar macht, gesammelt zurückgeben."""
    probleme: list[str] = []
    if not SLUG_RE.match(brief.slug):
        probleme.append("slug: kleinbuchstaben, ziffern, bindestrich (2–41 Zeichen)")
    for feld in PFLICHT:
        wert = getattr(brief, feld)
        if not wert:
            probleme.append(f"{feld}: fehlt")
    if len(brief.ziele) < 1:
        probleme.append("ziele: mindestens ein Ziel")
    if len(brief.seiten) < 1:
        probleme.append("seiten: mindestens eine Seite")
    if brief.sprache not in ("de", "en"):
        probleme.append(f"sprache: {brief.sprache!r} — erlaubt sind de, en")
    return probleme


def load(pfad: Path | str) -> Brief:
    """Briefing aus einer Datei oder aus dem Kundenordner lesen."""
    pfad = Path(pfad)
    datei = pfad / "briefing.toml" if pfad.is_dir() else pfad
    if not datei.exists():
        raise BriefError(f"kein Briefing: {datei}")
    try:
        daten = tomllib.loads(datei.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise BriefError(f"{datei.name}: {exc}") from exc

    daten.setdefault("slug", datei.parent.name)
    unbekannt = set(daten) - set(Brief.__dataclass_fields__)
    if unbekannt:
        raise BriefError(f"{datei.name}: unbekannte Felder {sorted(unbekannt)}")

    brief = Brief(**daten)
    probleme = validate(brief)
    if probleme:
        raise BriefError(f"{datei.name}: " + "; ".join(probleme))
    return brief


def load_slug(slug: str) -> Brief:
    return load(config.CLIENTS / slug / "briefing.toml")


def alle() -> list[Brief]:
    """Alle Briefings unter dem Kundenordner, alphabetisch."""
    briefings: list[Brief] = []
    if not config.CLIENTS.exists():
        return briefings
    for ordner in sorted(config.CLIENTS.iterdir()):
        if not ordner.is_dir():
            continue
        try:
            briefings.append(load(ordner))
        except BriefError:
            continue
    return briefings


def neu(slug: str, kunde: str, produkt: str, zielgruppe: str,
        ziele: list[str], seiten: list[str], **rest) -> tuple[Brief, Path]:
    """Kundenordner mit Briefing, Runden- und Kritikordner anlegen."""
    if not SLUG_RE.match(slug):
        raise BriefError("slug: kleinbuchstaben, ziffern, bindestrich (2–41 Zeichen)")
    ordner = config.CLIENTS / slug
    if (ordner / "briefing.toml").exists():
        raise BriefError(f"{slug}: Briefing existiert bereits")

    brief = Brief(slug=slug, kunde=kunde, produkt=produkt, zielgruppe=zielgruppe,
                  ziele=list(ziele), seiten=list(seiten), **rest)
    probleme = validate(brief)
    if probleme:
        raise BriefError("; ".join(probleme))

    for unter in ("runden", "kritik"):
        (ordner / unter).mkdir(parents=True, exist_ok=True)
    datei = ordner / "briefing.toml"
    datei.write_text(to_toml(brief))
    return brief, datei


def runden(brief: Brief) -> list[str]:
    """Vorhandene Runden, sortiert: v1, v2, … v10."""
    ordner = brief.ordner / "runden"
    if not ordner.exists():
        return []

    def nummer(name: str) -> int:
        return int(name[1:]) if name[1:].isdigit() else 0

    return sorted((p.name for p in ordner.iterdir() if p.is_dir() and p.name.startswith("v")),
                  key=nummer)


def naechste_runde(brief: Brief) -> str:
    """Die nächste Rundennummer nach der höchsten vorhandenen — nicht nach der Anzahl.

    Nach dem Löschen einer Runde darf keine Nummer doppelt vergeben werden.
    """
    vorhandene = runden(brief)
    hoechste = max((int(name[1:]) for name in vorhandene if name[1:].isdigit()), default=0)
    return f"v{hoechste + 1}"


def runde_pfad(brief: Brief, runde: str) -> Path:
    return brief.ordner / "runden" / runde
