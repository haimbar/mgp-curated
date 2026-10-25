# Curated Mathematics Genealogy Project: code and reproducibility guide

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23652111.svg)](https://doi.org/10.5281/zenodo.23652111)

**Paper:** Haim Bar, Neil A. Spencer, Xingche Guo, Elizabeth D. Schifano, and Jun Yan (2026).
"From Genealogy to Research-Ready Data: Curating, Validating, and Applying the Mathematics Genealogy Project."
*Journal of Data Science.*

**Zenodo deposit (curated database + code):** https://doi.org/10.5281/zenodo.23652111

---

## Reproduce the paper's results (about 2 minutes)

Everything in the paper that is derived from the curated database can be
reproduced from the database alone.  No account, credentials, or download from
the Math Genealogy Project or ProQuest is needed.

```bash
# 1. Get the code (this repository) and the curated database (Zenodo deposit).
git clone https://github.com/haimbar/mgp-curated.git
cd mgp-curated
#    Download mg.db from https://doi.org/10.5281/zenodo.23652111 into this folder.
#    (optional) check the download:   shasum -a 256 -c mg.db.sha256

# 2. Install the Python packages (Python 3.10 or later).
pip install -r requirements.txt

# 3. Run everything.
python3 reproduce.py --db mg.db --out results
```

`reproduce.py` does the following, and prints `PASS` or `FAIL` for every value:

1. Recomputes, with SQL, the counts and tables in the paper that come from the
   database (Tables 1-3 and S1-S13, and the numbers quoted in the text), and
   compares each with the value printed in the paper.
2. Runs the figure scripts for Figures 1-4, S2 and S3 and writes the PDFs to
   `results/`.
3. Recomputes the quantities derived from them (cross-validated accuracy, ROC AUC
   values in Table S11, margin statistics in Table S10, expected true and false
   positives in Table S12, the Fuh example) and compares them with the paper.

The full report is written to `results/summary.txt`.  The exit status is 0 if every
check passes.  `--quick` skips the figure scripts (about 10 seconds).  On a
laptop the complete run takes about 1.5 to 2 minutes; the two slowest scripts are
`save_top2_probs.py` and `plot_roc_margin.py`.

Notes on what to expect:

* The figures are regenerated, not copied.  They agree with the figures in the
  paper up to cosmetic differences in fonts or layout across matplotlib versions.
* Cross-validation uses a fixed seed (`--seed 42`), and the other scripts are
  deterministic, so the numbers should agree exactly.  A runner-up label in
  `top2_probs.csv` can differ when two fields tie to the last digit; this does not
  affect any reported number.
* Tables 4 and 5 (coverage against ProQuest) need ProQuest counts that we are not
  permitted to redistribute, so `reproduce.py` does not check them by default (see
  below).  The MGP side of those tables is computed from `mg.db`.

### What is, and is not, expected to be reproducible

| Step | Reproducible from `mg.db`? |
|---|---|
| All tables and figures derived from the database (above) | Yes: `reproduce.py` |
| Building `mg.db` from the raw MGP records (Part 1 below) | Code provided; not expected to be repeated.  It needs an MGP account and several days of API downloads, and the MGP data change over time. |
| ProQuest counts for the coverage comparison (Tables 4-5) | No.  Needs an institutional ProQuest subscription, and the counts cannot be redistributed. |

---

## Part 1: Database curation (documentation of how `mg.db` was built)

This part is documented for completeness.  It is not needed to reproduce the
paper's results.  It produces `mg.db`, a SQLite database that consolidates the raw
MGP data and enriches it with imputed broad-field codes, canonical school names,
and inferred dissertation languages.  The steps must be run in the order given.
`<code_dir>` is the directory containing this repository and `<data_dir>` is any
writable directory for the raw data and the database.

### Software requirements

Python 3.10 or later, and the packages in `requirements.txt`.  Install the
`pymathgene` package from this repository:

```bash
cd <code_dir>/pymg
pip install -e .
```

### Accounts and credentials

| Requirement | Purpose | Where to register |
|---|---|---|
| MGP account (email + password) | Authenticate to the MGP REST API to download records | <https://mathgenealogy.org> (free) |
| Institutional ProQuest subscription | Only for Step 7 (coverage validation) | Through your institution's library |

---

### Step 1: Download raw records from the MGP API

The MGP API returns one JSON record per mathematician.  Records are downloaded
incrementally and appended to `allmgdat.txt` in `<data_dir>`.  Each login session
issues a JSON Web Token that expires after a fixed period; you may need to
re-authenticate and resume from the last downloaded ID.

```python
from pymathgene import get_mg_data
import os

datadir = get_mg_data.init()
os.chdir(datadir)

lastID = get_mg_data.lastID(datadir)   # 0 if starting fresh
n = get_mg_data.max_recs()             # at most 5,000 per token

cred  = get_mg_data.getlogin()
token = get_mg_data.login(cred)        # raises RuntimeError if authentication fails

# Gaps (HTTP 502) are normal because MGP IDs are not contiguous.
res = get_mg_data.query_loop(cred, token, first=lastID + 1,
                             last=lastID + n + 1, max_fails=20)
print(res)   # {'total': <records found>, 'fails': <gaps skipped>}
```

Repeat (re-authenticating each session) until the download is complete.  The
complete MGP database contains approximately 300,000 records.

### Step 2: Parse raw JSON into flat import files

```python
from pymathgene import get_mg_data
import os

datadir = "<data_dir>"
os.chdir(datadir)
flds = get_mg_data.parse_batch("allmgdat.txt")
get_mg_data.error_checking(datadir, flds)
```

`parse_batch` produces `persons.txt`, `degrees.txt`, `students.txt`, and
`studuniq.txt` (pipe-delimited).  The standalone script `readjson.py` performs the
same parsing without the `pymathgene` package.

### Step 3: Create the SQLite database

```bash
cd <data_dir>
sqlite3 mg.db < <code_dir>/createdb.sql
```

`createdb.sql` creates tables `MSC`, `PERSONS`, `DEGREES`, `STUDENTS`, `FIELDS`,
and `SCORES`, each with an explicit primary key (surrogate keys `DEGREE_ID` for
`DEGREES` and `LINK_ID` for `STUDENTS`, and a composite key for `SCORES`) and
explicit `FOREIGN KEY` clauses; bulk-imports the flat files (`DEGREES` and
`STUDENTS` through staging tables; an advisor recorded as `0`, the MGP code for an
unknown advisor, is stored as `NULL`, and seven unusable rows are dropped so that
`PRAGMA foreign_key_check` returns no rows); and applies a small set of manual
corrections for malformed year values (e.g. `'Aug. 2018'` becomes `2018`).

To upgrade an older `mg.db` in place instead of rebuilding it, run
`migrate_schema.sql`.  The `MSC` table needs a pipe-delimited file named `msc` in
`<data_dir>` with the MSC 2020 codes and names (<https://msc2020.org>).

### Step 4: Infer dissertation language

```bash
python3 <code_dir>/language_infer.py --db <data_dir>/mg.db
```

Creates `LANGUAGE_DETECT`.  Each degree row gets a BCP-47 language code, a
confidence score (0-1), and a detection method, combining `langdetect` (seeded
with 42) with a country-to-language prior.  Bilingual titles are flagged.  Use
`--limit 1000` to test on a subset.

### Step 5: Canonicalize school names

```bash
python3 <code_dir>/school_canonicalize.py --db <data_dir>/mg.db
```

Creates `SCHOOL_CANONICAL`, mapping each of the 2,867 raw school-name strings to a
canonical name with a match method and confidence, and writes uncertain pairs to
`school_review.csv`.  After the first run, inspect `school_review.csv`, add
confirmed pairs to the alias table at the top of the script, and re-run.

### Step 6: Impute missing broad-field (MSC) codes

```bash
python3 <code_dir>/msc_impute.py --db <data_dir>/mg.db
```

About 41% of degree records have no MSC code.  This script imputes a broad field
for them from three signals (title keywords, the advisor/sibling/student network,
and a year prior) and writes `MSC_IMPUTE` (`DEGREE_ID`, `DEGMGID`, `FIELD`,
`CONFIDENCE`, `TITLE_W`, `NET_W`, `METHODS`).

### Step 7: ProQuest coverage validation (needs institutional access)

The scripts `proquest_batch.py` and `proquest_fetch.py` scrape subject-facet counts
from ProQuest Dissertations & Theses for 20 sampled US universities and six years
(1960-2010), using a session cookie from a logged-in browser session at your
institution.  Edit `COOKIE_FILE` and `DB_PATH` near the top of `proquest_batch.py`
before running it.  The ProQuest counts are not distributed with this repository.
If you have collected them in `data/proquest_comparison.csv` (columns `University`,
`Year`, `Field`, `ProQuest_Count`, `MG_Count`), `reproduce.py --proquest
data/proquest_comparison.csv` also recomputes the MGP counts from `mg.db` and the
pooled ratios in Tables 4 and 5.

---

## Part 2: The individual scripts

`reproduce.py` runs all of these.  They can also be run one at a time; each takes
`--db <path to mg.db>` and writes into the current directory.

| Paper item | Command |
|---|---|
| (cache used by Figure 2, Tables S10, S12) | `python3 save_top2_probs.py --db mg.db` |
| Figure 1, confusion matrix and cross-validated accuracy | `python3 plot_confusion_matrix.py --db mg.db --seed 42` |
| Figure 2, margin vs. top probability | `python3 plot_margin_grid.py --csv top2_probs.csv` |
| Figure 3, field distribution before and after imputation | `python3 plot_msc_imputation.py --db mg.db` |
| Figure 4, Fisher genealogy | `python3 plot_fisher_network.py --db mg.db` |
| Figure S2, annual degree count | `python3 plot_by_year.py --db mg.db` |
| Figure S3, ROC curves (also writes `roc_predictions.csv`) | `python3 plot_roc_margin.py --db mg.db` |
| Section 3.3, Fuh near-tie example | `python3 example_ambiguous_imputation.py --db mg.db` |
| SQL listings S1-S11 | the files in `queries/` (run with `sqlite3 mg.db < queries/<file>.sql`) |

---

## Files

```
reproduce.py                      one-command reproduction (start here)
requirements.txt
mg.db.sha256                      checksum of the database in the Zenodo deposit
queries/                          SQL listings from the supplement
msc_impute.py                     imputation model (also imported by the figure scripts)
save_top2_probs.py  plot_*.py  example_ambiguous_imputation.py
pymg/  readjson.py  createdb.sql  migrate_schema.sql
language_infer.py  school_canonicalize.py
proquest_fetch.py  proquest_batch.py
```
