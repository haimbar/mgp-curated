import tkinter as tk
from tkinter import filedialog
import os
import requests
import json
import re

def init():
    '''Select the data directory.'''
    if os.environ.get('DISPLAY', '') == '':
        print('No display found. Enter the directory name manually:')
        datadir = input()
    else:
        root = tk.Tk()
        root.withdraw()
        datadir = filedialog.askdirectory()
    return(datadir)


def lastID(datadir):
    '''Look for the last record downloaded (stored in allmgdat.txt)
    and return its ID.'''
    if os.path.isdir(datadir) == False:
        print(f'Directory {datadir} not found')
        return(None)
    os.chdir(datadir)
    if os.path.isfile('allmgdat.txt') == False:
        print('File allmgdat.txt. Returning 0, but check if the data directory was entered correctly')
        return(0)
    with open(f'allmgdat.txt', 'r') as f:
        last_line = f.readlines()[-1]
        splt  = last_line.split(', ', )
        if len(splt) <= 1:
            print("No valid data?")
            return(None)
        for j in range(len(splt)):
            if "MGP_academic" in splt[j]:
                ID = int(splt[j].split('ID\': ')[1].replace("'", ""))
                return(ID)
    print('Something is wrong - did not find ID in the last line of the file')
    return(None)


def max_recs():
    '''Prompt the user to enter the maximum number of records to download.'''
    user_input = input("Enter the maximum number of records to download (<= 5000): ")
    try:
        number = int(user_input)
        if 1 <= number <= 5000:
            return(number)
        else:
            print("Input is out of range.")
            return(None)
    except ValueError:
        print("Not a valid input.")
        return(None)


def getlogin():
    '''Prompt the user to enter their MG email and passowrd.
    Password is not shown on console while the user is typing it.'''
    print("Enter email used for MGP authentication:", end=" ")
    email = input()
    password = getpass()
    return {'email': email, 'password': password}


def login(authdata):
    '''Log in to the MGP API and get a JSON Web Token (JWT) for authentication.
    authdata is a dict with user's email address and the user's password.
    If login is successful, returns a JSON object with key token set
    to the JWT.
    If login is unsuccessful, raises RuntimeError.'''
    r = requests.post(f"{PROTOCOL}://{HOSTNAME}:{PORT}/login", authdata)
    if r.ok:
        r.close()
        return r.json()
    else:
        r.close()
        raise RuntimeError("Failed to authenticate")


def IDquery(token, params, PROTOCOL = "https", HOSTNAME = "mathgenealogy.org",
        PORT = "8000", endpoint = '/api/v2/MGP/acad', silent=True):
    '''Get the MG data for one record (passed via the params.'''
    headers = {'x-access-token': token['token']}
    r = requests.get(f"{PROTOCOL}://{HOSTNAME}:{PORT}{endpoint}",
        headers = headers, params = params)
    if r.status_code == 502:
        if not silent:
            print(f"ID {params} returned Error 502")
        return(None)
    if r.ok:
        r.close()
        return(r.text)
    else:
        r.close()
        raise RuntimeError("Error executing query")


def query_loop(cred, token, first, last, max_fails=20):
    '''Get the data for multiple records, from first to last.
    If we get max_fails consecutive Nones, we stop. Maybe there
    are no more entries, or the API is non-responsive'''
    refresh = 2000
    fails = []
    cnt = 0
    f = open('allmgdattmp.txt', 'a')
    for i in range(first, last):
        # The authentication key can time out, so refresh the session
        if cnt > 0 and cnt%refresh == 0:
            token = login(cred)
        querydata = {'id': i}
        dq = IDquery(token, querydata)
        if dq is None:
            fails.append(i)
            if len(fails) >= max_fails:
                print(f'Reached {max_fails} errors. Quitting.')
                return {'total': cnt, 'fails': fails}
                break
            continue
        acad = json.loads(doquery(endpoint, token, querydata))
        # Save the raw json data
        f.write(str(acad)+"\n")
        f.flush()
        cnt += 1
        #parse_json(str(acad))
    f.close()
    return {'total': cnt, 'fails': fails}


def parse_json(rec):
    '''Prepare the academic record from mathgenealogy.org to be loaded
    to sqlite3.'''
    rec = rec.replace(u'\xa0', u' ')
    rec = rec.replace('\\xa0', ' ')
    rec = rec.replace('\r', ' ')
    rec = rec.replace('\n', ' ')
    splt  = rec.split(', ',)
    if len(splt) <= 1:
        print("No valid data?")
        return(None)
    fper = open("persons_tmp.txt", "a")
    fstu = open("students_tmp.txt", "a")
    fdeg = open("degrees_tmp.txt", "a")
    inAdvisees = False
    ID = 'missing'
    degree_type = 'missing'
    degree_msc = 'Unknown'
    degree_year = 'missing'
    school = 'missing'
    country = 'missing'
    title = 'missing'
    for j in range(len(splt)):
        if "MGP_academic" in splt[j]:
            ID = splt[j].split('ID\': ')[1].replace("'", "")
        if splt[j].startswith("\'family_name\':"):
            famname = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"').rstrip('|')
        if splt[j].startswith("\'given_name\':"):
            firstname = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"').rstrip('|')
        if splt[j].startswith("\'other_names\':"):
            othername = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"').rstrip('|')
        if "advised by" in splt[j]:
            advisor = splt[j].split("advised by\': {", 2)[1].split("': ", 2)[0].lstrip("'")
            print('|'.join((advisor, ID)), file=fstu)
            #print(ID, advisor)
            jj = 0
            while not splt[j+jj].endswith("}"):
                advisor = splt[j+jj].split("': ")[0].lstrip("'")
                adsids = re.findall(r'\d+', advisor)
                jj += 1
                if len(adsids) == 0:
                    continue
                print('|'.join((advisor, ID)), file=fstu)
        if "degree_msc" in splt[j]:
            degree_msc =  splt[j].split("degree_msc\': ", 2)[1].lstrip("'").rstrip("'")
            if degree_msc == "Unknown":
                degree_msc = -1
            else:
                degree_msc = int(degree_msc)
        if "degree_type" in splt[j]:
            degree_type = splt[j].split("degree_type\': ", 2)[1].lstrip("'").rstrip("'").replace('"', "")
        if "degree_year" in splt[j]:
            degree_year = splt[j].split("degree_year\': ", 2)[1].lstrip("'").rstrip("'")
            degree_year = degree_year.lstrip(' ').rstrip(' ')
