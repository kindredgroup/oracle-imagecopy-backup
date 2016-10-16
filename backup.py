#!/usr/bin/python2.6

import os, ConfigParser, sys
from datetime import datetime, timedelta
from subprocess import Popen, PIPE, STDOUT
from tempfile import TemporaryFile
from backupcommon import BackupLock, BackupLogger, info, debug, error, exception, scriptpath, OracleExec, Configuration, BackupTemplate, create_snapshot_class

# Check command line arguments
uioptions = ['config','setschedule','backupimagecopy','report','validatebackup','generaterestore','imagecopywithsnap','missingarchlog']

def printhelp():
  print "Usage: backup.py <config> <%s>" % '|'.join(uioptions)
  sys.exit(2)

if len(sys.argv) != 3:
  printhelp()
else:
  scriptaction = sys.argv[2].lower()
if not scriptaction in uioptions:
  printhelp()

# Check environment
if (scriptaction == 'setschedule') and (os.getenv('OSPASSWORD') is None):
  print "Environment variable OSPASSWORD must be set."
  sys.exit(2)

# Directory where the executable script is located
scriptpath = scriptpath()

# Read configuration
configsection = sys.argv[1]
Configuration.init(configsection)

# Database specific configuration
backupdest = os.path.join(Configuration.get('backupdest', 'generic'), configsection)
archdir = os.path.join(backupdest, 'archivelog')
hasdataguard = Configuration.get('hasdataguard').upper() == 'TRUE'
dosnapshot = Configuration.get('dosnapshot').upper() == 'TRUE'
gimanaged = Configuration.get('gimanaged').upper() == 'TRUE'
registercatalog = Configuration.get('registercatalog').upper() == 'TRUE'

# Log file for this session
logdir = os.path.join(backupdest, 'backup_logs')
logfile = os.path.join(logdir, "%s_%s_%s.log" % (configsection, datetime.now().strftime('%Y%m%dT%H%M%S'), scriptaction) )
print "Log file for this session: %s" % logfile
BackupLogger.init(logfile, configsection)
BackupLogger.clean()

# Oracle environment variables
oraclehome = Configuration.get('oraclehome', 'generic')
OracleExec.init(oraclehome)

# Prepare a dictionary of all possible template substitutions
Configuration.substitutions.update({ 'recoverywindow': Configuration.get('recoverywindow'),
                  'parallel': Configuration.get('parallel'),
                  'backupdest': backupdest,
                  'archdir': archdir,
                  'catalogconnect': Configuration.get('catalog', 'rman'),
                  'configname': configsection,
                  'osuser': Configuration.get('osuser', 'generic'),
                  'ospassword': os.getenv('OSPASSWORD'),
                  'scriptpath': scriptpath,
                  'schedulebackup': Configuration.get('schedulebackup'),
                  'schedulearchlog': Configuration.get('schedulearchlog'),
                  'dbid': int(Configuration.get('dbid')),
                  'oraclehome': oraclehome,
                  'tnspath': OracleExec.tnspath,
                  'logfile': logfile
                })

# Read RMAN templates
rmantemplateconfig = BackupTemplate('rmantemplate.cfg')

# Initialize snapshot class
snap = create_snapshot_class(configsection)

# Execute RMAN with script as input
def exec_rman(rmanscript):
  # Modify rman script with common headers
  finalscript = rmantemplateconfig.get('header')
  if registercatalog:
    finalscript+= "\n%s" % rmantemplateconfig.get('headercatalog')
  finalscript+= "\n%s" % rmanscript
  finalscript+= "\n%s" % rmantemplateconfig.get('footer')
  # print finalscript
  OracleExec.rman(finalscript)

# Execute sqlplus with a given script
def exec_sqlplus(sqlplusscript, silent=False, header=True):
  script = ""
  if header:
    script+= "%s\n" % rmantemplateconfig.get('sqlplusheader')
  script+= "%s\n" % sqlplusscript
  if header:
    script+= "%s\n" % rmantemplateconfig.get('sqlplusfooter')
  return OracleExec.sqlplus(script, silent)

##############
# User actions
##############

def configure():
  rmanscript = ''
  # Create directory for archive logs
  if not os.path.exists(archdir):
    os.makedirs(archdir)
  # Register database in catalog if needed
  if registercatalog:
    alreadyregistered = False
    info("Checking from RMAN catalog if database is already registered")
    output = exec_sqlplus(rmantemplateconfig.get('isdbregisteredincatalog'), silent=True, header=False)
    for line in output.splitlines():
      if line.startswith('DATABASE IS REGISTERED IN RC'):
        alreadyregistered = True
    if not alreadyregistered:
      rmanscript+= rmantemplateconfig.get('registerdatabase')
  # Configure archivelog deletion policy
  if hasdataguard:
    rmanscript+= "\n%s" % rmantemplateconfig.get('configdelaldg')
  else:
    rmanscript+= "\n%s" % rmantemplateconfig.get('configdelalnodg')
  # configures rman default settings
  rmanscript+= "\n%s" % rmantemplateconfig.get('config')
  info("Running RMAN configuration")
  exec_rman(rmanscript)
  info("Running additional configuration from SQL*Plus")
  exec_sqlplus(rmantemplateconfig.get('configfromsqlplus'))

def backup(level):
  entry = 'backup'
  if level == '1c':
    entry+= 'cumulative'
  elif level == '1d':
    entry+= 'diff'
  elif level == 'arch':
    entry+= 'archivelog'
  elif level == 'imagecopy':
    entry+= 'imagecopy'
  else:
    entry+= 'full'
  # Execute backup commands inside run block
  rmanscript = "run {\n%s\n%s\n}\n" % (rmantemplateconfig.get(entry), rmantemplateconfig.get('backupfooter'))
  exec_rman(rmanscript)

