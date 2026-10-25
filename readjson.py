# Generate TABLES
#
# persons: MGID, famname, firstname, othername, field("TBF")
# degrees: MGID, degree_type, degree_msc, degree_year, school, country, advisor, title
# students: MGIDadvisor, MGIDstudent
# msc: code, name


import re
import pandas as pd
import os

#os.chdir("/Users/haim/OneDrive - University of Connecticut/Documents/Research/MG")
os.chdir("/home/haim/Projects/MG")
#countries = pd.read_csv("countries.csv", sep='|')

#filename='/Users/haim/Desktop/MG/allmgdat.txt'
filename = 'allmgdat.txt'
with open(filename) as f:
    data = f.readlines()

print(len(data))
fper = open("persons.txt", "w")
fstu = open("students.txt", "w")
fdeg = open("degrees.txt", "w")
# i=488  # for i in range(488, 491): # for i in range(296000, 296002):

for i in range(len(data)):
    rec = data[i].rstrip()
    #rec = rec.lstrip("{'MGP_academic': {").rstrip("},")
    splt  = rec.split(', ',)
    if len(splt) <= 1:
        continue
    inAdvisees = False
    for j in range(len(splt)):
        if "MGP_academic" in splt[j]:
            ID = splt[j].split('ID\': ')[1].replace("'", "")
        if splt[j].startswith("\'family_name\':"):
            famname = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"')
        if splt[j].startswith("\'given_name\':"):
            firstname = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"')
        if splt[j].startswith("\'other_names\':"):
            othername = splt[j].split('\': ')[1].lstrip("'").rstrip("'").lstrip('"').rstrip('"')
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
        if "degree_type" in splt[j]:
            degree_type = splt[j].split("degree_type\': ", 2)[1].lstrip("'").rstrip("'").replace('"', "")
        if "degree_year" in splt[j]:
            degree_year = splt[j].split("degree_year\': ", 2)[1].lstrip("'").rstrip("'")
        if "schools':" in splt[j]:
            if "schools': ['']" in splt[j]:
                school = "NONE"
                country = "NONE"
                continue
            school = splt[j].split("schools': [", 2)[1]
            school = school.lstrip("'").rstrip("'")
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
                title = "NONE"
                continue
            title = splt[j].split("thesis_title': ", 2)[1].replace("'}]", "").replace("\"}]", "").replace("\"}]", "")
            title = title.replace("'}", "").replace("|", "&vert;").replace('"', "&quot;").lstrip("'").lstrip("\"")
            print("|".join((ID, degree_type, degree_msc, degree_year, school, country, title)), file=fdeg)
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
