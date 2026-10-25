-- ---------------------------------------------------------------------------
-- migrate_schema.sql
--
-- In-place migration of an existing mg.db to the revised schema:
--   * explicit PRIMARY KEY on every table
--       - surrogate DEGREE_ID (= old rowid) for DEGREES
--       - surrogate LINK_ID for STUDENTS, with partial unique indexes on the
--         (ADVISOR, STUDENT) pair (ADVISOR may be NULL = advisor not recorded)
--       - composite (MGID, METHOD) for SCORES
--       - CODE for MSC, MGID for FIELDS
--   * explicit FOREIGN KEY clauses
--   * STUDENTS cleaned so foreign_key_check is empty: advisor 0 -> NULL,
--     self-loops and rows whose endpoint is absent from PERSONS dropped
--   * LANGUAGE_DETECT / MSC_IMPUTE key column renamed DEGROWID -> DEGREE_ID
--     (values unchanged: both already stored DEGREES.rowid, which becomes
--      DEGREE_ID; rowid 1..N is contiguous so the mapping is the identity)
--
-- Use this when the raw .txt extracts are not available to run createdb.sql
-- from scratch.  Always back up mg.db first.
--
--   cp mg.db mg_backup_pre_pk_migration.db
--   sqlite3 mg.db ".read migrate_schema.sql"
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = OFF;      -- do not re-validate existing rows during copy
BEGIN;

-- --- MSC: add PK on CODE ------------------------------------------------
CREATE TABLE MSC_NEW (
    CODE INTEGER   PRIMARY KEY,
    NAME CHAR(100) NOT NULL );
INSERT INTO MSC_NEW (CODE, NAME) SELECT CODE, NAME FROM MSC;
DROP TABLE MSC;
ALTER TABLE MSC_NEW RENAME TO MSC;

-- --- DEGREES: add surrogate DEGREE_ID = old rowid ---------------------
CREATE TABLE DEGREES_NEW (
    DEGREE_ID INTEGER PRIMARY KEY,
    DEGMGID   INTEGER  NOT NULL REFERENCES PERSONS(MGID),
    DEGTYPE   CHAR(30) NOT NULL,
    DEGMSC    INTEGER           REFERENCES MSC(CODE),
    DEGYEAR   INTEGER  NOT NULL,
    SCHOOL    CHAR(60) NOT NULL,
    COUNTRY   CHAR(30) NOT NULL,
    TITLE     CHAR(300),
    LANGUAGE  CHAR(30) );
INSERT INTO DEGREES_NEW
      (DEGREE_ID, DEGMGID, DEGTYPE, DEGMSC, DEGYEAR, SCHOOL, COUNTRY, TITLE, LANGUAGE)
SELECT rowid,     DEGMGID, DEGTYPE, DEGMSC, DEGYEAR, SCHOOL, COUNTRY, TITLE, LANGUAGE
FROM   DEGREES;
DROP TABLE DEGREES;
ALTER TABLE DEGREES_NEW RENAME TO DEGREES;
CREATE INDEX idx_degrees_mgid    ON DEGREES(DEGMGID);
CREATE INDEX idx_degrees_msc     ON DEGREES(DEGMSC);
CREATE INDEX idx_degrees_country ON DEGREES(COUNTRY);
CREATE INDEX idx_degrees_year    ON DEGREES(DEGYEAR);

-- --- STUDENTS: surrogate LINK_ID, ADVISOR nullable -----------------
-- The raw table stores advisor 0 when the advisor is not recorded
-- (28,807 rows); it also has 4 self-loops and 3 edges to a student absent
-- from PERSONS.  Map 0 -> NULL and drop the unusable rows so the declared
-- foreign keys hold for every remaining row.
CREATE TABLE STUDENTS_NEW (
    LINK_ID INTEGER PRIMARY KEY,
    ADVISOR INTEGER          REFERENCES PERSONS(MGID),
    STUDENT INTEGER NOT NULL REFERENCES PERSONS(MGID) );
INSERT INTO STUDENTS_NEW (ADVISOR, STUDENT)
SELECT DISTINCT NULLIF(ADVISOR, 0), STUDENT
FROM   STUDENTS
WHERE  STUDENT <> ADVISOR
  AND  STUDENT IN (SELECT MGID FROM PERSONS)
  AND  (ADVISOR = 0 OR ADVISOR IN (SELECT MGID FROM PERSONS));
