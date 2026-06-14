"""Kontrollierte Calandi-Branchenliste (Phase A, Benchmarking-Fundament).

Single Source of Truth: diese Liste wird per Migration 0005 in die Tabelle
`industry_categories` geseedet (FK-Integrität) UND treibt das Branche-Dropdown
(kein DB-Read pro Render). Council-Empfehlung: ~40 strukturierte Kategorien
statt Freitext — STB-Mandantschaft ist Pareto-verteilt, das reicht. `wz_top`
ist der WZ-2008-Abschnitt (A..U) als Anschluss-Metadaten; `None` = sammelnd.

Erweitern/ändern: hier pflegen, dann den geänderten Seed-Block in einer neuen
Migration nachziehen (codes sind stabil — labels dürfen sich ändern).
"""

# (code, label, wz_top, sort_order)
INDUSTRY_CATEGORIES: list[tuple[str, str, str | None, int]] = [
    # Handel
    ("handel_grosshandel", "Großhandel", "G", 10),
    ("handel_einzelhandel", "Einzelhandel (stationär)", "G", 11),
    ("handel_ecommerce", "Online-/Versandhandel", "G", 12),
    ("handel_kfz", "Kfz-Handel & -Werkstätten", "G", 13),
    # Produktion / Verarbeitendes Gewerbe
    ("prod_maschinenbau", "Maschinen-/Anlagenbau", "C", 20),
    ("prod_metall", "Metallverarbeitung", "C", 21),
    ("prod_elektro", "Elektro-/Elektronikindustrie", "C", 22),
    ("prod_lebensmittel", "Lebensmittelherstellung", "C", 23),
    ("prod_kunststoff_chemie", "Kunststoff/Chemie", "C", 24),
    ("prod_konsumgueter", "Konsumgüter/Markenartikel", "C", 25),
    ("prod_sonstige", "Sonstige Herstellung", "C", 29),
    # Bau
    ("bau_hoch_tief", "Hoch-/Tiefbau", "F", 30),
    ("bau_ausbau", "Ausbau/Bauinstallation (SHK, Elektro)", "F", 31),
    ("bau_handwerk", "Sonstiges Bauhandwerk", "F", 32),
    # Transport / Logistik
    ("logistik_transport", "Transport & Logistik", "H", 40),
    # Wirtschaftliche Dienstleistungen
    ("dl_gebaeude", "Gebäudedienste/Facility/Reinigung", "N", 50),
    ("dl_personal", "Personaldienstleistung/Zeitarbeit", "N", 51),
    ("dl_sicherheit", "Sicherheit & Brandschutz", "N", 52),
    ("dl_sonstige", "Sonstige wirtschaftliche Dienstleistung", "N", 59),
    # IT / Medien
    ("it_software", "Software/SaaS", "J", 60),
    ("it_services", "IT-Dienstleistung/Systemhaus", "J", 61),
    ("medien_agentur", "Marketing-/Werbeagentur", "M", 62),
    ("medien_verlag", "Verlag/Medienproduktion", "J", 63),
    # Freiberuflich / wissensbasiert
    ("beratung_unternehmen", "Unternehmensberatung", "M", 70),
    ("kanzlei_recht_steuer", "Rechts-/Steuer-/WP-Kanzlei", "M", 71),
    ("ingenieur_architektur", "Ingenieur-/Architekturbüro", "M", 72),
    # Gesundheit / Soziales
    ("gesundheit_praxis", "Arzt-/Zahnarztpraxis/MVZ", "Q", 80),
    ("gesundheit_pflege", "Pflege/Senioren", "Q", 81),
    ("gesundheit_apotheke", "Apotheke", "G", 82),
    ("gesundheit_sonstige", "Sonstige Gesundheit", "Q", 89),
    # Gastgewerbe / Tourismus
    ("gastro_restaurant", "Gastronomie", "I", 90),
    ("gastro_hotel", "Hotellerie/Beherbergung", "I", 91),
    # Immobilien / Finanz
    ("immobilien", "Immobilien/Hausverwaltung", "L", 100),
    ("finanz_versicherung", "Finanz-/Versicherungsdienste", "K", 101),
    # Weitere Sektoren
    ("landwirtschaft", "Land-/Forstwirtschaft", "A", 110),
    ("energie_umwelt", "Energie/Umwelt/Entsorgung", "E", 111),
    ("bildung", "Bildung/Training", "P", 112),
    ("handwerk_sonstiges", "Sonstiges Handwerk", "S", 113),
    # Sammelkategorie
    ("sonstige", "Sonstige/Nicht zugeordnet", None, 999),
]

# Schnelles Lookup + Validierung
INDUSTRY_CODES: set[str] = {c for c, _l, _w, _s in INDUSTRY_CATEGORIES}


def industry_choices() -> list[dict]:
    """Dropdown-Quelle (sortiert), ohne DB-Read."""
    return [{"code": c, "label": l}
            for c, l, _w, _s in sorted(INDUSTRY_CATEGORIES, key=lambda t: t[3])]


def is_valid_industry(code: str | None) -> bool:
    """True für einen bekannten Code ODER None (Branche optional)."""
    return code is None or code in INDUSTRY_CODES