#            degree_year = re.sub('^[a-zA-Z]+.* +', ' ', degree_year)
        if "schools':" in splt[j]:
            if "schools': ['']" in splt[j]:
                school = "NONE"
                country = "NONE"
                continue
            school = splt[j].split("schools': [", 2)[1]
            school = school.lstrip("'").rstrip("'").lstrip(" ").rstrip(" ")
            # country fix:
            jj = 0
            while not splt[j+jj].endswith("]"):
                if jj > 0:
                    school = school + ', ' + splt[j+jj]
                jj += 1
            country = splt[j+jj].replace("']", "").replace('"]', "").replace("']", "").replace('"]', "")
            school = school.lstrip('"').rstrip('"') #.replace("'", "")
        if "'thesis_title':" in splt[j]:
            if "thesis_title': ['']" in splt[j]:
                title = "NOTITLE"
                continue
            title = splt[j].split("thesis_title': ", 2)[1].replace("'}]", "").replace("\"}]", "").replace("\"}]", "")
            title = title.replace("'}", "").replace("|", "&vert;").replace('"', " &quot; ").lstrip("'").lstrip("\"")
            if title == "Unknown":
                title = "NOTITLE"
            print("|".join((ID, degree_type, str(degree_msc), degree_year, school, country, title)), file=fdeg)
        if "advisees" in splt[j]:
            inAdvisees = True
            if "advisees': ['']" in splt[j]:
                continue
            advisees = [(splt[j].split("[[", 2)[1].replace("'", ""))]
            print('|'.join((ID, '|'.join(advisees))), file=fstu)
        if inAdvisees & splt[j].startswith("['"):
            #advisees.append(splt[j].split("[", 2)[1].replace("'", ""))
            print('|'.join((ID, splt[j].split("[", 2)[1].replace("'", ""))), file=fstu)
    print('|'.join((ID, famname, firstname, othername)), file=fper)
    fper.close()
    fstu.close()
    fdeg.close()
    return({'type': degree_type, 'msc': degree_msc, 'year': degree_year, 'school': school, 'country': country})


def parse_batch(filename, start=0, end=0):
    '''Call parse_json for a list of MG IDs, and return a dictionary
    with sets of degree tpes, MSCs, years, schools, and countries.'''
    degree_types = set()
    degree_mscs = set()
    degree_years = set()
    schools = set()
    countries = set()
    with open(filename) as f:
        data = f.readlines()
        if start > 0 and end > start:
            rng = range(start, end+1)
        else:
            rng = range(len(data))
        for i in rng:
            if not data[i].startswith("{'MGP"):
                continue
            #print(i, data[i],'\n')
            flds = parse_json(data[i])
            if flds is None:
                continue
            degree_types.add(flds["type"])
            degree_mscs.add(flds["msc"])
            degree_years.add(flds["year"])
            schools.add(flds["school"])
            countries.add(flds["country"])
    return([degree_types, degree_mscs, degree_years, schools, countries])

def error_checking(datadir, flds):
    '''Check for unusual values, and for possible typos.'''
    degtype = list(flds[0])
    degtype.sort()
    print('Degree types')
    print(degtype, '\n')
    mscs = list(flds[1])
    mscs.remove('Unknown')
    mscs.sort()
    print('MSC')
    print(mscs, '\n')
    years = list(flds[2])
    years.sort()
    print('Years')
    for i in range(len(years)):
        if not years[i].isdigit():
            print(i, f'_{years[i]}_')
    print('\n')
    schools = list(flds[3])
    schools.sort()
    print('Schools')
    print(schools, '\n')
    countries = list(flds[4])
    countries.sort()
    print('Countries')
    print(countries, '\n')
    # Check that the persons file has four columns in each row:
    print("Persons file format")
    with open('persons_tmp.txt') as f:
        data = f.readlines()
        for i in range(len(data)):
            if data[i].count('|') != 3:
                print(i, data[i])
    print('Done\n')
    # Remove duplicates from the students file
    print("Student file - remove duplicates")
    with open('students_tmp.txt') as f:
        data = f.readlines()
    data = list(set(data))
    f = open('students_uniq.txt', 'w')
    f.write(''.join(data))
    f.close()
   
