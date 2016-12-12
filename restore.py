#!/usr/bin/python2

import os, sys
from datetime import datetime, date, timedelta
from subprocess import Popen, PIPE, check_call
from backupcommon import BackupLock, BackupLogger, info, debug, error, exception, OracleExec, Configuration, BackupTemplate, create_snapshot_class
from ConfigParser import SafeConfigParser
from tempfile import mkstemp, TemporaryFile
from random import randint

def printhelp():
  print "Usage: autorestore.py <configuration_file_name without directory> [config]"
  sys.exit(2)

if len(sys.argv) not in [2]:
  printhelp()

if os.geteuid() == 0:
  print "No, I will not run as root."
  sys.exit(0)

if (not os.getenv('RESTORE_SAFE_SANDBOX')) or (os.environ['RESTORE_SAFE_SANDBOX'] != 'TRUE'):
  print "THIS RESTORE PROCESS CAN BE VERY DANGEROUS IF THIS HOST HAS ACCESS TO PRODUCTION DATABASE FILESYSTEM/STORAGE."
  print "THE RESTORE PROCESS CAN OVERWRITE OR DELETE FILES ON THEIR ORIGINAL CONTROL FILE LOCATIONS!"
  print "RUN IT ONLY ON A HOST THAT IS COMPLETELY SANDBOXED FROM PRODUCTION DATABASE ENVIRONMENT."
  print "TO CONTINUE, SET ENVIRONMENT VARIABLE RESTORE_SAFE_SANDBOX TO VALUE TRUE (CASE SENSITIVE)."
  print ""
  sys.exit(3)
