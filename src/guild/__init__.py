"""Guild — eine Design- und Entwicklungsfirma aus Rollen, Runden und einem Gate.

Die Firma ist kein einzelner Prompt, sondern ein Prozess:

    Briefing → Runde (Bau) → Kritik (Findings) → Revision → QA-Gate → Abgabe

Jede Runde liegt als eigener Ordner auf der Platte, jede Kritik als JSON, jedes QA-Ergebnis
in der Datenbank. Das Gate ist der Teil, der „hochprofessionell" prüfbar macht: eine Runde
verlässt das Haus erst, wenn Titel, Sprache, Beschreibung, Überschriftenordnung, Alt-Texte,
interne Links, Kontrast (WCAG AA), Viewport und Größenbudget stimmen.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
