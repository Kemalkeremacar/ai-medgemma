-- READ-ONLY mapping research template.
-- Replace {{CODE}} with a single local/HUV/dotted candidate code.
-- Do not run UPDATE/INSERT/DELETE/MERGE/EXEC apply commands.

-- 1) Direct procedure definition lookup
SELECT TOP 50
    att.Kod,
    att.Ad,
    att.HuvKodu,
    att.*
FROM AYAKTA_TEDAVI_TANIM att
WHERE att.Kod = '{{CODE}}'
   OR att.HuvKodu = '{{CODE}}';

-- 2) If direct lookup fails, search by normalized dotted/legacy variants.
-- Add local normalization rules manually and keep output aggregate/catalog-only.
SELECT TOP 50
    att.Kod,
    att.Ad,
    att.HuvKodu
FROM AYAKTA_TEDAVI_TANIM att
WHERE REPLACE(att.Kod, '.', '') = REPLACE('{{CODE}}', '.', '')
   OR REPLACE(att.HuvKodu, '.', '') = REPLACE('{{CODE}}', '.', '');

-- 3) Optional: count historical references only; do not export case rows.
SELECT
    att.Kod,
    att.Ad,
    COUNT_BIG(*) AS provision_row_count
FROM PROVIZYON_FATURA_AYAKTA_TEDAVI pfat
JOIN AYAKTA_TEDAVI_TANIM att ON att.Id = pfat.AyaktaTedaviTanimId
WHERE att.Kod = '{{CODE}}'
   OR att.HuvKodu = '{{CODE}}'
GROUP BY att.Kod, att.Ad;
