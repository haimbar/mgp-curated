SELECT
  CASE
    WHEN DEGMSC IS NULL                         THEN 'Unknown'
    WHEN DEGMSC IN (68, 93, 94)                 THEN 'CS / Systems'
    WHEN DEGMSC = 62                            THEN 'Statistics'
    WHEN DEGMSC = 65                            THEN 'Numerical Analysis'
    WHEN DEGMSC IN (70,74,76,78,80,81,82,83,85,86)
                                                THEN 'Physics / Applied'
    WHEN DEGMSC = 90                            THEN 'Operations Research'
    WHEN DEGMSC = 91                            THEN 'Economics / Game Theory'
    WHEN DEGMSC = 92                            THEN 'Biology'
    WHEN DEGMSC = 97                            THEN 'Math Education'
    WHEN DEGMSC = 60                            THEN 'Probability'
    ELSE                                             'Pure Mathematics'
  END AS field,
  COUNT(*) AS cnt
FROM DEGREES
GROUP BY field
ORDER BY cnt DESC;
