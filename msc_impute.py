"""
msc_impute.py  –  Impute missing DEGREES.DEGMSC at the broad-field level.

Signals used:
  1. Thesis title keywords (weighted lexicon per field)
  2. Network: advisor's field, academic siblings' fields, advisees' fields
  3. Year-based prior (suppresses impossible fields, e.g. CS before 1950)

Output table  MSC_IMPUTE:
  DEGREE_ID  INTEGER PRIMARY KEY   – DEGREE_ID (FK) of the DEGREES row
  DEGMGID    INTEGER               – person ID (may have multiple degrees)
  FIELD      CHAR(30)              – imputed broad field name
  CONFIDENCE REAL                  – overall confidence 0–1
  TITLE_W    REAL                  – weight contributed by title signal
  NET_W      REAL                  – weight contributed by network signal
  METHODS    CHAR(60)              – comma-separated list of signals used

Usage:
  python3 msc_impute.py [--db ~/Data/MG/mg.db]
"""

import re
import sqlite3
import argparse
from pathlib import Path
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# Broad field definitions (same as MG.tex Table 2 / query)
# ---------------------------------------------------------------------------
FIELD_MSC: dict[str, set[int]] = {
    "CS":         {68, 93, 94},
    "Statistics": {62},
    "Numerical":  {65},
    "Physics":    {70, 74, 76, 78, 80, 81, 82, 83, 85, 86},
    "OR":         {90},
    "Economics":  {91},
    "Biology":    {92},
    "Education":  {97},
    "Probability":{60},
    "Pure Math":  {0,1,3,5,6,8,11,12,13,14,15,16,17,18,19,20,22,
                   26,28,30,31,32,33,34,35,37,39,40,41,42,43,44,
                   45,46,47,49,51,52,53,54,55,57,58},
}
ALL_FIELDS = list(FIELD_MSC)
# reverse map: MSC code → field name
MSC_TO_FIELD: dict[int, str] = {}
for _f, _codes in FIELD_MSC.items():
    for _c in _codes:
        MSC_TO_FIELD[_c] = _f

# Base prior: approximate fraction of known degrees per field
FIELD_PRIOR: dict[str, float] = {
    "CS":         0.216,
    "Statistics": 0.086,
    "Numerical":  0.040,
    "Physics":    0.108,
    "OR":         0.030,
    "Economics":  0.100,
    "Biology":    0.027,
    "Education":  0.016,
    "Probability":0.037,
    "Pure Math":  0.340,
}

# ---------------------------------------------------------------------------
# Year-based prior multiplier
# ---------------------------------------------------------------------------
def year_factor(field: str, year) -> float:
    """Multiplier ∈ [0, 1] for how plausible `field` is at `year`."""
    try:
        yr = int(year)
    except (TypeError, ValueError):
        return 1.0
    if field == "CS":
        if yr < 1940: return 0.0
        if yr < 1960: return (yr - 1940) / 20 * 0.15
        if yr < 1970: return 0.15 + (yr - 1960) / 10 * 0.85
        return 1.0
    if field == "OR":
        if yr < 1920: return 0.0
        if yr < 1945: return (yr - 1920) / 25 * 0.3
        if yr < 1960: return 0.3 + (yr - 1945) / 15 * 0.7
        return 1.0
    if field == "Numerical":
        if yr < 1890: return 0.0
        if yr < 1940: return (yr - 1890) / 50 * 0.2
        if yr < 1960: return 0.2 + (yr - 1940) / 20 * 0.8
        return 1.0
    if field == "Education":
        if yr < 1920: return 0.0
        if yr < 1960: return (yr - 1920) / 40 * 0.5
        return 1.0
    return 1.0   # all other fields always plausible

