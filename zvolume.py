#!/usr/bin/python2

import sys
from backupcommon import Configuration, create_snapshot_class
#from datetime import datetime, timedelta

# Check command line arguments
uioptions = ['create']
if (len(sys.argv) < 3) or (len(sys.argv) > 4) or (not sys.argv[2] in uioptions):
  print "Usage: zvolume.py <config> <%s>" % '|'.join(uioptions)
  sys.exit(2)

configsection = sys.argv[1]
Configuration.init(configsection)

storage = create_snapshot_class(configsection)

# Main UI
if sys.argv[2] == 'create':
    storage.createvolume()
