SELECT
  SUM(CASE WHEN DEGMSC IS NULL THEN 1 ELSE 0 END)            AS missing_msc,
  SUM(CASE WHEN TITLE = 'Unknown' OR TITLE IS NULL THEN 1
           ELSE 0 END)                                         AS missing_title,
  SUM(CASE WHEN DEGYEAR = 'Unknown' OR DEGYEAR IS NULL
           OR DEGYEAR = '' THEN 1 ELSE 0 END)                 AS missing_year,
  COUNT(*)                                                     AS total
FROM DEGREES;
