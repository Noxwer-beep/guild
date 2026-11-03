"""Das Gate: was eine Runde messbar gut oder schlecht macht.

Keine Geschmacksurteile — alles hier ist nachprüfbar: Titel, Sprache, Beschreibung,
Überschriftenordnung, Alt-Texte, interne Links, Kontrast nach WCAG, Viewport, Größen,
externe Abhängigkeiten. Was kritisch ist, hält die Abgabe auf.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import config

KRITISCH, WARNUNG, INFO = "critical", "warn", "info"
GEWICHT = {KRITISCH: 12, WARNUNG: 3, INFO: 1}

TITEL_MIN, TITEL_MAX = 20, 70

#: Text, der in einer Abgabe nichts zu suchen hat: ungefüllte Klammern, Lorem, Todos.
PLATZHALTER = re.compile(
    r"\[[^\]\n]{2,60}\]"          # [Straße und Hausnummer]
    r"|\{\{[^}]{1,40}\}\}"          # {{ name }}
    r"|\blorem ipsum\b"
    r"|\bTODO\b|\bTBD\b|\bXXX\b"
    r"|Musterstra(ss|ß)e",
    re.I)
BESCHREIBUNG_MIN, BESCHREIBUNG_MAX = 50, 165


@dataclass
class Finding:
    key: str
    severity: str
    area: str
    title: str
    detail: str
    file: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ergebnis:
    runde: str
    findings: list[Finding] = field(default_factory=list)
    dateien: int = 0
    bytes_html: int = 0
    bytes_css: int = 0
    bytes_js: int = 0

    @property
    def kritisch(self) -> int:
        return sum(1 for f in self.findings if f.severity == KRITISCH)

    @property
    def warnungen(self) -> int:
        return sum(1 for f in self.findings if f.severity == WARNUNG)

    @property
    def score(self) -> int:
        abzug = sum(GEWICHT.get(f.severity, 1) for f in self.findings)
        return max(0, 100 - abzug)

    @property
    def bestanden(self) -> bool:
        return self.kritisch == 0

    def as_dict(self) -> dict:
        return {
            "runde": self.runde,
            "bestanden": self.bestanden,
            "score": self.score,
            "kritisch": self.kritisch,
            "warnungen": self.warnungen,
            "findings": [f.as_dict() for f in self.findings],
            "dateien": self.dateien,
            "bytes_html": self.bytes_html,
            "bytes_css": self.bytes_css,
            "bytes_js": self.bytes_js,
        }


# ---------------------------------------------------------------- HTML lesen

class _Sammler(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titel = ""
        self._im_titel = False
        self.sprache = ""
        self.ueberschriften: list[tuple[int, str]] = []
        self._h_offen: int | None = None
        self._h_text: list[str] = []
        self.bilder: list[str | None] = []
        self.bild_quellen: list[str] = []
        self.meta: dict[str, str] = {}
        self.extern: list[str] = []
        self.styles_inline = 0
        self.links: list[str] = []
        self.skripte_href: list[str] = []
        self.text: list[str] = []
        self._stumm = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        werte = dict(attrs)
        if tag == "html":
            self.sprache = (werte.get("lang") or "").strip()
        elif tag == "title":
            self._im_titel = True
        elif tag == "meta":
            name = (werte.get("name") or "").lower()
            if name:
                self.meta[name] = (werte.get("content") or "").strip()
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._h_offen = int(tag[1])
            self._h_text = []
        elif tag == "img":
            self.bilder.append(werte.get("alt"))
            self.bild_quellen.append((werte.get("src") or "").strip())
        elif tag == "a":
            href = (werte.get("href") or "").strip()
            if href:
                self.links.append(href)
                if re.match(r"^https?://", href):
                    self.extern.append(href)
        elif tag in ("script", "style"):
            self._stumm += 1
        if tag == "script":
            src = (werte.get("src") or "").strip()
            if src:
                self.skripte_href.append(src)
                if re.match(r"^https?://", src):
                    self.extern.append(src)
        elif tag == "link":
            href = (werte.get("href") or "").strip()
            if re.match(r"^https?://", href):
                self.extern.append(href)
        if "style" in werte:
            self.styles_inline += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._stumm:
            self._stumm -= 1
        if tag == "title":
            self._im_titel = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._h_offen is not None:
            text = " ".join("".join(self._h_text).split())
            self.ueberschriften.append((self._h_offen, text))
            self._h_offen = None
            self._h_text = []

    def handle_data(self, data: str) -> None:
        if self._stumm == 0 and data.strip():
            self.text.append(data.strip())
        if self._im_titel:
            self.titel += data
        if self._h_offen is not None:
            self._h_text.append(data)


# ---------------------------------------------------------------- Kontrast

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _rgb(farbe: str) -> tuple[int, int, int] | None:
    farbe = farbe.strip()
    m = HEX_RE.match(farbe)
    if not m:
        return None
    wert = m.group(1)
    if len(wert) == 3:
        wert = "".join(c * 2 for c in wert)
    return tuple(int(wert[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _luminanz(rgb: tuple[int, int, int]) -> float:
    def kanal(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (kanal(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(vorne: str, hinten: str) -> float | None:
    """WCAG-Kontrastverhältnis zweier Hex-Farben, oder None."""
    a, b = _rgb(vorne), _rgb(hinten)
    if not a or not b:
        return None
    la, lb = _luminanz(a), _luminanz(b)
    hell, dunkel = max(la, lb), min(la, lb)
    return round((hell + 0.05) / (dunkel + 0.05), 2)


TOKEN_RE = re.compile(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", re.I)

#: Welche Token-Paare als Text auf Fläche geprüft werden.
PAARE = (
    ("fg", "bg", config.CONTRAST_TEXT),
    ("text", "bg", config.CONTRAST_TEXT),
    ("fg", "surface", config.CONTRAST_TEXT),
    ("text", "surface", config.CONTRAST_TEXT),
    ("muted", "bg", config.CONTRAST_TEXT),
    ("muted", "surface", config.CONTRAST_TEXT),
    ("accent", "bg", config.CONTRAST_LARGE),
    ("accent", "surface", config.CONTRAST_LARGE),
)


def kontraste(css: str) -> list[Finding]:
    """Alle Token-Paare prüfen, die im Stylesheet vorkommen."""
    token = {name.lower(): wert for name, wert in TOKEN_RE.findall(css)}
    if not token:
        return []
    findings: list[Finding] = []
    gesehen: set[tuple[str, str]] = set()
    for vorne, hinten, minimum in PAARE:
        if vorne not in token or hinten not in token:
            continue
        paar = (vorne, hinten)
        if paar in gesehen:
            continue
        gesehen.add(paar)
        wert = kontrast(token[vorne], token[hinten])
        if wert is None:
            continue
        if wert < minimum:
            findings.append(Finding(
                key=f"contrast:{vorne}/{hinten}", severity=KRITISCH, area="Kontrast",
                title=f"--{vorne} auf --{hinten} nur {wert}:1",
                detail=f"WCAG AA verlangt {minimum}:1 für Text. {token[vorne]} auf "
                       f"{token[hinten]} ist zu schwach.",
                file="design/tokens.css"))
        elif wert < minimum + 1.5:
            findings.append(Finding(
                key=f"contrast-knapp:{vorne}/{hinten}", severity=INFO, area="Kontrast",
                title=f"--{vorne} auf --{hinten} {wert}:1 — knapp über der Grenze",
                detail=f"AA ist erfüllt ({minimum}:1), Reserve fehlt aber für Screens "
                       f"bei Sonnenlicht.",
                file="design/tokens.css"))
    return findings


# ---------------------------------------------------------------- Prüfen

def _internes_ziel(wurzel: Path, seite: Path, href: str) -> Path | None:
    ziel = href.split("#", 1)[0].split("?", 1)[0]
    if not ziel or ziel.startswith(("mailto:", "tel:", "javascript:")):
        return None
    if re.match(r"^https?://", ziel):
        return None
    return (seite.parent / ziel).resolve()


def pruefe_seite(wurzel: Path, seite: Path) -> list[Finding]:
    rel = str(seite.relative_to(wurzel))
    text = seite.read_text(errors="replace")
    sammler = _Sammler()
    sammler.feed(text)
    findings: list[Finding] = []

    def f(key: str, severity: str, area: str, title: str, detail: str) -> None:
        findings.append(Finding(key, severity, area, title, detail, rel))

    titel = " ".join(sammler.titel.split())
    if not titel:
        f("title", KRITISCH, "Struktur", "kein <title>", "Ohne Titel ist der Tab leer.")
    elif not TITEL_MIN <= len(titel) <= TITEL_MAX:
        f("title-laenge", WARNUNG, "Struktur", f"Titel {len(titel)} Zeichen",
          f"Empfohlen {TITEL_MIN}–{TITEL_MAX}: {titel!r}")

    if not sammler.sprache:
        f("lang", KRITISCH, "Struktur", "kein lang am <html>",
          "Screenreader raten die Aussprache.")
    elif sammler.sprache.lower()[:2] != "de":
        f("lang-falsch", WARNUNG, "Struktur", f"lang={sammler.sprache}",
          "Im Briefing steht Deutsch.")

    beschreibung = sammler.meta.get("description", "")
    if not beschreibung:
        f("description", WARNUNG, "Struktur", "keine Meta-Beschreibung",
          "Suchmaschinen und Vorschauen raten sonst.")
    elif not BESCHREIBUNG_MIN <= len(beschreibung) <= BESCHREIBUNG_MAX:
        f("description-laenge", INFO, "Struktur",
          f"Beschreibung {len(beschreibung)} Zeichen",
          f"Empfohlen {BESCHREIBUNG_MIN}–{BESCHREIBUNG_MAX}.")

    if "viewport" not in sammler.meta:
        f("viewport", KRITISCH, "Struktur", "kein viewport",
          "Ohne viewport rendert das Handy auf 980 px und zoomt heraus.")

    h1 = [t for ebene, t in sammler.ueberschriften if ebene == 1]
    if len(h1) != 1:
        f("h1", KRITISCH if not h1 else WARNUNG, "Hierarchie",
          f"{len(h1)} × h1", "Genau eine Hauptüberschrift pro Seite.")
    letzte = 0
    for ebene, inhalt in sammler.ueberschriften:
        if letzte and ebene > letzte + 1:
            f(f"heading-sprung:{ebene}", WARNUNG, "Hierarchie",
              f"h{letzte} → h{ebene}", f"Ebene übersprungen bei {inhalt!r}.")
            break
        letzte = ebene

    fehlend = [i for i, alt in enumerate(sammler.bilder) if alt is None]
    if fehlend:
        f("alt", KRITISCH, "Barrierefreiheit",
          f"{len(fehlend)} Bild(er) ohne alt",
          "Jedes Bild braucht alt — bei Dekoration alt=\"\".")
    leer_aber_sinnvoll = [a for a in sammler.bilder if a == ""]
    if leer_aber_sinnvoll:
        f("alt-leer", INFO, "Barrierefreiheit",
          f'{len(leer_aber_sinnvoll)} Bild(er) mit alt=""',
          "Nur richtig, wenn das Bild wirklich nichts aussagt.")

    for href in sammler.links:
        ziel = _internes_ziel(wurzel, seite, href)
        if ziel is None:
            continue
        if not str(ziel).startswith(str(wurzel.resolve())) or not ziel.exists():
            f(f"link:{href[:40]}", KRITISCH, "Navigation", f"toter Link {href}",
              "Ziel existiert nicht in dieser Runde.")

    extern = {u for u in sammler.extern}
    if len(extern) > config.MAX_EXTERNAL:
        f("extern", WARNUNG, "Abhängigkeiten",
          f"{len(extern)} externe Abhängigkeiten",
          f"Erlaubt sind {config.MAX_EXTERNAL}; jede ist ein Fremdausfall.")

    if sammler.styles_inline > 12:
        f("inline", INFO, "Code", f"{sammler.styles_inline} style-Attribute",
          "Mehr als zwölf heißt: das Stylesheet deckt die Komponenten nicht ab.")

    for quelle in sammler.bild_quellen:
        if not quelle or quelle.startswith(("http://", "https://", "data:")):
            continue
        ziel = (seite.parent / quelle.split("?", 1)[0]).resolve()
        if not ziel.exists():
            f(f"bild-fehlt:{quelle[:30]}", KRITISCH, "Bilder", f"fehlendes Bild {quelle}",
              "Das Bild liegt nicht in dieser Runde.")
            continue
        groesse = ziel.stat().st_size
        if groesse > config.MAX_BILD:
            f(f"bild-groesse:{quelle[:30]}", WARNUNG, "Bilder",
              f"{quelle} {groesse // 1024} KB",
              f"Budget {config.MAX_BILD // 1024} KB je Bild — sonst lädt die Seite zäh.")
        if ziel.suffix.lower() in (".png", ".jpg", ".jpeg") and groesse < 6_000:
            f(f"bild-klein:{quelle[:30]}", INFO, "Bilder", f"{quelle} wirkt leer",
              "Unter 6 KB ist meist ein Platzhalter oder ein Fehlschlag.")

    sichtbar = " ".join(sammler.text)
    treffer = sorted({m.group(0) for m in PLATZHALTER.finditer(sichtbar)})
    if treffer:
        f("platzhalter", KRITISCH, "Inhalt",
          f"{len(treffer)} Platzhalter im Text",
          "Ungefüllt: " + ", ".join(treffer[:4]) +
          " — fehlende Kundendaten gehören ins Briefing (fakten), nicht in die Seite.")

    groesse = len(text.encode())
    if groesse > config.MAX_HTML:
        f("groesse", WARNUNG, "Gewicht", f"HTML {groesse // 1024} KB",
          f"Budget {config.MAX_HTML // 1024} KB.")
    return findings


def pruefe_css(wurzel: Path, datei: Path) -> list[Finding]:
    rel = str(datei.relative_to(wurzel))
    text = datei.read_text(errors="replace")
    findings = kontraste(text)
    for finding in findings:
        finding.file = rel
    groesse = len(text.encode())
    if groesse > config.MAX_CSS:
        findings.append(Finding("css-groesse", WARNUNG, "Gewicht", f"CSS {groesse // 1024} KB",
                                f"Budget {config.MAX_CSS // 1024} KB.", rel))
    if "@import" in text and re.search(r"@import\s+url\(\s*['\"]?https?:", text):
        findings.append(Finding("css-import", WARNUNG, "Abhängigkeiten",
                                "externer @import",
                                "Fonts über @import blockieren das Rendern.", rel))
    return findings


def pruefe_runde(wurzel: Path, runde: str) -> Ergebnis:
    """Alle Seiten und Stylesheets einer Runde prüfen."""
    ergebnis = Ergebnis(runde=runde)
    if not wurzel.exists():
        ergebnis.findings.append(Finding("fehlt", KRITISCH, "Runde",
                                         f"Runde {runde} fehlt", f"{wurzel} existiert nicht."))
        return ergebnis

    dateien = [p for p in wurzel.rglob("*") if p.is_file()]
    ergebnis.dateien = len(dateien)
    seiten = sorted(p for p in dateien if p.suffix.lower() in (".html", ".htm"))
    if not seiten:
        ergebnis.findings.append(Finding("keine-seite", KRITISCH, "Runde",
                                         "keine HTML-Seite",
                                         "Eine Runde ohne Seite ist keine Runde."))
        return ergebnis

    index = next((p for p in seiten if p.name == "index.html"), None)
    if index is None:
        ergebnis.findings.append(Finding("kein-index", WARNUNG, "Runde", "kein index.html",
                                         "Der Einstieg fehlt; jede Abgabe braucht einen."))

    for seite in seiten:
        ergebnis.findings.extend(pruefe_seite(wurzel, seite))
        ergebnis.bytes_html += seite.stat().st_size
    for datei in seiten:
        pass
    for datei in sorted(p for p in dateien if p.suffix.lower() == ".css"):
        ergebnis.findings.extend(pruefe_css(wurzel, datei))
        ergebnis.bytes_css += datei.stat().st_size
    for datei in sorted(p for p in dateien if p.suffix.lower() == ".js"):
        ergebnis.bytes_js += datei.stat().st_size

    if ergebnis.bytes_js > config.MAX_JS:
        ergebnis.findings.append(Finding("js-groesse", WARNUNG, "Gewicht",
                                         f"JS {ergebnis.bytes_js // 1024} KB",
                                         f"Budget {config.MAX_JS // 1024} KB.", "js"))

    if not any(p.suffix.lower() in (".md", ".txt") for p in dateien):
        ergebnis.findings.append(Finding("kein-readme", INFO, "Abgabe", "keine Notiz",
                                         "Eine kurze Notiz erklärt Aufbau und Entscheidungen."))
    return ergebnis
