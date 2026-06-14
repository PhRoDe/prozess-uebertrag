-- Phase B v2 (Council 2026-06-14): Umsatz-Default-Margen + EBITDA + weitere
-- Quoten + GuV-Verfahren. MANUELL ausführen, BEVOR der Code deployt.
-- company_year_metrics ist noch leer → Spalten-Umbau gefahrlos.

-- Alte v1-Margen (nur Gesamtleistung-Basis) entfernen — ersetzt durch duale.
alter table company_year_metrics drop column if exists rohertragsmarge;
alter table company_year_metrics drop column if exists ebit_marge;
alter table company_year_metrics drop column if exists jue_marge;

-- Neue Absolutwerte
alter table company_year_metrics add column if not exists sonst_betr_ertraege numeric;
alter table company_year_metrics add column if not exists finanzertraege numeric;
alter table company_year_metrics add column if not exists zinsaufwand numeric;
alter table company_year_metrics add column if not exists ebitda numeric;
alter table company_year_metrics add column if not exists ebit_analytisch numeric;

-- Duale Margen (Umsatz = Default für externe Vergleiche, Gesamtleistung sekundär)
alter table company_year_metrics add column if not exists rohertragsmarge_umsatz numeric;
alter table company_year_metrics add column if not exists rohertragsmarge_gesamtleistung numeric;
alter table company_year_metrics add column if not exists betriebsergebnis_marge_umsatz numeric;
alter table company_year_metrics add column if not exists betriebsergebnis_marge_gesamtleistung numeric;
alter table company_year_metrics add column if not exists ebitda_marge_umsatz numeric;
alter table company_year_metrics add column if not exists ebitda_marge_gesamtleistung numeric;
alter table company_year_metrics add column if not exists jue_marge_umsatz numeric;
alter table company_year_metrics add column if not exists jue_marge_gesamtleistung numeric;
alter table company_year_metrics add column if not exists materialquote_umsatz numeric;
alter table company_year_metrics add column if not exists materialquote_gesamtleistung numeric;

-- Einzel-Quoten + Verfahren
alter table company_year_metrics add column if not exists abschreibungsquote_umsatz numeric;
alter table company_year_metrics add column if not exists aktivierungsquote numeric;
alter table company_year_metrics add column if not exists zinsdeckung numeric;
alter table company_year_metrics add column if not exists steuerquote numeric;
alter table company_year_metrics add column if not exists verfahren text;   -- gkv | null

-- v2-Formeln dokumentieren (Council-Empfehlung; metrics_version=2).
insert into metric_definitions (version, metric, formula, basis) values
  (2, 'gesamtleistung', 'umsatz + bestandsveraenderung + aktivierte_eigenleistungen', null),
  (2, 'rohertrag', 'gesamtleistung - materialaufwand', null),
  (2, 'betriebsergebnis', 'rohertrag + sonst_betr_ertraege - personalaufwand - abschreibungen - sonst_betr_aufw', null),
  (2, 'ebitda', 'betriebsergebnis + abschreibungen', null),
  (2, 'ebit_analytisch', 'jue + steuern + zinsaufwand (Banker-EBIT)', null),
  (2, 'finanzergebnis', 'finanzertraege - zinsaufwand', null),
  (2, 'neutrales_ergebnis', 'jue + steuern - betriebsergebnis - finanzergebnis (Residuum)', null),
  (2, 'jue', 'PDF-Anker falls vorhanden, sonst ergebnis_vor_steuern - steuern', null),
  (2, 'rohertragsmarge', 'rohertrag / basis', 'umsatz (default) + gesamtleistung'),
  (2, 'betriebsergebnis_marge', 'betriebsergebnis / basis', 'umsatz (default) + gesamtleistung'),
  (2, 'ebitda_marge', 'ebitda / basis', 'umsatz (default) + gesamtleistung'),
  (2, 'jue_marge', 'jue / basis', 'umsatz (default) + gesamtleistung'),
  (2, 'materialquote', 'materialaufwand / basis', 'umsatz (default) + gesamtleistung'),
  (2, 'personalaufwandsquote', 'personalaufwand / gesamtleistung', 'gesamtleistung'),
  (2, 'abschreibungsquote', 'abschreibungen / umsatz', 'umsatz'),
  (2, 'aktivierungsquote', '(bestandsveraenderung + aktivierte_eigenleistungen) / gesamtleistung (Verzerrungs-Indikator)', 'gesamtleistung'),
  (2, 'zinsdeckung', 'betriebsergebnis / zinsaufwand', null),
  (2, 'steuerquote', 'steuern / (betriebsergebnis + finanzergebnis)', null),
  (2, 'verfahren', 'gkv wenn GKV-Sektionen (Material/Bestandsv./aktiv.Eigenl.) vorhanden, sonst null', null)
on conflict (version, metric) do update
  set formula = excluded.formula, basis = excluded.basis;
