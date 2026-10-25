## Create a database from the Math Geneology website

###  Data download

To set up the data folder, run

```
from pymathgene import get_mg_data
datadir = get_mg_data.init()
get_mg_data.os.chdir(datadir)
```

This is the folder where we will keep the raw files downloaded from mathgenealogy.org, as well as the parsed files, and the sqlite3 database.
The init function will try to open a directory selection dialog if a graphical display it available. Otherwise, it will ask for the user's input from the command line.

The next step is to check the latest ID downloaded. To do that, run

```
lastID = get_mg_data.lastID(datadir)
```

If the returned value is None, there may be a problem with the data file, or this could be a database rebuild, in which case, setlastID to 0.

Next, enter the number of records to download. The maximum number is 5000 (because the token has a timeout).

```
n = get_mg_data.max_recs()
```

Get the mathgenealogy.org credentials which are used to connect to the API, and obtain a token for a session:

```
cred = get_mg_data.getlogin()
token = get_mg_data.login(cred)
```

If a token is returned, loop of the range of requested IDs. Otherwise, check why no token was returned (maybe the website, ot the API is down?)

Next, get new data from the API:

```
res = get_mg_data.query_loop(cred, token, first=lastID+1, lastlastID+n+1, max_fails=20)
print(res)
```

The res variable is a dictionary with two entries: total (number of found records) and fails.
The function will automatically abort if the number of failures (HTTP error code 502) exceeds the maximum set by the user (default is 20).


### Data Processing

In the Data Download section, we obtained the raw data from the API and saved it in a file. Next, we process the file and prepare it for sqlite3. For this stage, there is no need to obtain a token - all is done on the local machine.
Since this stage is independent of the previous one, we start again with loading the package and setting the data directory:

```
from pymathgene import get_mg_data
datadir = get_mg_data.init()
get_mg_data.os.chdir(datadir)
```

Next, we parse the JSON data file and create three batch files that can be loaded to sqlite3.
These files are: degrees_tmp.txt, persons_tmp.txt, students_tmp.txt.

Note that the function below can be called with start and end arguments, to process just part of the JSON file (for updates, and testing.)

```
filename = "allmgdat.txt"
flds = get_mg_data.parse_batch(filename)
```

The variable flds contains the unique values of degree types, field code (MSC), years, schools, and countries.
With the returned values, we can run some error checking. It will also create a students_uniq.txt file, after removing duplicate lines.

```
get_mg_data.error_checking(datadir, flds)
```

**problem with degree types**
1180 types! Create a dictionary later.

**problem in msc**
missing, Unknown - leave out for now.

**problem in years**
There are ambiguous values such as 1497 /1506, 1960-1961 - leave out for now.

**problems in schools** 
2857 schools - some may be with typos. Use AI to identify duplicates.

**problems in coutries** 
Bosnia, Bosnia Herzegovina, NONE, Netherlands-Italy, None, Poland-Japan, missing - load as is out for now.


Remove &quot &quot;

Load to db:
  * infer field
  * title - standardize, translate
  * SQL init

  - Parse all and update only modified/new (**checksum**)
  - infer language (only for English)

  Is there an API to get all IDS from MG?

Saved queries
Exports
Network

imputefield.py
title_similarity.py
