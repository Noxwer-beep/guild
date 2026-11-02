"""Die Sicht auf das Terminal. Kein Enginelogik hier."""

from __future__ import annotations

import os
import sys

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from . import __version__, config
from .qa import KRITISCH, WARNUNG

_WIDTH = None if sys.stdout.isatty() else int(os.environ.get("COLUMNS", "104"))
console = Console(width=_WIDTH)
err_console = Console(stderr=True, width=_WIDTH)

MARKE = (
    "  /\\_/\\   GUILD",
    " ( o.o )  a firm of agents",
    "  > ^ <   briefing · revisions · gate",
)

SEVERITY_STIL = {KRITISCH: "bold red", WARNUNG: "yellow", "info": "dim"}


def kopf(untertitel: str = "") -> None:
    for i, zeile in enumerate(MARKE):
        stil = "bold #c9f24d" if i == 0 else "dim"
        console.print(Text(zeile, style=stil))
    console.print(Text(f"  {__version__}", style="dim"))
    if untertitel:
        console.print()
        console.print(Text(untertitel, style="bold"))
    console.print()


def head(titel: str, unter: str = "") -> None:
    console.print(Text(titel.upper(), style="bold"))
    if unter:
        console.print(Text(unter, style="dim"))
    console.print()


def ok(text: str) -> None:
    console.print(Text(f"✓ {text}", style="#c9f24d"))


def warn(text: str) -> None:
    err_console.print(Text(f"! {text}", style="yellow"))


def fail(text: str) -> None:
    err_console.print(Text(f"✗ {text}", style="red"))


def hint(text: str) -> None:
    console.print(Text(f"  {text}", style="dim"))


def tabelle(kopfzeilen: list[str], breiten: list[int], zeilen: list[list[str]]) -> None:
    """Feste Breiten, kein Umbruch — eine Zeile ist ein Datensatz."""
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, expand=False)
    for titel, breite in zip(kopfzeilen, breiten):
        table.add_column(titel, width=breite, no_wrap=True, overflow="ellipsis")
    for zeile in zeilen:
        table.add_row(*zeile)
    console.print(table)


def jobs(zeilen: list[dict]) -> None:
    if not zeilen:
        hint("noch kein Briefing — `guild new <slug>` legt eins an")
        return
    tabelle(["SLUG", "KUNDE", "RUNDEN", "GATE", "STAND"], [16, 22, 7, 8, 30],
            [[z["slug"], z["kunde"][:22], str(z["runden"]), z["gate"], z["stand"]]
             for z in zeilen])


def runden(zeilen: list[dict]) -> None:
    if not zeilen:
        hint("keine gemessene Runde")
        return
    tabelle(["RUNDE", "SCORE", "KRITISCH", "WARNUNGEN", "DATEIEN", "GEMESSEN"],
            [7, 6, 9, 11, 8, 20],
            [[z["runde"], str(z["score"]), str(z["kritisch"]), str(z["warnungen"]),
              str(z["dateien"]), z["gemessen"][:19].replace("T", " ")] for z in zeilen])


def findings(zeilen: list[dict], limit: int = 18) -> None:
    if not zeilen:
        ok("keine offenen Findings")
        return
    for finding in zeilen[:limit]:
        stil = SEVERITY_STIL.get(finding["severity"], "dim")
        console.print(Text(f'  {finding["severity"]:<8}', style=stil), end="")
        console.print(Text(f'{finding["area"]:<14}', style="dim"), end="")
        console.print(Text(f'{finding["title"]}', style="bold"), end="")
        console.print(Text(f'  {finding["file"]}', style="dim"))
        console.print(Text(f'           {finding["detail"]}', style="dim"))
    if len(zeilen) > limit:
        hint(f"… {len(zeilen) - limit} weitere — `guild show <slug> --all`")


def plan(zeilen: list[dict]) -> None:
    tabelle(["ROLLE", "AUFGABE", "LIEFERT"], [13, 58, 20],
            [[z["rolle"], z["aufgabe"][:58], z["ergebnis"]] for z in zeilen])


def rollen_nach_severity(findings_liste: list[dict]) -> Table:
    table = Table(box=box.SIMPLE, show_edge=False)
    table.add_column("SCHWERE", width=9)
    table.add_column("ANZAHL", width=7, justify="right")
    for schwere in (KRITISCH, WARNUNG, "info"):
        anzahl = sum(1 for f in findings_liste if f["severity"] == schwere)
        table.add_row(schwere, str(anzahl))
    return table


def pfad(p: str) -> None:
    console.print(Text(f"  {p}", style="dim"))
