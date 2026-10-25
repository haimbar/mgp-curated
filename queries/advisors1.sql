SELECT students, COUNT(*) AS num_advisors
FROM (
    SELECT ADVISOR, COUNT(*) AS students
    FROM STUDENTS
    GROUP BY ADVISOR
)
GROUP BY students
ORDER BY students
LIMIT 10;
