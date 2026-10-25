-- ---------------------------------------------------------------------------
-- mg.db schema.  Every base table has an explicit primary key; every
-- cross-table reference is an explicit FOREIGN KEY.  The advisor--student
-- relation is many-to-many and is stored in the STUDENTS junction table;
-- every other relation is one-to-many (or one-to-one) and is a foreign key.
-- Running this script reproduces a database on which PRAGMA foreign_key_check
-- returns no rows.
--
-- Run with:  sqlite3 mg.db ".read createdb.sql"
-- Input files (msc, persons.txt, degrees.txt, studuniq.txt,
-- fieldsinferred2.txt) must be in the working directory.
-- ---------------------------------------------------------------------------

PRAGMA foreign_keys = ON;

-- --- Subject classification lookup -----------------------------------------
CREATE TABLE MSC
         (CODE  INTEGER   PRIMARY KEY,
          NAME  CHAR(100) NOT NULL );

-- --- People (advisors and/or graduates) ----------------------------------
CREATE TABLE PERSONS
         (MGID  INTEGER PRIMARY KEY,          -- native Math Genealogy id
          LNAME CHAR(30) NOT NULL,
          FNAME CHAR(30) NOT NULL,
          ONAME CHAR(30) );

-- --- Degrees -------------------------------------------------------------
-- One row per dissertation.  DEGMGID (the graduate) is NOT unique: 1,866
-- individuals appear on two or more degree records, so DEGREES needs a
-- surrogate key.  DEGREE_ID is assigned in input-file order and replaces
-- the implicit rowid that LANGUAGE_DETECT and MSC_IMPUTE reference.
CREATE TABLE DEGREES
         (DEGREE_ID INTEGER PRIMARY KEY,
          DEGMGID   INTEGER   NOT NULL REFERENCES PERSONS(MGID),
          DEGTYPE   CHAR(30)  NOT NULL,
          DEGMSC    INTEGER            REFERENCES MSC(CODE),   -- NULL if unknown
          DEGYEAR   INTEGER   NOT NULL,
          SCHOOL    CHAR(60)  NOT NULL,
          COUNTRY   CHAR(30)  NOT NULL,
          TITLE     CHAR(300),                                 -- NULL if unrecorded
          LANGUAGE  CHAR(30) );

-- --- Advisor--student links (junction table for the M:N relation) -------
-- ADVISOR is NULL when the graduate's advisor is not recorded (the raw
-- extract stores 0 for that case).  The natural key is the (ADVISOR, STUDENT)
-- pair, but a NULLable column cannot sit in a primary key, so LINK_ID is a
-- surrogate single-column key and two partial unique indexes enforce the
-- pair: one for edges with a known advisor, one allowing a student at most
-- one "advisor unknown" edge.
CREATE TABLE STUDENTS
         (LINK_ID INTEGER PRIMARY KEY,
          ADVISOR INTEGER          REFERENCES PERSONS(MGID),
          STUDENT INTEGER NOT NULL REFERENCES PERSONS(MGID) );

-- --- Person-level broad field (derived, not from MGP) -------------------
CREATE TABLE FIELDS
         (MGID          INTEGER PRIMARY KEY REFERENCES PERSONS(MGID),  -- one row per person
          GIVENFIELD    CHAR(5) DEFAULT 'NA',
          INFERREDFIELD CHAR(5) );

-- --- Field-affinity scores (one row per person per scoring method) ------
CREATE TABLE SCORES
         (MGID   INTEGER  NOT NULL REFERENCES PERSONS(MGID),
          METHOD CHAR(20) NOT NULL,
          MATH DECIMAL(1, 3), STAT DECIMAL(1, 3), CS  DECIMAL(1, 3),
          PHYS DECIMAL(1, 3), ORE  DECIMAL(1, 3), ECON DECIMAL(1, 3),
          BIOL DECIMAL(1, 3), OTHER DECIMAL(1, 3),
          PRIMARY KEY (MGID, METHOD) );

-- ---------------------------------------------------------------------------
-- Load the raw MGP extracts.
-- ---------------------------------------------------------------------------
.import msc                 MSC
.import persons.txt          PERSONS
.import fieldsinferred2.txt  FIELDS

-- STUDENTS is loaded through a staging table: the raw file has two columns
-- (no surrogate key), advisor 0 must become NULL, and a few unusable rows
-- are dropped -- self-loops (ADVISOR = STUDENT) and edges whose endpoint is
-- absent from PERSONS.
CREATE TABLE STUDENTS_STAGING (ADVISOR INTEGER, STUDENT INTEGER);
.import studuniq.txt STUDENTS_STAGING
INSERT INTO STUDENTS (ADVISOR, STUDENT)
SELECT DISTINCT NULLIF(ADVISOR, 0), STUDENT
FROM   STUDENTS_STAGING
WHERE  STUDENT <> ADVISOR
  AND  STUDENT IN (SELECT MGID FROM PERSONS)
  AND  (ADVISOR = 0 OR ADVISOR IN (SELECT MGID FROM PERSONS));
DROP TABLE STUDENTS_STAGING;
CREATE UNIQUE INDEX ux_students_pair  ON STUDENTS(ADVISOR, STUDENT) WHERE ADVISOR IS NOT NULL;
CREATE UNIQUE INDEX ux_students_noadv ON STUDENTS(STUDENT)          WHERE ADVISOR IS NULL;

-- DEGREES is loaded through a staging table because degrees.txt has eight
-- fields and the target table has nine columns (the leading surrogate key).
CREATE TABLE DEGREES_STAGING
         (DEGMGID INTEGER, DEGTYPE CHAR(30), DEGMSC INTEGER, DEGYEAR INTEGER,
          SCHOOL CHAR(60), COUNTRY CHAR(30), TITLE CHAR(300), LANGUAGE CHAR(30) );
.import degrees.txt DEGREES_STAGING

INSERT INTO DEGREES
       (DEGMGID, DEGTYPE, DEGMSC, DEGYEAR, SCHOOL, COUNTRY, TITLE, LANGUAGE)
SELECT  DEGMGID, DEGTYPE, DEGMSC, DEGYEAR, SCHOOL, COUNTRY, TITLE, LANGUAGE
FROM    DEGREES_STAGING;
DROP TABLE DEGREES_STAGING;

-- Derived tables (SCHOOL_CANONICAL, LANGUAGE_DETECT, MSC_IMPUTE) are created
-- by the curation scripts (school_canonicalize.py, language_infer.py,
-- msc_impute.py); LANGUAGE_DETECT and MSC_IMPUTE key on DEGREES.DEGREE_ID.

-- ---------------------------------------------------------------------------
-- Fix known data-entry problems in the degree year.
-- ---------------------------------------------------------------------------
UPDATE degrees SET degyear = 2018 WHERE degyear = 'Aug. 2018';
UPDATE degrees SET degyear = 2007 WHERE degyear = '200';
UPDATE degrees SET degyear = 1999 WHERE degyear = '199';
UPDATE degrees SET degyear = 2014 WHERE degyear = '43';

-- ---------------------------------------------------------------------------
-- Example export: student / advisor / subject / year for a date range.
-- ---------------------------------------------------------------------------
-- .mode csv
-- .output ~/Desktop/mscs.csv
-- SELECT STUDENTS.STUDENT, STUDENTS.ADVISOR, DEGREES.DEGMSC, DEGREES.DEGYEAR
-- FROM STUDENTS
-- LEFT JOIN DEGREES ON STUDENTS.STUDENT = DEGREES.DEGMGID
-- WHERE DEGREES.DEGYEAR BETWEEN 1924 AND 2025 ;
-- .output stdout
