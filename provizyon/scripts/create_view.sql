USE [ESYS_SAGLIK_TEST]
GO

SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

DROP VIEW IF EXISTS dbo.S_VW_PROVIZYON_AI;
GO

CREATE VIEW dbo.S_VW_PROVIZYON_AI AS
SELECT
    pf.ID                        AS ProvizyonId,
    tt.Ad                        AS ProvizyonTipi,
    pfd.Ad                       AS ProvizyonDurumu,
    pf.ProvizyonFaturaDurumTanimID AS ProvizyonDurumId,

    -- Hasta bilgileri
    CASE WHEN pf.AkrabaID IS NOT NULL
         THEN ha.TCNo ELSE hp.TCNo
    END                          AS TCKimlik,
    hp.Sicil                     AS UyeSicil,
    CASE WHEN pf.AkrabaID IS NOT NULL
         THEN ha.Ad ELSE hp.Ad
    END                          AS HastaAd,
    CASE WHEN pf.AkrabaID IS NOT NULL
         THEN ha.Soyad ELSE hp.Soyad
    END                          AS HastaSoyad,
    CASE WHEN pf.AkrabaID IS NOT NULL
         THEN DATEDIFF(YEAR, ha.DogumTarihi, ISNULL(pf.HizmetTarih, GETDATE()))
         ELSE DATEDIFF(YEAR, hp.DogumTarihi, ISNULL(pf.HizmetTarih, GETDATE()))
    END                          AS HastaYas,
    CASE
        WHEN pf.AkrabaID IS NOT NULL THEN
            CASE ha.Cinsiyet WHEN 'E' THEN 'Erkek' WHEN 'K' THEN N'Kadın' ELSE ha.Cinsiyet END
        ELSE
            CASE hp.Cinsiyet WHEN 'E' THEN 'Erkek' WHEN 'K' THEN N'Kadın' ELSE hp.Cinsiyet END
    END                          AS Cinsiyet,
    pf.PersonelID                AS UyeId,

    -- Kurum / klinik bilgileri
    k.Ad                         AS KurumAdi,
    kg.Ad                        AS KurumTipi,
    il.Ad                        AS Il,
    pit.IslemTipiAd              AS IslemTipi,
    pit.BransAd                  AS Brans,
    pit.Doktor                   AS DoktorAdi,
    pf.HizmetTarih               AS HizmetTarih,

    -- ICD tani bilgileri (<~> ile birlesik: kod|ad)
    STUFF((
        SELECT '<~>' + icd.Kod + '|' + icd.Ad
        FROM dbo.PROVIZYON_FATURA_ISLEM_TIP pfit
        INNER JOIN dbo.P_FATURA_ISLEM_TIP_ICD10 picd ON pfit.ID = picd.ProvizyonFaturaIslemTipID
        INNER JOIN dbo.ICD10_TANIM icd ON picd.ICD10ID = icd.ID
        WHERE pfit.ProvizyonFaturaID = pf.ID
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 3, '') AS TaniBilgileri,

    -- Islem bilgileri (<~> ile birlesik: kod|ad|kodTipi)
    STUFF((
        SELECT '<~>' + att.Kod + '|' + att.Ad + '|' +
            CASE
                WHEN att.Kod LIKE '[0-9][0-9][0-9][0-9][0-9][0-9]'
                     AND att.Kod NOT LIKE '0%' THEN 'SUT'
                WHEN att.Kod LIKE '[0-9][0-9].[0-9]%' THEN 'HUV'
                WHEN att.Kod LIKE 'TZH.[0-9]%' THEN 'HUV'
                ELSE ''
            END
        FROM dbo.PROVIZYON_FATURA_AYAKTA_TEDAVI pfat
        INNER JOIN dbo.AYAKTA_TEDAVI_TANIM att ON pfat.AyaktaTedaviTanimID = att.ID
        WHERE pfat.ProvizyonFaturaID = pf.ID
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 3, '') AS IslemBilgileri,

    -- Belge dosya yollari (<~> ile birlesik: yol|dosyaAdi|dosyaTur|evrakAd)
    STUFF((
        SELECT '<~>' + ISNULL(pfl.DosyaYolu, '') + '|' + ISNULL(pfl.DosyaAd, '') + '|' + ISNULL(pfl.DosyaTur, '') + '|' + ISNULL(pfl.EvrakAd, '')
        FROM dbo.PROVIZYON_FILE pfl
        WHERE pfl.ProvizyonID = pf.ID
        ORDER BY pfl.KayitTarihi
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 3, '') AS BelgeBilgileri

FROM dbo.PROVIZYON_FATURA pf
LEFT JOIN dbo.TALEP_TUR_TANIM tt           ON pf.TalepTurTanimID = tt.ID
LEFT JOIN dbo.PROVIZYON_FATURA_DURUM_TANIM pfd ON pf.ProvizyonFaturaDurumTanimID = pfd.ID
LEFT JOIN dbo.KURUM_TANIM k                ON pf.KurumTanimID = k.ID
LEFT JOIN dbo.KURUM_GRUP_TANIM kg          ON k.KurumGrupID = kg.ID
LEFT JOIN dbo.IL il                        ON pf.IlID = il.ID
LEFT JOIN dbo.HASTA_PERSONEL hp            ON pf.PersonelID = hp.ID
LEFT JOIN dbo.HASTA_AKRABA ha             ON pf.AkrabaID = ha.ID
OUTER APPLY (
    SELECT TOP 1
        it.Ad   AS IslemTipiAd,
        b.Ad    AS BransAd,
        pfit2.Doktor
    FROM dbo.PROVIZYON_FATURA_ISLEM_TIP pfit2
    LEFT JOIN dbo.ISLEM_TIP_TANIM it ON pfit2.IslemTipID = it.ID
    LEFT JOIN dbo.BRANS_TANIM b      ON pfit2.BransTanimID = b.ID
    WHERE pfit2.ProvizyonFaturaID = pf.ID
) pit;
GO
