-- Phase A: Firmen-Entität + kontrollierte Branchen + Job-Metadaten
-- (Benchmarking-Fundament). MANUELL im Supabase-SQL-Editor ausführen, BEVOR der
-- Code deployt. Alle Job-Spalten nullable → Bestandsjobs bleiben gültig.
--
-- industry_categories ist die DB-Seite der Single Source of Truth in
-- app/industries.py (Seed unten muss dazu passen). companies referenziert sie
-- per FK (Vergleichbarkeit). branche_label bleibt Freitext-Notiz (Hybrid).

create table if not exists industry_categories (
    code              text primary key,        -- stabiler Slug (app/industries.py)
    label             text not null,
    wz2008_top_level  text,                     -- WZ-2008-Abschnitt (A..U), Metadaten
    sort_order        int not null default 100
);

create table if not exists companies (
    id            uuid primary key default gen_random_uuid(),
    name          text not null,
    rechtsform    text,                          -- GmbH, GmbH & Co. KG, e.K., Einzelunt., ...
    branche_code  text references industry_categories(code),
    branche_label text,                          -- Freitext-Notiz (Hybrid)
    revenue_band  text,                          -- '<1M' | '1-5M' | '5-25M' | '>25M'
    employee_band text,                          -- '<10' | '10-50' | '50-250' | '>250'
    created_by    text,
    created_at    timestamptz not null default now()
);
create index if not exists companies_branche_idx on companies (branche_code);
create index if not exists companies_created_by_idx on companies (created_by);

-- Job → Firma (nullable: Legacy-Jobs ohne Firma). Plus Perioden-/Quellen-
-- Metadaten fürs spätere Benchmarking (data_source, Periodenlänge, Konsolidierung).
alter table jobs add column if not exists company_id uuid references companies(id);
alter table jobs add column if not exists source_type text;        -- ja|bwa|euer|susa (dominant)
alter table jobs add column if not exists period_start date;
alter table jobs add column if not exists period_end date;
alter table jobs add column if not exists coverage_months int;
alter table jobs add column if not exists is_consolidated boolean;

-- RLS wie bei keepalive/line_items: nur service_role (App) greift zu.
alter table industry_categories enable row level security;
alter table companies enable row level security;

-- Seed der Calandi-Branchen (Quelle: app/industries.py). Idempotent.
insert into industry_categories (code, label, wz2008_top_level, sort_order) values
  ('handel_grosshandel', 'Großhandel', 'G', 10),
  ('handel_einzelhandel', 'Einzelhandel (stationär)', 'G', 11),
  ('handel_ecommerce', 'Online-/Versandhandel', 'G', 12),
  ('handel_kfz', 'Kfz-Handel & -Werkstätten', 'G', 13),
  ('prod_maschinenbau', 'Maschinen-/Anlagenbau', 'C', 20),
  ('prod_metall', 'Metallverarbeitung', 'C', 21),
  ('prod_elektro', 'Elektro-/Elektronikindustrie', 'C', 22),
  ('prod_lebensmittel', 'Lebensmittelherstellung', 'C', 23),
  ('prod_kunststoff_chemie', 'Kunststoff/Chemie', 'C', 24),
  ('prod_konsumgueter', 'Konsumgüter/Markenartikel', 'C', 25),
  ('prod_sonstige', 'Sonstige Herstellung', 'C', 29),
  ('bau_hoch_tief', 'Hoch-/Tiefbau', 'F', 30),
  ('bau_ausbau', 'Ausbau/Bauinstallation (SHK, Elektro)', 'F', 31),
  ('bau_handwerk', 'Sonstiges Bauhandwerk', 'F', 32),
  ('logistik_transport', 'Transport & Logistik', 'H', 40),
  ('dl_gebaeude', 'Gebäudedienste/Facility/Reinigung', 'N', 50),
  ('dl_personal', 'Personaldienstleistung/Zeitarbeit', 'N', 51),
  ('dl_sicherheit', 'Sicherheit & Brandschutz', 'N', 52),
  ('dl_sonstige', 'Sonstige wirtschaftliche Dienstleistung', 'N', 59),
  ('it_software', 'Software/SaaS', 'J', 60),
  ('it_services', 'IT-Dienstleistung/Systemhaus', 'J', 61),
  ('medien_agentur', 'Marketing-/Werbeagentur', 'M', 62),
  ('medien_verlag', 'Verlag/Medienproduktion', 'J', 63),
  ('beratung_unternehmen', 'Unternehmensberatung', 'M', 70),
  ('kanzlei_recht_steuer', 'Rechts-/Steuer-/WP-Kanzlei', 'M', 71),
  ('ingenieur_architektur', 'Ingenieur-/Architekturbüro', 'M', 72),
  ('gesundheit_praxis', 'Arzt-/Zahnarztpraxis/MVZ', 'Q', 80),
  ('gesundheit_pflege', 'Pflege/Senioren', 'Q', 81),
  ('gesundheit_apotheke', 'Apotheke', 'G', 82),
  ('gesundheit_sonstige', 'Sonstige Gesundheit', 'Q', 89),
  ('gastro_restaurant', 'Gastronomie', 'I', 90),
  ('gastro_hotel', 'Hotellerie/Beherbergung', 'I', 91),
  ('immobilien', 'Immobilien/Hausverwaltung', 'L', 100),
  ('finanz_versicherung', 'Finanz-/Versicherungsdienste', 'K', 101),
  ('landwirtschaft', 'Land-/Forstwirtschaft', 'A', 110),
  ('energie_umwelt', 'Energie/Umwelt/Entsorgung', 'E', 111),
  ('bildung', 'Bildung/Training', 'P', 112),
  ('handwerk_sonstiges', 'Sonstiges Handwerk', 'S', 113),
  ('sonstige', 'Sonstige/Nicht zugeordnet', null, 999)
on conflict (code) do update
  set label = excluded.label,
      wz2008_top_level = excluded.wz2008_top_level,
      sort_order = excluded.sort_order;
