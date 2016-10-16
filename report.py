#!/usr/bin/python2

import os, sys, json
from backupcommon import scriptpath, Configuration, BackupLogger, BackupTemplate, OracleExec, info, error, debug, exception, create_snapshot_class
from tempfile import mkstemp

def printhelp():
  print "Usage: report.py [comma separated list of databases]"
  sys.exit(2)

if len(sys.argv) not in [1,2]:
  printhelp()

# Directory where the executable script is located
scriptpath = scriptpath()

# Read configuration
#configsection = sys.argv[1]
logf = mkstemp(prefix='backupreport-', suffix='.log')
os.close(logf[0])
Configuration.init('generic')
BackupLogger.init(logf[1], 'reporting')
Configuration.substitutions.update( {'logfile': BackupLogger.logfile, 'autorestorecatalog': Configuration.get('autorestorecatalog', 'autorestore')} )
reporttemplate = BackupTemplate('reporttemplate.cfg')

def exec_sqlplus(script, header = 'sqlplusheader'):
  finalscript = "%s\n%s\n%s" % (reporttemplate.get(header), script, reporttemplate.get('sqlplusfooter'))
  output = OracleExec.sqlplus(finalscript, silent=True)
  for line in output.splitlines():
    if line.startswith('OUTLOG: '):
      yield(line.strip()[8:])


def process_database(dbname):
  Configuration.defaultsection = dbname
  OracleExec.init(Configuration.get('oraclehome', 'generic'))
  Configuration.substitutions.update({'dbname': dbname})
  # Read job status information from the database
  jobinfo = {}
  for line in exec_sqlplus(reporttemplate.get('jobstatus')):
    j = json.loads(line)
    if j["type"] == "job":
      if j["job_name"] == "ARCHLOGBACKUP_JOB":
        jobinfo["archlog"] = j
      elif j["job_name"] == "IMAGECOPY_JOB":
        jobinfo["imagecopy"] = j
    elif j["type"] == "exec":
      if j["job_name"] == "ARCHLOGBACKUP_JOB":
        jobinfo["archlogexec"] = j
      elif j["job_name"] == "IMAGECOPY_JOB":
        jobinfo["imagecopyexec"] = j
  # Read snapshot information
  zfs = create_snapshot_class(dbname)
  snaps = zfs.listsnapshots(True, True)
  # Autorestore information
  autorestoreinfo = None
  try:
    for line in exec_sqlplus(reporttemplate.get('autorestorestatus'), 'sqlplusautorestoreheader'):
      autorestoreinfo = json.loads(line)
  except:
    pass
  # Print output
  print "%s:" % dbname
  try:
    print "  Backup job: %s, last: %s, duration: %s, last failure: %s" % (jobinfo['imagecopy']['state'], jobinfo['imagecopy']['last_start_date'], jobinfo['imagecopy']['last_run_duration'], jobinfo['imagecopyexec']['last_failed'])
    print "  Archivelog job: %s, last: %s, duration: %s, last failure: %s" % (jobinfo['archlog']['state'], jobinfo['archlog']['last_start_date'], jobinfo['archlog']['last_run_duration'], jobinfo['archlogexec']['last_failed'])
    if len(snaps) > 0:
      firstsnap = zfs.getsnapinfo(snaps[0])
      lastsnap = zfs.getsnapinfo(snaps[-1])
      print "  Snapshots: %d, latest: %s, oldest: %s" % (len(snaps), firstsnap["creation"], lastsnap["creation"])
    else:
      print "  Snapshots: none"
    if autorestoreinfo is not None:
      print "  Last successful restore: %s, last restore failure: %s, last successful validation: %s, avg difference from target (s): %d, avg restore time (min): %d" % (autorestoreinfo["last_success"], autorestoreinfo["last_fail"], autorestoreinfo["last_validated"], autorestoreinfo["avgdiff"], autorestoreinfo["avgrestoremin"])
  except:
    print "  Error getting information."

excludelist = ['generic','rman','zfssa','autorestore']
includelist = []
if len(sys.argv) == 2:
  includelist = sys.argv[1].split(",")

# Loop through all sections
for dbname in Configuration.sections():
  if dbname not in excludelist and (len(includelist) == 0 or dbname in includelist):
    process_database(dbname)
