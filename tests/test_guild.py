"""Tests für Briefing, Gate, Ablauf und CLI."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import fixtures
from guild import brief as brief_modul
from guild import config, engine, qa
from guild.cli import main
from guild.store import Store


class Basis(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.wurzel = Path(self._tmp.name)
        self.kunden = self.wurzel / "clients"
        self.kunden.mkdir()
        self.patches = [
            mock.patch.object(config, "CLIENTS", self.kunden),
            mock.patch.object(config, "STATE", self.wurzel / "state"),
            mock.patch.object(config, "DB", self.wurzel / "state" / "guild.db"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def brief(self, slug: str = "nordlicht"):
        brief, _ = brief_modul.neu(
            slug, kunde="Nordlicht Coaching", produkt="Website für einen Laufcoach",
            zielgruppe="Einsteiger 30–50 in Hamburg",
            ziele=["Erstgespräch anfragen", "Trainingsplan erklären"],
            seiten=["index.html", "kontakt.html"])
        return brief


class Briefing(Basis):
    def test_roundtrip_erhaelt_die_felder(self) -> None:
        brief = self.brief()
        gelesen = brief_modul.load_slug("nordlicht")
        self.assertEqual(gelesen.kunde, brief.kunde)
        self.assertEqual(gelesen.ziele, brief.ziele)
        self.assertEqual(gelesen.seiten, brief.seiten)

    def test_fehlende_angaben_werden_abgelehnt(self) -> None:
        with self.assertRaises(brief_modul.BriefError):
            brief_modul.neu("x", kunde="", produkt="p", zielgruppe="z",
                            ziele=["a"], seiten=["index.html"])

    def test_slug_wird_geprueft(self) -> None:
        with self.assertRaises(brief_modul.BriefError):
            brief_modul.neu("Gross Und Leer", kunde="a", produkt="b", zielgruppe="c",
                            ziele=["x"], seiten=["index.html"])

    def test_unbekanntes_feld_im_briefing_faellt_auf(self) -> None:
        brief = self.brief()
        pfad = brief.ordner / "briefing.toml"
        pfad.write_text(pfad.read_text() + '\nfarbe = "rot"\n')
        with self.assertRaises(brief_modul.BriefError) as ctx:
            brief_modul.load(pfad)
        self.assertIn("unbekannte Felder", str(ctx.exception))

    def test_runden_sortieren_numerisch(self) -> None:
        brief = self.brief()
        for name in ("v2", "v10", "v1"):
            (brief.ordner / "runden" / name).mkdir(parents=True)
        self.assertEqual(brief_modul.runden(brief), ["v1", "v2", "v10"])
        self.assertEqual(brief_modul.naechste_runde(brief), "v11")


class Gate(Basis):
    def test_saubere_runde_besteht(self) -> None:
        self.brief()
        ergebnis = qa.pruefe_runde(fixtures.baue(
            config.CLIENTS / "nordlicht" / "runden" / "v1"), "v1")
        self.assertTrue(ergebnis.bestanden, ergebnis.findings)
        self.assertEqual(ergebnis.score, 100)

    def test_fehlender_titel_ist_kritisch(self) -> None:
        self.brief()
        ergebnis = qa.pruefe_runde(fixtures.baue(
            config.CLIENTS / "nordlicht" / "runden" / "v1", titel=""), "v1")
        self.assertFalse(ergebnis.bestanden)
        self.assertIn("title", [f.key for f in ergebnis.findings])

    def test_bild_ohne_alt_ist_kritisch(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        seite = wurzel / "index.html"
        seite.write_text(seite.read_text().replace(
            'alt="Läuferin auf einer Straße im Morgenlicht"', ""))
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertIn("alt", [f.key for f in ergebnis.findings])

    def test_toter_interner_link_ist_kritisch(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        seite = wurzel / "index.html"
        seite.write_text(seite.read_text().replace('href="kontakt.html"',
                                                   'href="gibtsnicht.html"'))
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertTrue(any(f.area == "Navigation" for f in ergebnis.findings))

    def test_kontrast_unter_aa_ist_kritisch(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        (wurzel / "style.css").write_text(
            ":root { --bg: #ffffff; --fg: #9a9a9a; }\n")
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertIn("contrast:fg/bg", [f.key for f in ergebnis.findings])

    def test_kontrastrechnung_stimmt(self) -> None:
        self.assertEqual(qa.kontrast("#000000", "#ffffff"), 21.0)
        self.assertEqual(qa.kontrast("#ffffff", "#ffffff"), 1.0)
        self.assertIsNone(qa.kontrast("rot", "#fff"))

    def test_ueberschriftenebene_darf_nicht_springen(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        seite = wurzel / "index.html"
        seite.write_text(seite.read_text().replace("<h2>Zweite Ebene</h2>",
                                                   "<h3>Zweite Ebene</h3>"))
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertTrue(any(f.key.startswith("heading-sprung") for f in ergebnis.findings))

    def test_platzhalter_im_text_sind_kritisch(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        seite = wurzel / "index.html"
        seite.write_text(seite.read_text().replace(
            "<p>Noch ein Absatz.</p>", "<p>Öffnungszeiten: [bitte ergänzen]</p>"))
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertFalse(ergebnis.bestanden)
        self.assertIn("platzhalter", [f.key for f in ergebnis.findings])

    def test_fehlendes_bild_ist_kritisch(self) -> None:
        self.brief()
        wurzel = fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        seite = wurzel / "index.html"
        seite.write_text(seite.read_text().replace('src="bild.png"', 'src="fehlt.png"'))
        ergebnis = qa.pruefe_runde(wurzel, "v1")
        self.assertFalse(ergebnis.bestanden)
        self.assertTrue(any(f.key.startswith("bild-fehlt") for f in ergebnis.findings))

    def test_leere_runde_ist_ein_fehler(self) -> None:
        self.brief()
        leer = config.CLIENTS / "nordlicht" / "runden" / "v1"
        leer.mkdir(parents=True)
        ergebnis = qa.pruefe_runde(leer, "v1")
        self.assertIn("keine-seite", [f.key for f in ergebnis.findings])


class Ablauf(Basis):
    def test_gate_ist_erst_nach_der_revision_offen(self) -> None:
        brief = self.brief()
        store = Store()
        fixtures.baue(brief.ordner / "runden" / "v1")
        engine.messen(brief, "v1", store)

        zustand = engine.stand(brief, store)
        self.assertFalse(zustand.bereit)
        self.assertIn("mindestens", zustand.grund)
        with self.assertRaises(RuntimeError):
            engine.abgeben(brief, store)

        fixtures.baue(brief.ordner / "runden" / "v2")
        engine.messen(brief, "v2", store)
        zustand = engine.stand(brief, store)
        self.assertTrue(zustand.bereit, zustand.grund)
        ziel = engine.abgeben(brief, store)
        self.assertTrue((ziel / "index.html").exists())
        self.assertTrue((ziel / "ABGABE.md").exists())

    def test_rotes_gate_verhindert_die_abgabe(self) -> None:
        brief = self.brief()
        store = Store()
        fixtures.baue(brief.ordner / "runden" / "v1")
        fixtures.baue(brief.ordner / "runden" / "v2", titel="")
        engine.messen(brief, "v2", store)
        with self.assertRaises(RuntimeError):
            engine.abgeben(brief, store)

    def test_vergleich_zeigt_behobene_findings(self) -> None:
        brief = self.brief()
        store = Store()
        fixtures.baue(brief.ordner / "runden" / "v1", titel="")
        fixtures.baue(brief.ordner / "runden" / "v2")
        diff = engine.vergleiche(brief, "v1", "v2", store)
        self.assertEqual(diff["kritisch"]["von"], 1)
        self.assertEqual(diff["kritisch"]["nach"], 0)
        self.assertTrue(diff["behoben"])
        self.assertIn("index.html", diff["dateien"]["geaendert"])

    def test_messung_landet_im_store(self) -> None:
        brief = self.brief()
        store = Store()
        fixtures.baue(brief.ordner / "runden" / "v1")
        engine.messen(brief, "v1", store)
        gespeichert = store.round("nordlicht", "v1")
        self.assertIsNotNone(gespeichert)
        self.assertEqual(gespeichert["score"], 100)
        self.assertTrue(json.loads(gespeichert["qa"])["bestanden"])


class Cli(Basis):
    def test_jede_unterkommando_antwortet(self) -> None:
        self.brief()
        store = Store()
        fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v2")
        engine.messen(brief_modul.load_slug("nordlicht"), "v2", store)

        self.assertEqual(main([]), 0)
        self.assertEqual(main(["show", "nordlicht"]), 0)
        self.assertEqual(main(["plan", "nordlicht"]), 0)
        self.assertEqual(main(["compare", "nordlicht", "v1", "v2"]), 0)
        self.assertEqual(main(["run", "nordlicht", "v2"]), 0)
        self.assertEqual(main(["ship", "nordlicht"]), 0)

    def test_unbekannter_auftrag_ist_ein_aufruffehler(self) -> None:
        self.assertEqual(main(["show", "gibtsnicht"]), 2)

    def test_abgabe_ohne_revision_ist_code_drei(self) -> None:
        self.brief()
        fixtures.baue(config.CLIENTS / "nordlicht" / "runden" / "v1")
        main(["run", "nordlicht", "v1"])
        self.assertEqual(main(["ship", "nordlicht"]), 3)