DROP TABLE STUDENTS;
ALTER TABLE STUDENTS_NEW RENAME TO STUDENTS;
CREATE UNIQUE INDEX ux_students_pair  ON STUDENTS(ADVISOR, STUDENT) WHERE ADVISOR IS NOT NULL;
CREATE UNIQUE INDEX ux_students_noadv ON STUDENTS(STUDENT)          WHERE ADVISOR IS NULL;
CREATE INDEX idx_students_advisor ON STUDENTS(ADVISOR);
CREATE INDEX idx_students_student ON STUDENTS(STUDENT);

-- --- FIELDS: PK on MGID (one row per person) -------------------------
CREATE TABLE FIELDS_NEW (
    MGID          INTEGER PRIMARY KEY REFERENCES PERSONS(MGID),
    GIVENFIELD    CHAR(5) DEFAULT 'NA',
    INFERREDFIELD CHAR(5) );
INSERT INTO FIELDS_NEW (MGID, GIVENFIELD, INFERREDFIELD)
SELECT MGID, GIVENFIELD, INFERREDFIELD FROM FIELDS;
DROP TABLE FIELDS;
ALTER TABLE FIELDS_NEW RENAME TO FIELDS;

-- --- SCORES: composite PK (MGID, METHOD) ---------------------------
CREATE TABLE SCORES_NEW (
    MGID   INTEGER  NOT NULL REFERENCES PERSONS(MGID),
    METHOD CHAR(20) NOT NULL,
    MATH DECIMAL(1,3), STAT DECIMAL(1,3), CS   DECIMAL(1,3),
    PHYS DECIMAL(1,3), ORE  DECIMAL(1,3), ECON DECIMAL(1,3),
    BIOL DECIMAL(1,3), OTHER DECIMAL(1,3),
    PRIMARY KEY (MGID, METHOD) );
INSERT INTO SCORES_NEW
SELECT MGID, METHOD, MATH, STAT, CS, PHYS, ORE, ECON, BIOL, OTHER FROM SCORES;
DROP TABLE SCORES;
ALTER TABLE SCORES_NEW RENAME TO SCORES;

-- --- LANGUAGE_DETECT: DEGROWID -> DEGREE_ID, add FK ----------------
CREATE TABLE LANGUAGE_DETECT_NEW (
    DEGREE_ID    INTEGER PRIMARY KEY REFERENCES DEGREES(DEGREE_ID),
    DEGMGID      INTEGER,
    LANGUAGE     CHAR(30),
    CONFIDENCE   REAL,
    METHOD       CHAR(30),
    IS_BILINGUAL INTEGER DEFAULT 0,
    LANG2        CHAR(30) );
INSERT INTO LANGUAGE_DETECT_NEW
      (DEGREE_ID, DEGMGID, LANGUAGE, CONFIDENCE, METHOD, IS_BILINGUAL, LANG2)
SELECT DEGROWID,  DEGMGID, LANGUAGE, CONFIDENCE, METHOD, IS_BILINGUAL, LANG2
FROM   LANGUAGE_DETECT;
DROP TABLE LANGUAGE_DETECT;
ALTER TABLE LANGUAGE_DETECT_NEW RENAME TO LANGUAGE_DETECT;
CREATE INDEX idx_langdet_mgid ON LANGUAGE_DETECT(DEGMGID);
CREATE INDEX idx_langdet_lang ON LANGUAGE_DETECT(LANGUAGE);

-- --- MSC_IMPUTE: DEGROWID -> DEGREE_ID, add FK --------------------
CREATE TABLE MSC_IMPUTE_NEW (
    DEGREE_ID  INTEGER PRIMARY KEY REFERENCES DEGREES(DEGREE_ID),
    DEGMGID    INTEGER,
    FIELD      CHAR(30),
    CONFIDENCE REAL,
    TITLE_W    REAL,
    NET_W      REAL,
    METHODS    CHAR(60) );
INSERT INTO MSC_IMPUTE_NEW
      (DEGREE_ID, DEGMGID, FIELD, CONFIDENCE, TITLE_W, NET_W, METHODS)
SELECT DEGROWID,  DEGMGID, FIELD, CONFIDENCE, TITLE_W, NET_W, METHODS
FROM   MSC_IMPUTE;
DROP TABLE MSC_IMPUTE;
ALTER TABLE MSC_IMPUTE_NEW RENAME TO MSC_IMPUTE;

-- SCHOOL_CANONICAL already has RAW_NAME as PRIMARY KEY; left unchanged.

COMMIT;

PRAGMA foreign_key_check;   -- expect no rows
PRAGMA integrity_check;
VACUUM;
