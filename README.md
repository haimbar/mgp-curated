# Reproducibility Guide

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20682944.svg)](https://doi.org/10.5281/zenodo.20682944)

**Paper:** Haim Bar, Neil A. Spencer, Xingche Guo, Elizabeth D. Schifano, and Jun Yan (2025).
"From Genealogy to Research-Ready Data: Curating, Validating, and Applying the Mathematics Genealogy Project."
*Journal of Data Science.*

**Zenodo deposit (code + data):** https://doi.org/10.5281/zenodo.20682944

---

This document describes how to reproduce the database curation and all
results reported in the paper.  All code is contained in this
repository.  No files need to be duplicated: the tar-zip archive for
distribution will be assembled from the file lists at the end of each
section.

Throughout this guide, `<code_dir>` refers to the directory where you
cloned or unpacked this repository, and `<data_dir>` refers to any
writable directory where you want to store the raw downloaded data and
the SQLite database (these can be the same directory if you prefer).

---

## Part 1 — Database curation

The goal of this part is to produce `mg.db`, a SQLite database that
consolidates the raw Math Genealogy Project (MGP) data and enriches it
with imputed broad-field codes, canonical school names, and inferred
dissertation languages.  The steps must be run in the order given.

### Software requirements

Install Python 3.10 or later and the following packages:

```bash
pip install requests langdetect rapidfuzz beautifulsoup4
```

`beautifulsoup4` is needed only for the ProQuest validation step
(Step 6).

Install the `pymathgene` package from this repository:

```bash
cd <code_dir>/pymg
pip install -e .
```

### Accounts and credentials

| Requirement | Purpose | Where to register |
|---|---|---|
| MGP account (email + password) | Authenticate to the MGP REST API to download records | <https://mathgenealogy.org> (free) |
| Institutional ProQuest subscription | Access ProQuest Dissertations & Theses for the coverage-validation step | Through your institution's library |

---

### Step 1 — Download raw records from the MGP API

The MGP API returns one JSON record per mathematician.  Records are
downloaded incrementally and appended to `allmgdat.txt` in `<data_dir>`.
Each login session issues a JSON Web Token that expires after a fixed
period; you may need to re-authenticate and resume from the last
downloaded ID.

Run the following in a Python session:

```python
from pymathgene import get_mg_data
import os

# Choose or create the data directory.
# On headless systems the function prompts for a path on the command line;
# on systems with a graphical display it opens a directory-chooser dialog.
datadir = get_mg_data.init()
os.chdir(datadir)

# Find the ID of the last record already downloaded (returns 0 if starting fresh).
lastID = get_mg_data.lastID(datadir)

# Choose how many records to download in this session (maximum 5,000 per token).
n = get_mg_data.max_recs()

# Authenticate with your MGP email and password.
cred  = get_mg_data.getlogin()
token = get_mg_data.login(cred)   # raises RuntimeError if authentication fails

# Download records with IDs in [lastID+1, lastID+n].
# Gaps (HTTP 502) are tolerated up to max_fails; gaps are normal because
# MGP IDs are not contiguous.
res = get_mg_data.query_loop(
    cred, token,
    first    = lastID + 1,
    last     = lastID + n + 1,
    max_fails = 20,
)
print(res)   # {'total': <records found>, 'fails': <gaps skipped>}
```

Repeat (re-authenticating each session) until the download is complete.
The complete MGP database contains approximately 300,000 records; plan
for multiple sessions.

**Files needed:** `pymg/`

---

### Step 2 — Parse raw JSON into flat import files

The raw file `allmgdat.txt` contains one JSON record per line.  This
step parses it into pipe-delimited flat files that SQLite can import
directly.

```python
from pymathgene import get_mg_data
import os

datadir = "<data_dir>"   # same directory as Step 1
os.chdir(datadir)

flds = get_mg_data.parse_batch("allmgdat.txt")
get_mg_data.error_checking(datadir, flds)
```

`parse_batch` produces:

| File | Contents |
|---|---|
| `persons.txt` | MGID, family name, given name, other names |
| `degrees.txt` | MGID, degree type, MSC code, year, school, country, title |
| `students.txt` | advisor MGID, student MGID (raw, may contain duplicates) |
| `studuniq.txt` | `students.txt` with duplicate pairs removed |

`error_checking` prints a summary of anomalies (ambiguous years,
unknown countries, etc.) that are handled in downstream steps.

The standalone script `readjson.py` performs the same parsing without
the `pymathgene` package and may be used as an alternative.

**Files needed:** `pymg/`, `readjson.py`

---

### Step 3 — Create the SQLite database

```bash
cd <data_dir>
sqlite3 mg.db < <code_dir>/createdb.sql
```

`createdb.sql` does the following in order:

1. Creates tables: `MSC`, `PERSONS`, `DEGREES`, `STUDENTS`, `FIELDS`,
   `SCORES`.
2. Bulk-imports the flat files produced in Step 2.
3. Applies a small set of manual corrections for malformed year values
   (e.g. `'Aug. 2018'` → `2018`).

The `MSC` table requires a pipe-delimited file named `msc` in
`<data_dir>` containing the MSC 2020 classification codes and names.
This file is not distributed here; it can be obtained from
<https://msc2020.org>.

**Files needed:** `createdb.sql`

---

### Step 4 — Infer dissertation language

```bash
python3 <code_dir>/language_infer.py --db <data_dir>/mg.db
```

Creates the `LANGUAGE_DETECT` table in `mg.db`.  Each degree row is
assigned a BCP-47 language code, a confidence score (0–1), and a
detection method.  The method combines the `langdetect` library (seeded
with 42 for reproducibility) with a country-to-language prior that
down-weights implausible language–country combinations.  Bilingual
titles (detected as containing two languages) are flagged separately.

To process only a subset for testing:

```bash
python3 <code_dir>/language_infer.py --db <data_dir>/mg.db --limit 1000
```

**Files needed:** `language_infer.py`

---

### Step 5 — Canonicalize school names

```bash
python3 <code_dir>/school_canonicalize.py
```

The `DEGREES` table contains 2,867 distinct school-name strings; many
are variant spellings of the same institution.  This script:

1. Groups names by country.
2. Computes pairwise `fuzz.ratio` (character-level edit distance via
   `rapidfuzz`) within each country group.
3. Auto-accepts pairs with score ≥ 92; sends pairs scoring 75–91 to a
   manual-review CSV.
4. Applies two guards against false positives: a US-state blocker
   (names that mention different US states are never merged) and a
   token-distinctiveness guard.
5. Uses advisor co-occurrence as a cross-language signal: if the same
   advisor appears at two schools with similar names (score ≥ 70), the
   pair is auto-accepted even across country groups.
6. Applies a curated alias table for major cross-language pairs that
   share no common tokens (e.g. local-language vs. English names).
7. Creates the `SCHOOL_CANONICAL` table mapping every raw name to a
   canonical name, match method, and confidence score.
8. Writes uncertain pairs to `school_review.csv`.

After the first run, inspect `school_review.csv` and add confirmed
pairs to the alias table at the top of the script, then re-run.

The script reads and writes `mg.db` from the path hard-coded near the
top of the file (`DB_PATH`); edit that variable to point to
`<data_dir>/mg.db` before running.

**Files needed:** `school_canonicalize.py`

---

### Step 6 — Impute missing broad-field (MSC) codes

```bash
python3 <code_dir>/msc_impute.py --db <data_dir>/mg.db
```

Approximately 41 % of degree records have no MSC code.  This script
imputes a broad field (Mathematics, Statistics, CS, Physics, Economics,
Biology, OR, Probability, Numerical, Education) for those records using
three signals:

| Signal | Description |
|---|---|
| Title keywords | Weighted lexicon per field; terms matched against the thesis title |
| Network | Advisor's field, academic siblings' fields, advisees' fields |
| Year prior | Suppresses fields that did not exist before a given year (e.g. CS before 1950) |

The three signals are combined into a posterior probability over fields.
Results are written to the `MSC_IMPUTE` table with columns:
`DEGROWID`, `DEGMGID`, `FIELD`, `CONFIDENCE`, `TITLE_W`, `NET_W`,
`METHODS`.

**Files needed:** `msc_impute.py`

---

### Step 7 — ProQuest coverage validation (requires institutional access)

This step validates MGP coverage against ProQuest Dissertations &
Theses for 20 sampled US universities across six decades (1960–2010 in
ten-year steps).  It is needed to reproduce Tables 5–6 and the
coverage discussion in the paper.

#### Why this step is difficult to automate

ProQuest does not provide a public API.  The scripts scrape
subject-facet counts from ProQuest search results pages using an
authenticated browser session.  Automation requires a valid session
cookie extracted from a live, logged-in browser session at your
institution.  Cookies typically expire within hours; a new cookie must
be obtained for each session.

#### How to obtain the session cookie

1. Log in to ProQuest Dissertations & Theses through your institution's
   library portal.
2. Open your browser's developer tools:
   - **Safari**: Develop → Show Web Inspector → Network tab
   - **Chrome / Edge**: F12 → Network tab
   - **Firefox**: F12 → Network tab
3. Navigate to any ProQuest results page (e.g. search for any term).
4. In the Network tab, click any request sent to `proquest.com`.
5. In the request headers, locate the `Cookie` header.
6. Copy the entire value of that header (it is a long string of
   `name=value` pairs separated by semicolons).
7. Paste the copied value into a plain-text file named
   `proquest_cookies.txt` in `<code_dir>` (one line, no label or
   prefix).

#### Running the scripts

```bash
# Fetch subject-facet counts for all 20 universities × 6 decades.
# Raw responses are saved to <code_dir>/data/proquest/<University>_<Year>.tsv
# Combined output (MG vs. ProQuest): <code_dir>/data/proquest_comparison.csv
python3 <code_dir>/proquest_batch.py

# Fetch counts for a single ProQuest results URL (useful for testing
# or for manually sampling additional universities).
python3 <code_dir>/proquest_fetch.py "https://www.proquest.com/pqdtglobal/results/..."
```

Before running, edit the `DB_PATH` and `COOKIE_FILE` variables near the
top of `proquest_batch.py` to point to your `mg.db` and
`proquest_cookies.txt` files.

The reference output (`data/proquest_comparison.csv`) is included in
the repository so that readers who cannot access ProQuest can still
reproduce Part 2 without repeating this step.

**Files needed:** `proquest_batch.py`, `proquest_fetch.py`,
`data/proquest_comparison.csv`

---

## Part 2 — Reproducing paper results

This part assumes `mg.db` is fully built (all six curation steps
complete).  All scripts accept a `--db` flag; the default is
`~/Data/MG/mg.db`, so pass `--db <data_dir>/mg.db` if your database is
elsewhere.  Scripts that read CSV files (Step 2.2 onward) expect those
files in the current working directory unless otherwise noted.

---

### Step 2.1 — Descriptive statistics (Table 1)

```bash
python3 <code_dir>/descriptives.py
```

Queries `mg.db` and prints counts of persons, degrees, advisor–student
pairs, degrees missing a title, and degrees missing an MSC code.  Edit
the `datdir` variable (or the `localdirdata` file it reads) to point to
`<data_dir>` before running.

**Files needed:** `descriptives.py`

---

### Step 2.2 — Pre-computation: top-2 predicted probabilities

Several figures plot the top two predicted field probabilities for every
imputed record.  Generate the cache file once before running those
figures:

```bash
python3 <code_dir>/save_top2_probs.py --db <data_dir>/mg.db
```

Output: `top2_probs.csv` in the current directory (columns: `MGID`,
`FIELD1`, `P1`, `FIELD2`, `P2`).

**Files needed:** `save_top2_probs.py`

---

### Step 2.3 — Figures

#### Figure 1 — Imputation confusion matrix

> **Paper location:** Main text, Figure 1 (`fig:msc_cm`)
> **Caption:** *Confusion matrix for the MSC broad-field imputation,
> estimated by 10-fold cross-validation on the subset of degree records
> with known MSC codes.*

```bash
python3 <code_dir>/plot_confusion_matrix.py --db <data_dir>/mg.db --seed 42
```

The `--seed 42` flag ensures reproducibility.  The script holds out a
random 10 % of known-MSC records, rebuilds the network signal from the
remaining 90 %, runs imputation, and plots the 10 × 10 confusion matrix.

Output: `msc_confusion_matrix.pdf`

**Files needed:** `plot_confusion_matrix.py`

---

#### Figure 2 — Confidence margin vs. top probability (hexbin grid)

> **Paper location:** Main text, Figure 2 (`fig:top2scatter`)
> **Caption:** *Confidence margin $p_1 - p_2$ vs. highest predicted
> probability $p_1$ for four broad fields.*

```bash
python3 <code_dir>/plot_margin_grid.py --csv top2_probs.csv
```

Reads `top2_probs.csv` generated in Step 2.2.  Also writes per-field
CSV files (`margin_<field>.csv`) for downstream use.

Output: `margin_grid.pdf`

**Files needed:** `plot_margin_grid.py`

---

#### Figure 3 — Broad-field distribution before and after imputation

> **Paper location:** Main text, Figure 3 (`fig:mscimpute_compare`)
> **Caption:** *Broad-field distribution by decade, before and after
> MSC imputation.*

```bash
python3 <code_dir>/plot_msc_imputation.py --db <data_dir>/mg.db
```

Produces two side-by-side 100 % stacked bar charts: known MSC records
only (left) and known + imputed records (right), broken down by decade.

Output: `msc_imputation_comparison.pdf`

**Files needed:** `plot_msc_imputation.py`

---

#### Figure 4 — Fisher academic genealogy network

> **Paper location:** Main text, Figure 4 (`fig:fisher_network`)
> **Caption:** *Academic genealogy of Ronald A. Fisher (MGP ID 46924),
> three generations of ancestors and two generations of descendants.*

```bash
python3 <code_dir>/plot_fisher_network.py --db <data_dir>/mg.db
```

Lays out ancestors (generations −1 to −3) above Fisher in a symmetric
arc and descendants (generations +1 and +2) below in nested
half-circles.  Generation +3 is shown as pie-chart summary nodes, one
per Gen +2 parent, with area proportional to student count and slices
showing field distribution.

Output: `fisher_network.pdf`

**Files needed:** `plot_fisher_network.py`

---

#### Supplementary Figure S2 — Annual degree count (log scale)

> **Paper location:** Supplementary material, Figure S2
> **Caption:** *Annual degree count on a log$_{10}$ scale.*

```bash
python3 <code_dir>/plot_by_year.py --db <data_dir>/mg.db
```

Output: `countbyyear.pdf`

**Files needed:** `plot_by_year.py`

---

#### Supplementary Figure S3 — ROC curves

> **Paper location:** Supplementary material, Figure S3 (`fig:roc_margin`)
> **Caption:** *ROC curves for four broad fields, computed by varying
> the confidence margin $p_1 - p_2$ as the decision threshold.*

```bash
python3 <code_dir>/plot_roc_margin.py --db <data_dir>/mg.db
```

Ground truth comes from degree records with observed MSC codes; the
imputation model is applied to those records with their MSC hidden.
Also writes `roc_predictions.csv` (columns: `MGID`, `TRUE_FIELD`,
`PRED_FIELD`, `P1`, `P2`, `MARGIN`).

Output: `roc_margin.pdf`

**Files needed:** `plot_roc_margin.py`

---

### Step 2.4 — Ambiguous-imputation example (Section 4.2)

```bash
python3 <code_dir>/example_ambiguous_imputation.py --db <data_dir>/mg.db
```

Reproduces the Cheng-Der Fuh (MGID 194) near-tie example discussed in
the paper: the thesis *"The Bootstrap Method for Markov Chains"* (1989)
receives competing keyword signals (bootstrap → Statistics weight 2.5;
Markov → Probability weight 2.0) and a network that leans toward
Probability, producing a near-tie of Probability 0.530 vs.
Statistics 0.343.

**Files needed:** `example_ambiguous_imputation.py`

---

## File list for the reproducibility archive

### Part 1 — curation

```
pymg/
    pyproject.toml
    src/pymathgene/__init__.py
    src/pymathgene/get_mg_data.py
readjson.py
createdb.sql
language_infer.py
school_canonicalize.py
msc_impute.py
proquest_fetch.py
proquest_batch.py
data/proquest_comparison.csv
```

### Part 2 — results

```
descriptives.py
save_top2_probs.py
plot_confusion_matrix.py
plot_margin_grid.py
plot_msc_imputation.py
plot_fisher_network.py
plot_by_year.py
plot_roc_margin.py
example_ambiguous_imputation.py
```
