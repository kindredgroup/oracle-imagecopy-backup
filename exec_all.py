#!/usr/bin/python2

import os, sys
from subprocess import check_call
from backupcommon import scriptpath, Configuration

def printhelp():
  print "Usage: exec_all.py <action for backup.py> [comma separated exclude list]"
  sys.exit(2)

if len(sys.argv) not in [2,3]:
  printhelp()

# Directory where the executable script is located
scriptpath = scriptpath()

# Read configuration
configsection = sys.argv[1]
Configuration.init()

if len(sys.argv) == 3:
  excludelist = sys.argv[2].split(",")
else:
  excludelist = []
excludelist.append('generic')
excludelist.append('rman')
excludelist.append('zfssa')
excludelist.append('autorestore')
excludelist.append('netapp')

# Loop through all sections
for dbname in Configuration.sections():
  if dbname not in excludelist:
    # Execute backup.py with the specified action
    print "--- DATABASE: %s ---" % dbname
    check_call([ os.path.join(scriptpath, 'backup.py'), dbname, configsection])
