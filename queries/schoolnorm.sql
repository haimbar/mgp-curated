SELECT
    SCHOOL,
    LOWER(TRIM(SCHOOL))               AS norm_lower,
    COUNT(*)                          AS cnt
FROM DEGREES
WHERE SCHOOL IS NOT NULL AND SCHOOL <> '' AND SCHOOL <> 'NONE'
GROUP BY LOWER(TRIM(SCHOOL))
HAVING COUNT(DISTINCT SCHOOL) > 1    -- groups with >1 spelling
ORDER BY cnt DESC;