# ---------------------------------------------------------------------------
# Title keyword lexicon
# Each entry: (pattern, field, weight)
# Patterns are matched case-insensitively as word-boundary patterns.
# Higher weight = stronger signal for that field.
# ---------------------------------------------------------------------------
_KEYWORDS: list[tuple[str, str, float]] = [
    # ---- CS ----
    (r'algorithm',              "CS", 1.5),
    (r'neural\s+network',       "CS", 3.0),
    (r'deep\s+learning',        "CS", 3.0),
    (r'machine\s+learning',     "CS", 3.0),
    (r'artificial\s+intelligen',"CS", 2.5),
    (r'\bcomputer\b',           "CS", 1.2),
    (r'\bsoftware\b',           "CS", 2.0),
    (r'\bcompil',               "CS", 1.8),
    (r'\bdatabas',              "CS", 2.0),
    (r'\bdata\s+struct',        "CS", 1.8),
    (r'\bcrypt',                "CS", 2.0),
    (r'\bencrypt',              "CS", 2.0),
    (r'\bprotocol\b',           "CS", 1.8),
    (r'\bcomplexity\b',         "CS", 1.5),
    (r'\bNP[- ]hard\b',         "CS", 3.0),
    (r'\bpolynomial\s+time\b',  "CS", 2.5),
    (r'\bparallelism\b',        "CS", 1.8),
    (r'\bdistributed\s+comput', "CS", 2.5),
    (r'\bcloud\s+comput',       "CS", 2.5),
    (r'\boperating\s+system',   "CS", 2.5),
    (r'\bcomputer\s+vision',    "CS", 3.0),
    (r'\bimage\s+(processing|recogn|segment)', "CS", 2.5),
    (r'\bnatural\s+language\s+process', "CS", 3.0),
    (r'\bprogramming\s+language',"CS", 2.0),
    (r'\btransformer\b',        "CS", 1.5),
    (r'\breinforcement\s+learn',"CS", 3.0),
    (r'\bautomata\b',           "CS", 1.8),
    (r'\bformal\s+verif',       "CS", 2.0),
    (r'\bgraph\s+(algorithm|network|mining)', "CS", 1.8),
    (r'\bwireless\b',           "CS", 1.5),
    (r'\bInternet\b',           "CS", 1.5),
    (r'\bcyber',                "CS", 1.8),
    (r'\bsemantic\s+web',       "CS", 2.0),
    # broader CS vocabulary
    (r'\brobot',                "CS", 2.0),   # robot, robotics, robotic
    (r'\bautonomous\s+(agent|robot|system|vehicle)', "CS", 2.5),
    (r'\bintelligent\s+agent',  "CS", 2.5),
    (r'\bmultiagent|\bmulti.agent', "CS", 2.5),
    (r'\bspeech\s+recogn',      "CS", 2.5),
    (r'\bhuman.computer\s+inter',"CS", 2.5),
    (r'\bpassword\b',           "CS", 2.0),
    (r'\bIoT\b|\binternet\s+of\s+things', "CS", 2.5),
    (r'\btype\s+system\b',      "CS", 2.5),
    (r'\btype\s+theory\b',      "CS", 2.5),
    (r'\bprogramming\s+language',"CS", 2.0),
    (r'\bconvolutional',        "CS", 2.5),
    (r'\brecurrent.*network',   "CS", 2.5),
    (r'\bsemantic',             "CS", 1.0),   # weak; covers PL semantics
    (r'\bdata\s+center',        "CS", 2.0),
    (r'\bvirtual.*machine|hypervisor',"CS", 2.0),
    (r'\bembedded\s+system',    "CS", 2.0),
    (r'\breal.time\s+system',   "CS", 2.0),
    (r'\buser\s+interface',     "CS", 2.0),
    (r'\bontolog',              "CS", 2.0),
    (r'\bknowledge\s+(graph|represent|base)', "CS", 2.0),
    (r'\bfault\s+(toleran|detect)', "CS", 1.8),
    (r'\bsoftware\s+(test|verif|engineer)', "CS", 2.0),
    (r'\bvideo\s+(analys|understand|segment)', "CS", 2.0),
    (r'\bspeech\s+synthes',     "CS", 2.5),
    (r'\bsignal\s+process',     "CS", 1.5),

    # ---- Statistics ----
    (r'\bregress',              "Statistics", 1.8),
    (r'\bestim',                "Statistics", 0.8),
    (r'\bBayesian\b',           "Statistics", 2.5),
    (r'\bhypothesis\s+test',    "Statistics", 2.5),
    (r'\bnonparametric',        "Statistics", 2.5),
    (r'\blikelihood\b',         "Statistics", 1.8),
    (r'\bmaximum\s+likelihood', "Statistics", 2.5),
    (r'\bconfidence\s+interval',"Statistics", 2.5),
    (r'\bsampl',                "Statistics", 0.8),
    (r'\bsurvey\s+sampl',       "Statistics", 2.0),
    (r'\bANOVA\b',              "Statistics", 3.0),
    (r'\banalysis\s+of\s+variance', "Statistics", 2.5),
    (r'\bsurvival\s+analy',     "Statistics", 2.5),
    (r'\btime\s+series',        "Statistics", 1.8),
    (r'\bcensored\b',           "Statistics", 2.0),
    (r'\bbootstrap\b',          "Statistics", 2.5),
    (r'\bmixed\s+model',        "Statistics", 2.0),
    (r'\brandom\s+effect',      "Statistics", 2.0),
    (r'\bdiscriminant',         "Statistics", 2.0),
    (r'\bclinical\s+trial',     "Statistics", 2.0),
    (r'\bstatistical\s+(model|infer|test)', "Statistics", 2.0),
    (r'\bprincipal\s+component',"Statistics", 2.0),
    (r'\bbiostatistic',         "Statistics", 3.0),
    (r'\bchi.square',           "Statistics", 2.5),
    (r'\blogistic\s+regress',   "Statistics", 2.5),
    (r'\bdata\s+mining',        "Statistics", 1.5),
    (r'\bcluster\s+analy',      "Statistics", 1.8),

    # ---- Numerical Analysis ----
    (r'\bnumerical\b',          "Numerical", 2.0),
    (r'\bfinite\s+element',     "Numerical", 3.0),
    (r'\bfinite\s+differ',      "Numerical", 2.5),
    (r'\bfinite\s+volume',      "Numerical", 2.5),
    (r'\bspectral\s+method',    "Numerical", 2.5),
    (r'\bmultigrid\b',          "Numerical", 3.0),
    (r'\bdiscretiz',            "Numerical", 2.0),
    (r'\bquadrature\b',         "Numerical", 2.5),
    (r'\bconverg(e|ence|ent)',   "Numerical", 0.8),
    (r'\binterpolat',           "Numerical", 1.2),
    (r'\bnumerical\s+(solut|method|scheme|approx)', "Numerical", 2.5),
    (r'\biterative\s+method',   "Numerical", 2.0),
    (r'\berror\s+(bound|estim|analy)', "Numerical", 1.2),
    (r'\bpreconditio',          "Numerical", 2.0),
    (r'\bsparse\s+matrix',      "Numerical", 2.0),

    # ---- Probability ----
    (r'\bstochastic\b',         "Probability", 2.0),
    (r'\bmartingale\b',         "Probability", 3.0),
    (r'\bMarkov\b',             "Probability", 2.0),
    (r'\brandom\s+walk',        "Probability", 2.5),
    (r'\bBrownian\s+motion',    "Probability", 3.0),
    (r'\bdiffusion\s+process',  "Probability", 2.5),
    (r'\bstochastic\s+differential', "Probability", 3.0),
    (r'\bLévy\b|\bLevy\b',      "Probability", 2.5),
    (r'\brandom\s+matrix',      "Probability", 2.5),
    (r'\bpercolation\b',        "Probability", 2.5),
    (r'\bbranching\s+process',  "Probability", 2.5),
    (r'\brenewal\s+theory',     "Probability", 2.5),
    (r'\bcentral\s+limit',      "Probability", 2.5),
    (r'\blaw\s+of\s+large\s+numbers', "Probability", 2.5),
    (r'\brandom\s+field',       "Probability", 2.0),
    (r'\bGaussian\s+process',   "Probability", 2.0),
    (r'\bprobability\s+measure',"Probability", 2.0),
    (r'\binteracting\s+particle',"Probability", 2.5),

    # ---- Physics / Applied ----
    (r'\bfluid\b',              "Physics", 1.5),
    (r'\bmechanics\b',          "Physics", 0.8),
    (r'\bquantum\b',            "Physics", 1.8),
    (r'\belectromagn',          "Physics", 2.0),
    (r'\bwave\s+(equation|propagat)', "Physics", 1.5),
    (r'\bradiation\b',          "Physics", 1.5),
    (r'\bthermodynam',          "Physics", 2.0),
    (r'\bheat\s+(equat|transfer|conduct)', "Physics", 1.5),
    (r'\belastic',              "Physics", 1.2),
    (r'\bplasticit',            "Physics", 1.8),
    (r'\brelativity\b',         "Physics", 2.5),
    (r'\bgravitation',          "Physics", 2.0),
    (r'\bastrophys',            "Physics", 3.0),
    (r'\bastrono',              "Physics", 2.5),
    (r'\bgeophys',              "Physics", 3.0),
    (r'\boptics\b',             "Physics", 2.0),
    (r'\bphoton\b',             "Physics", 2.0),
    (r'\bplasma\b',             "Physics", 2.5),
    (r'\bnuclear\b',            "Physics", 1.5),
    (r'\bparticle\s+physics',   "Physics", 2.5),
    (r'\bmagnet',               "Physics", 1.5),
    (r'\bNavier.Stokes',        "Physics", 3.0),
    (r'\bsolid\s+mechanics',    "Physics", 2.5),
    (r'\bcontinuum',            "Physics", 1.8),
    (r'\bviscous\b',            "Physics", 2.0),
    (r'\bturbulence\b',         "Physics", 2.5),

    # ---- Operations Research ----
    (r'\boptimization\b|\boptimisation\b', "OR", 1.5),
    (r'\blinear\s+programming', "OR", 3.0),
    (r'\binteger\s+program',    "OR", 3.0),
    (r'\bcombinatorial\s+optim',"OR", 2.5),
    (r'\bscheduling\b',         "OR", 2.0),
    (r'\brouting\b',            "OR", 1.8),
    (r'\bsupply\s+chain',       "OR", 2.5),
    (r'\blogistics\b',          "OR", 2.0),
    (r'\binventory\b',          "OR", 2.5),
    (r'\bqueueing\b|\bqueuing\b',"OR", 2.5),
    (r'\bMarkov\s+decision',    "OR", 2.5),
    (r'\bdynamic\s+programming',"OR", 2.5),
    (r'\bnetwork\s+flow',       "OR", 2.5),
    (r'\btraveling\s+salesm',   "OR", 3.0),
    (r'\bfacility\s+locat',     "OR", 2.5),
    (r'\bknapsack\b',           "OR", 2.5),
    (r'\bstochastic\s+optim',   "OR", 2.0),
    (r'\brobust\s+optim',       "OR", 2.0),
    (r'\bconvex\s+optim',       "OR", 1.8),

    # ---- Economics / Game Theory ----
    (r'\bgame\s+theory',        "Economics", 3.0),
    (r'\bauction\b',            "Economics", 2.5),
    (r'\bmechanism\s+design',   "Economics", 3.0),
    (r'\bwelfare\b',            "Economics", 1.8),
    (r'\butility\b',            "Economics", 1.5),
    (r'\bequilibrium\b',        "Economics", 1.5),
    (r'\basset\s+pric',         "Economics", 2.5),
    (r'\bportfolio\b',          "Economics", 2.0),
    (r'\bfinancial\b|\bfinance\b', "Economics", 1.5),
    (r'\boption\s+pric',        "Economics", 2.5),
    (r'\bBlack.Scholes',        "Economics", 3.0),
    (r'\bcontract\s+theory',    "Economics", 2.5),
    (r'\bmoral\s+hazard',       "Economics", 3.0),
    (r'\badverse\s+select',     "Economics", 3.0),
    (r'\bwage\b',               "Economics", 1.5),
    (r'\blabor\s+market|\blabour\s+market', "Economics", 2.0),
    (r'\boligopol',             "Economics", 2.5),
    (r'\bconsumer\b',           "Economics", 1.2),
    (r'\bproduction\s+function',"Economics", 2.0),
    (r'\bsocial\s+choice',      "Economics", 2.5),
    (r'\bvoting\b',             "Economics", 1.8),
    (r'\bstrategic\b',          "Economics", 1.2),
    (r'\bmonopol',              "Economics", 2.0),
    # broader economics vocabulary
    (r'\beconom',               "Economics", 1.8),   # economic, economics, econometric
    (r'\beconometri',           "Economics", 3.0),   # very specific
    (r'\bmacroeconom',          "Economics", 3.0),
    (r'\bmicroeconom',          "Economics", 3.0),
    (r'\bfiscal\b',             "Economics", 2.5),
    (r'\bmonetary\s+polic',     "Economics", 2.5),
    (r'\binflation\b',          "Economics", 2.0),
    (r'\bexchange\s+rate',      "Economics", 2.5),
    (r'\binterest\s+rate',      "Economics", 2.0),
    (r'\bwage\b',               "Economics", 1.8),
    (r'\bearning',              "Economics", 1.5),
    (r'\bhuman\s+capital',      "Economics", 2.5),
    (r'\bunemployment',         "Economics", 2.5),
    (r'\bbargaining\b',         "Economics", 2.0),
    (r'\bcollective\s+bargain', "Economics", 2.5),
    (r'\binternational\s+trade',"Economics", 2.5),
    (r'\btariff\b',             "Economics", 2.5),
    (r'\btaxation\b|\btax\s+polic', "Economics", 2.0),
    (r'\bpublic\s+finance',     "Economics", 2.5),
    (r'\bmarket\s+effici',      "Economics", 2.5),
    (r'\binformation\s+asymm',  "Economics", 2.5),
    (r'\brisk\s+(aversion|premium)', "Economics", 2.0),
    (r'\bderivative.*pric|\boption.*pric', "Economics", 2.0),
    (r'\bventure\s+capital',    "Economics", 2.5),
    (r'\bNash\s+equilibri',     "Economics", 3.0),  # covers plural equilibria
    (r'\bGDP\b|\bGNP\b',        "Economics", 2.5),
    (r'\bpoverty\b',            "Economics", 1.8),
    (r'\binequality\b',         "Economics", 1.5),
    (r'\binsurance\b',          "Economics", 1.5),
    (r'\bmoral\b.*hazard|\bhazard.*moral', "Economics", 2.5),
    (r'\bsearch\s+unemploy|\bunemploy.*search', "Economics", 2.5),
    (r'\bpanel\s+(data|regression)', "Economics", 2.0),
    (r'\binstrumental\s+variable|\bIV\s+estimat', "Economics", 2.5),
    (r'\bdifference.in.difference', "Economics", 2.5),
    (r'\bnatural\s+experiment', "Economics", 2.0),
    (r'\bbehavioral\s+econom',  "Economics", 3.0),
    (r'\bexperimental\s+econom',"Economics", 3.0),
    (r'\bpolitical\s+econom',   "Economics", 2.5),

    # ---- Biology ----
    (r'\bgenom',                "Biology", 2.5),
    (r'\bgenetic',              "Biology", 2.0),
    (r'\bprotein\b',            "Biology", 2.0),
    (r'\bDNA\b',                "Biology", 2.5),
    (r'\bRNA\b',                "Biology", 2.5),
    (r'\bspecies\b',            "Biology", 1.5),
    (r'\bevolution',            "Biology", 1.5),
    (r'\becolog',               "Biology", 2.0),
    (r'\bepidemiolog',          "Biology", 2.5),
    (r'\bpopulation\s+dynam',   "Biology", 2.0),
    (r'\bphylogenet',           "Biology", 2.5),
    (r'\bcell\s+(biology|divis)',"Biology", 2.5),
    (r'\bcancer\b',             "Biology", 2.0),
    (r'\bvirus\b|\bviral\b',    "Biology", 2.0),
    (r'\bbacteria\b',           "Biology", 2.0),
    (r'\bneuroscience\b',       "Biology", 2.5),
    (r'\bbrain\b',              "Biology", 1.5),
    (r'\bbioinformat',          "Biology", 3.0),
    (r'\bmolecular\s+biol',     "Biology", 2.5),
    (r'\bbiochemistry\b',       "Biology", 2.5),
    (r'\bsequence\s+align',     "Biology", 2.5),

    # ---- Math Education ----
    (r'\bmathematics\s+education',"Education", 3.0),
    (r'\bmath\s+education',     "Education", 3.0),
    (r'\bpedagogie\b|\bpedagog',"Education", 2.5),
    (r'\bteaching\s+math',      "Education", 3.0),
    (r'\bcurriculum\b',         "Education", 2.0),
    (r'\bclassroom\b',          "Education", 1.5),
    (r'\bmath\s+anxiety',       "Education", 3.0),
    (r'\bSTEM\s+educat',        "Education", 2.5),
    (r'\bteacher\s+train',      "Education", 2.0),
    (r'\bschool\s+math',        "Education", 2.5),
    (r'\bmath\s+(learn|teach|understand)', "Education", 2.0),
    (r'\bproblem.solv',         "Education", 1.0),

    # ---- Pure Math (strong signals when other fields don't fire) ----
    (r'\balgebraic\s+geometr',  "Pure Math", 2.0),
    (r'\btopolog',              "Pure Math", 1.5),
    (r'\bmanifold\b',           "Pure Math", 2.0),
    (r'\bcohomolog',            "Pure Math", 2.5),
    (r'\bhomolog',              "Pure Math", 1.5),
    (r'\bhomotop',              "Pure Math", 2.5),
    (r'\bGalois\b',             "Pure Math", 2.5),
    (r'\bnumber\s+field',       "Pure Math", 2.0),
    (r'\bL-function',           "Pure Math", 2.5),
    (r'\bautomorphic\b',        "Pure Math", 2.5),
    (r'\brepresentation\s+theory',"Pure Math", 2.0),
    (r'\bharmonic\s+analy',     "Pure Math", 2.0),
    (r'\bBanach\s+space',       "Pure Math", 2.5),
    (r'\bHilbert\s+space',      "Pure Math", 2.0),
    (r'\boperator\s+algebra',   "Pure Math", 2.5),
    (r'\bC\*-algebra',          "Pure Math", 2.5),
    (r'\bRiemann\b',            "Pure Math", 1.8),
    (r'\bergodic',              "Pure Math", 1.8),
    (r'\bLie\s+group',          "Pure Math", 2.5),
    (r'\bnumber\s+theory',      "Pure Math", 2.5),
    (r'\bellipt',               "Pure Math", 1.0),
    (r'\bdifferential\s+geom',  "Pure Math", 2.0),
    (r'\bcommutative\s+ring',   "Pure Math", 2.5),
    (r'\babelian\b',            "Pure Math", 1.5),
    (r'\bprime\b',              "Pure Math", 1.0),
    (r'\bmodular\s+form',       "Pure Math", 2.5),
    (r'\bHodge\b',              "Pure Math", 2.5),
]