def backup_missing_archlog():
  output = exec_sqlplus(rmantemplateconfig.get('archivelogmissing'), silent=True)
  archlogscript = ""
  for line in output.splitlines():
    if line.startswith('BACKUP force as copy'):
      archlogscript+= "%s\n" % line.strip()
  if archlogscript:
    info("- Copying missing archivelogs")
    exec_rman("run {\n%s\n%s\n}" % (rmantemplateconfig.get('allocatearchlogchannel'), archlogscript))

def delete_expired_datafilecopy():
  output = exec_sqlplus(rmantemplateconfig.get('deletedatafilecopy'), silent=True)
  rmanscript = ""
  for line in output.splitlines():
    if line.startswith('DELETECOPY: '):
      rmanscript+= "%s\n" % line.strip()[12:]
  if rmanscript:
    info("- Deleting expired datafile copies")
    exec_rman(rmanscript)

def imagecopywithsnap():
  starttime = datetime.now()
  restoreparamfile = os.path.join(backupdest, 'autorestore.cfg')
  #
  info("Check if there are missing archivelogs")
  backup_missing_archlog()
  #
  info("Switch current log")
  output = exec_sqlplus(rmantemplateconfig.get('archivecurrentlogs'), silent=True)
  if os.path.isfile(restoreparamfile):
    with open(restoreparamfile, 'a') as f:
      for line in output.splitlines():
        if line.startswith('CURRENT DATABASE SCN:'):
          f.write("lastscn: %s\n" % line.strip()[22:])
        elif line.startswith('CURRENT DATABASE TIME:'):
          f.write("lasttime: %s\n" % line.strip()[23:])
        elif line.startswith('BCT FILE:'):
          f.write("bctfile: %s\n" % line.strip()[10:])
  #
  if dosnapshot:
    info("Snap the current backup area")
    snapid = snap.snap()
    debug("Created snapshot: %s" % snapid)
  #
  info("Checking for expired datafile copies")
  delete_expired_datafilecopy()
  #
  info("Refresh imagecopy")
  backup('imagecopy')
  exec_sqlplus(rmantemplateconfig.get('archivecurrentlogs'))
  #
  if dosnapshot:
    info("Clean expired snapshots")
    cleaningresult = snap.clean()
    for r in cleaningresult:
      debug(r['infostring'])
  #
  if gimanaged:
    p = Popen([os.path.join(scriptpath, 'dbinfo.py'), configsection], stdout=PIPE, stderr=None, stdin=None)
    output,outerr = p.communicate()
    debug(output)
  #
  info("Write database parameters for autorestore")
  with open(restoreparamfile, 'w') as f:
    f.write("[dbparams]\n")
    output = exec_sqlplus(rmantemplateconfig.get('autorestoreparameters'), silent=True)
    for line in output.splitlines():
      if line.startswith('dbconfig-'):
        f.write("%s\n" % line[9:])
  #
  endtime = datetime.now()
  info("------------ TOTAL ------------")
  info("Total execution time: %s" % (endtime-starttime))
  info("Execution started: %s" % starttime)
  info("Execution finished: %s" % endtime)

def exec_template(template_name):
  rmanscript = ''
  if registercatalog:
    rmanscript+= "%s\n" % rmantemplateconfig.get('resynccatalog')
  rmanscript+= rmantemplateconfig.get(template_name)
  exec_rman(rmanscript)

def generate_restore():
  #
  print "\n============="
  info(rmantemplateconfig.get('headerrestore'))
  if registercatalog:
    info(rmantemplateconfig.get('headercatalog'))
  info(rmantemplateconfig.get('fullrestore'))
  info(rmantemplateconfig.get('restorefooter'))

def setschedule():
  # Detect if we are running from a CDB and get the common user prefix
  output = exec_sqlplus(rmantemplateconfig.get('cdbdetect'), silent=True)
  commonprefix = ""
  for line in output.splitlines():
    if line.startswith('CDB-DETECT:') and line.strip() <> 'CDB-DETECT: NO':
      commonprefix = line.strip()[12:]
  Configuration.substitutions.update({'scheduleuserprefix': commonprefix})
  #
  script = "%s\n" % rmantemplateconfig.get('createuser')
  script+= "%s\n" % rmantemplateconfig.get('dropschedule')
  script+= "%s\n" % rmantemplateconfig.get('createschedule')
  exec_sqlplus(script)

################################################
### Main section
################################################

info("Configuration file: %s" % Configuration.configfilename)

lock = BackupLock(lockdir=backupdest, maxlockwait=int(Configuration.get('maxlockwait', 'generic')))

try:
  # User interface action execution
  if scriptaction == 'config':
    configure()
  elif scriptaction == 'generaterestore':
    generate_restore()
  elif scriptaction == 'imagecopywithsnap':
    imagecopywithsnap()
  elif scriptaction == 'setschedule':
    setschedule()
  elif scriptaction == 'missingarchlog':
    backup_missing_archlog()
  else:
    exec_template(scriptaction)
finally:
  lock.release()
  if (os.getenv('BACKUP_LOG_TO_SCREEN')) and (os.environ['BACKUP_LOG_TO_SCREEN'] == 'TRUE'):
    BackupLogger.close(True)
    print "\n\n======================\nBACKUP LOG FILE OUTPUT\n======================\n\n"
    if os.path.isfile(logfile):
      with open(logfile, 'r') as tmplogf:
        print tmplogf.read()
    else:
      print "Log file not found"
