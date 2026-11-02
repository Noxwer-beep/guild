# Guild

Eine Design- und Entwicklungsfirma aus Rollen, Runden und einem Gate.

Ein Auftrag kommt als Briefing herein. Dann wird gebaut, kritisiert, revidiert — und am Ende
steht ein Gate, das die Abgabe aufhält, bis sie sauber ist. Nicht „sieht gut aus", sondern
nachprüfbar: Titel, Sprache, Beschreibung, Überschriftenordnung, Alt-Texte, interne Links,
Kontrast nach WCAG, Viewport, Gewicht.

```
Briefing → v1 bauen → messen → Kritik → v2 bauen → messen → Abgabe
```

![Studio: ein Auftrag, zwei Runden, grünes Gate](docs/img/studio.png)

## Warum

Eine Seite in einem Durchgang wirkt selten fertig. Was fehlt, ist nicht Talent, sondern der
zweite Blick und eine Schwelle, ab der man aufhört. Beides ist hier fest verdrahtet: keine
Abgabe ohne mindestens eine Revision, und keine grüne Abgabe mit einem kritischen Finding.

## Installation

```bash
./guild                    # läuft aus dem Checkout, rich genügt
pip install -e ".[ui]"     # als Kommando auf dem PATH, mit Browser-Oberfläche
```

## Befehle

```
guild                       Übersicht aller Aufträge
guild new <slug> …          Briefing anlegen
guild run <slug> [runde]    Runde durch das Gate schicken
guild show <slug>           Stand, Findings, Verlauf
guild compare <slug> v1 v2  zwei Runden vergleichen
guild ship <slug>           abgeben (nur bei grünem Gate)
guild plan <slug>           was welche Rolle liefert
guild web                   Studio im Browser (127.0.0.1:8800)
```

Exit-Codes: `0` ok · `1` Fehler · `2` Aufruf- oder Briefingfehler · `3` Gate rot.

## Ein Auftrag, von Anfang bis Abgabe

```
$ ./guild new feld-feder --kunde "Feld & Feder" --produkt "Atelier für Buchbinderei" \
    --zielgruppe "Hochzeitspaare und kleine Marken" \
    --ziel "Anfrage stellen" --seite index.html --seite kontakt.html
✓ Briefing angelegt: ~/clients/feld-feder/briefing.toml

$ ./guild run feld-feder v1
69/100 · 2 kritisch · 2 Warnungen
  critical  Kontrast   --muted auf --bg nur 2.78:1        style.css
  warn      Struktur   keine Meta-Beschreibung            index.html

$ ./guild run feld-feder v2
98/100 · 0 kritisch · 0 Warnungen

$ ./guild ship feld-feder
✓ abgegeben: ~/clients/feld-feder/abgabe
```

`guild compare feld-feder v1 v2` zeigt dann, welche Findings verschwunden und welche
dazugekommen sind, und welche Dateien sich geändert haben.

## Das Gate

Was gemessen wird, und warum es kritisch ist:

| Prüfung | Schwere | Grund |
|---|---|---|
| `<title>` vorhanden | kritisch | ohne Titel ist der Tab leer und der Verlauf wertlos |
| `<html lang>` | kritisch | Screenreader raten sonst die Aussprache |
| `alt` an jedem Bild | kritisch | ein Bild ohne Text existiert für einen Teil der Leser nicht |
| interne Links | kritisch | ein toter Link ist ein Fehler, kein Detail |
| Kontrast ≥ 4.5:1 (3:1 für Akzente) | kritisch | WCAG AA, nachgerechnet aus den Tokens |
| viewport | kritisch | ohne ihn rendert das Handy auf 980 px |
| genau ein `h1`, keine Sprünge | kritisch / Warnung | Hierarchie ist Navigation |
| Meta-Beschreibung | Warnung | Vorschauen und Suche raten sonst |
| ≤ 3 externe Abhängigkeiten | Warnung | jede ist ein Fremdausfall |
| Größenbudget (HTML 140 KB, CSS 45 KB) | Warnung | Gewicht ist eine Designentscheidung |
| Notiz zur Runde | Hinweis | warum so und nicht anders |

Der Score ist `100 − Summe der Gewichte` (kritisch 12, Warnung 3, Hinweis 1). Abgabe nur bei
0 kritischen Findings **und** mindestens zwei Runden — eine Zahl ohne Revision ist keine
Arbeit, sondern ein Entwurf.

## Rollen

`guild plan <slug>` druckt die Checkliste:

| Rolle | Auftrag | Ergebnis |
|---|---|---|
| Strategie | Briefing lesen, Ziel schärfen, Seitenstruktur | `konzept.md` |
| Art Direction | Typografie, Farbsystem, Raster, Tokens | `design/tokens.css` |
| Text | Überschriften und Texte in der Stimme des Kunden | `inhalte.md` |
| Bau | HTML und CSS, semantisch, ohne Framework | `seite/` |
| Kritik | gegen Gate und Briefing lesen | `kritik.json` |
| Revision | Findings abarbeiten, jede Änderung begründen | `runde/` |
| Qualitätssicherung | messen | `qa.json` |

Die Rollen sind keine Sprachmodell-Aufrufe im Code — das Gate ist deterministisch, die
kreative Arbeit macht, wer sie macht: ein Agent oder ein Mensch. Guild hält den Prozess
zusammen, nicht die Ideen.

## Wo die Arbeit liegt

```
~/clients/<slug>/
  briefing.toml        was gebaut werden soll
  runden/v1/ …         jede Runde vollständig, nie überschrieben
  kritik/              Findings je Runde
  abgabe/              die freigegebene Fassung + ABGABE.md
~/.local/share/guild/guild.db    Messungen und Ereignisse
```

Kundendaten liegen außerhalb des Repos. Im Repo steht nur das Werkzeug.

## Grenzen

- Das Gate misst Struktur, Zugänglichkeit, Kontrast und Gewicht. Es misst **nicht**, ob die
  Gestaltung gut ist — dafür gibt es die Kritik-Rolle und den Menschen, der abnimmt.
- Kontrast wird aus den CSS-Tokens gerechnet, nicht aus gerendertem Text. Verläufe,
  Bilder als Hintergrund und `opacity` fallen durch dieses Netz.
- Nur statische Seiten. Wer ein Build-Werkzeug braucht, braucht ein anderes Werkzeug.
- Das Web-Fenster bindet auf Loopback, ohne Anmeldung. Es ist kein Dienst.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

20 Tests: Briefing-Roundtrip, jede Gate-Prüfung mit einer Runde, die sie auslösen muss,
Ablauf mit Revision und verweigerter Abgabe, jedes CLI-Unterkommando einmal echt.

## Lizenz

MIT.