# Compile patterns once
_COMPILED = [
    (re.compile(pat, re.IGNORECASE), field, weight)
    for pat, field, weight in _KEYWORDS
]

def _zero_scores() -> dict[str, float]:
    return {f: 0.0 for f in ALL_FIELDS}

def title_scores(title: str) -> dict[str, float]:
    """Return raw (unnormalized) keyword scores per field."""
    scores = _zero_scores()
    if not title:
        return scores
    for pat, field, weight in _COMPILED:
        if pat.search(title):
            scores[field] += weight
    return scores

def normalize(d: dict[str, float]) -> dict[str, float]:
    total = sum(d.values())
    if total == 0:
        return d
    return {k: v / total for k, v in d.items()}

# ---------------------------------------------------------------------------
# Main imputation
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="~/Data/MG/mg.db")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # ------------------------------------------------------------------ #
    # 1. Load known MSC data into memory
    # ------------------------------------------------------------------ #
    print("Loading known MSC data ...", flush=True)
    # person → list of (broad_field, year)  [may have multiple degrees]
    person_fields: dict[int, list[str]] = defaultdict(list)
    for mgid, msc, year in conn.execute(
        "SELECT DEGMGID, DEGMSC, DEGYEAR FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall():
        field = MSC_TO_FIELD.get(msc, "Pure Math")
        person_fields[mgid].append(field)

    # ------------------------------------------------------------------ #
    # 2. Load student-advisor graph
    # ------------------------------------------------------------------ #
    print("Loading advisor/student graph ...", flush=True)
    student_to_advisors: dict[int, list[int]] = defaultdict(list)
    advisor_to_students: dict[int, list[int]] = defaultdict(list)
    for advisor, student in conn.execute("SELECT ADVISOR, STUDENT FROM STUDENTS").fetchall():
        student_to_advisors[student].append(advisor)
        advisor_to_students[advisor].append(student)

    # ------------------------------------------------------------------ #
    # 3. Create output table
    # ------------------------------------------------------------------ #
    conn.executescript("""
        DROP TABLE IF EXISTS MSC_IMPUTE;
        CREATE TABLE MSC_IMPUTE (
            DEGREE_ID  INTEGER PRIMARY KEY REFERENCES DEGREES(DEGREE_ID),
            DEGMGID    INTEGER,
            FIELD      CHAR(30),
            CONFIDENCE REAL,
            TITLE_W    REAL,
            NET_W      REAL,
            METHODS    CHAR(60)
        );
    """)
    conn.commit()

    # ------------------------------------------------------------------ #
    # 4. Insert observed MSC records with confidence = 1.0
    # ------------------------------------------------------------------ #
    print("Inserting observed MSC records ...", flush=True)
    obs_rows = conn.execute(
        "SELECT DEGREE_ID, DEGMGID, DEGMSC FROM DEGREES WHERE DEGMSC IS NOT NULL"
    ).fetchall()
    obs_batch = []
    for rowid, mgid, msc in obs_rows:
        field = MSC_TO_FIELD.get(msc, "Pure Math")
        obs_batch.append((rowid, mgid, field, 1.0, 0.0, 0.0, "observed"))
        if len(obs_batch) >= 5000:
            conn.executemany("INSERT INTO MSC_IMPUTE VALUES (?,?,?,?,?,?,?)", obs_batch)
            conn.commit()
            obs_batch.clear()
    if obs_batch:
        conn.executemany("INSERT INTO MSC_IMPUTE VALUES (?,?,?,?,?,?,?)", obs_batch)
        conn.commit()
    print(f"  {len(obs_rows):,} observed records inserted.", flush=True)

    # ------------------------------------------------------------------ #
    # 5. Process missing-MSC degrees
    # ------------------------------------------------------------------ #
    rows = conn.execute(
        "SELECT DEGREE_ID, DEGMGID, TITLE, DEGYEAR FROM DEGREES WHERE DEGMSC IS NULL"
    ).fetchall()
    print(f"Imputing {len(rows):,} missing MSC records ...", flush=True)

    batch = []
    stats = Counter()

    for rowid, mgid, title, year in rows:

        methods_used = []

        # ---- title signal ----
        t_raw = title_scores(title or "")
        t_total = sum(t_raw.values())
        title_strength = min(t_total / 8.0, 1.0)   # saturates at 8 total weight

        # ---- network signal ----
        net_votes: dict[str, float] = _zero_scores()
        advisor_ids  = student_to_advisors.get(mgid, [])
        student_ids  = advisor_to_students.get(mgid, [])

        # advisor(s)
        for adv in advisor_ids:
            for f in person_fields.get(adv, []):
                net_votes[f] += 1.0
        if any(net_votes[f] > 0 for f in ALL_FIELDS):
            methods_used.append("advisor")

        # siblings (students of same advisor)
        sib_count = 0
        for adv in advisor_ids:
            for sib in advisor_to_students.get(adv, []):
                if sib == mgid:
                    continue
                for f in person_fields.get(sib, []):
                    net_votes[f] += 0.35
                    sib_count += 1
        if sib_count:
            methods_used.append("siblings")

        # students (if person is also an advisor)
        stu_count = 0
        for stu in student_ids:
            for f in person_fields.get(stu, []):
                net_votes[f] += 0.6
                stu_count += 1
        if stu_count:
            methods_used.append("students")

        net_total = sum(net_votes.values())
        net_strength = min(net_total / 3.0, 1.0)  # saturates after 3 votes

        # ---- combine ----
        # Weight title vs. network by their respective strengths
        tw = title_strength
        nw = net_strength
        total_w = tw + nw

        combined = _zero_scores()
        if total_w > 0:
            for f in ALL_FIELDS:
                t_contrib = (t_raw[f] / t_total) if t_total > 0 else 0.0
                n_contrib = (net_votes[f] / net_total) if net_total > 0 else 0.0
                combined[f] = (tw * t_contrib + nw * n_contrib) / total_w
        else:
            # Fall back to base prior
            combined = dict(FIELD_PRIOR)
            methods_used.append("prior_only")

        # ---- year-based multiplier ----
        for f in ALL_FIELDS:
            combined[f] *= year_factor(f, year)

        # Re-normalize after year adjustment
        combined = normalize(combined)
        if not combined:
            combined = dict(FIELD_PRIOR)

        # ---- pick best field ----
        best_field = max(combined, key=combined.get)
        confidence  = combined[best_field]

        # Confidence discount when only prior is available
        if total_w == 0:
            confidence *= 0.3

        if title_strength > 0:
            methods_used.insert(0, "title")

        batch.append((
            rowid, mgid, best_field,
            round(confidence, 4),
            round(tw, 4),
            round(nw, 4),
            ",".join(methods_used) or "none",
        ))
        stats["total"] += 1

        if len(batch) >= 5000:
            conn.executemany(
                "INSERT INTO MSC_IMPUTE VALUES (?,?,?,?,?,?,?)", batch
            )
            conn.commit()
            batch.clear()
            print(f"  {stats['total']:>7,} / {len(rows):,}", flush=True)

    if batch:
        conn.executemany("INSERT INTO MSC_IMPUTE VALUES (?,?,?,?,?,?,?)", batch)
        conn.commit()

    print(f"\nDone. {stats['total']:,} records imputed.")

    # ---- summary ----
    print("\nImputed field distribution:")
    for field, n in conn.execute("""
        SELECT FIELD, COUNT(*) n FROM MSC_IMPUTE
        GROUP BY FIELD ORDER BY n DESC
    """).fetchall():
        print(f"  {field:<20} {n:>8,}")

    print("\nConfidence bands:")
    for lo, hi, label in [(0.6, 1.0, "high  (>=0.60)"),
                          (0.4, 0.6, "med   (0.4-0.6)"),
                          (0.0, 0.4, "low   (<0.40)")]:
        n = conn.execute(
            f"SELECT COUNT(*) FROM MSC_IMPUTE WHERE CONFIDENCE >= {lo} AND CONFIDENCE < {hi+0.001}"
        ).fetchone()[0]
        print(f"  {label}: {n:>8,}")

    print("\nSignal usage (METHODS):")
    for methods, n in conn.execute("""
        SELECT METHODS, COUNT(*) n FROM MSC_IMPUTE
        GROUP BY METHODS ORDER BY n DESC LIMIT 15
    """).fetchall():
        print(f"  {methods:<45} {n:>7,}")

    conn.close()

if __name__ == "__main__":
    main()
