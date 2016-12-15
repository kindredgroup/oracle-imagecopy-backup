#!/usr/bin/python2

import sys
from backupcommon import Configuration, create_snapshot_class
from datetime import datetime, timedelta

# Check command line arguments
uioptions = ['list','clean','create','checkage','clone','dropclone','listclones','autoclone']
if (len(sys.argv) < 3) or (len(sys.argv) > 4) or (not sys.argv[2] in uioptions):
  print "Usage: zsnapper.py <config> <%s> [name]" % '|'.join(uioptions)
  sys.exit(2)

configsection = sys.argv[1]
Configuration.init(configsection)

zfs = create_snapshot_class(configsection)

# Public use functions
def checkage():
  # Returns the latest snapshot age for nagios check
  exitcode = 0
  warning = timedelta(hours = int(Configuration.get('warningsnapage', 'generic')))
  critical = timedelta(hours = int(Configuration.get('criticalsnapage', 'generic')))
  try:
    snaps = zfs.listsnapshots()
    minage = None
    for s in snaps:
      d = zfs.str2date(s["creation"])
      age = datetime.utcnow() - d
      if (minage is None) or (age < minage):
        minage = age
    s = "OK"
    if (minage is None) or (minage >= critical):
      exitcode = 2
      s = "CRITICAL"
    elif minage >= warning:
      exitcode = 1
      s = "WARNING"
    print "%s: The latest snapshot age %s" % (s, minage)
  except Exception as detail:
    print "Exception occured: %s" % detail
    exitcode = 3
  sys.exit(exitcode)

def clone_snapshot(source=None, clone=None):
  if source is None:
    sourcename = sys.argv[3]
  else:
    sourcename = source
  if clone is None:
    clonename = "%s_clone_%s" % (sourcename, datetime.now().strftime('%Y%m%dT%H%M%S'))
  else:
    clonename = clone
  zfs.clone(sourcename, clonename)
  fs = zfs.filesystem_info(clonename)
  print "Clone created."
  print "Clone name: %s" % clonename
  print "Mount point: %s" % fs["mountpoint"]
  print "Mount command (execute as root and replace zfs ip address and mount directory):"
  print "mount -t nfs -o rw,bg,soft,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600 <zfs_ip_address>:%s <mount_directory_here>" % fs["mountpoint"]

# Call the correct procedure based on parameters
if sys.argv[2] == 'clean':
  output = zfs.clean()
  for s in output:
    print s['infostring']
elif sys.argv[2] == 'create':
  snapname = zfs.snap()
  print "Snapshot created: %s" % snapname
elif sys.argv[2] == 'clone':
  clone_snapshot()
elif sys.argv[2] == 'checkage':
  checkage()
elif sys.argv[2] == 'dropclone':
  zfs.dropclone(sys.argv[3])
  print "Clone dropped."
elif sys.argv[2] == 'listclones':
  for s in zfs.listclones():
    print zfs.clone2str(s)
elif sys.argv[2] == 'autoclone':
  zfs.autoclone()
  print "Clone created."
else:
  snaps = zfs.listsnapshots()
  for s in snaps:
    print zfs.snap2str(zfs.getsnapinfo(s))
