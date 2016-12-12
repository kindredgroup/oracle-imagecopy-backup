#!/usr/bin/python2

import os, sys, errno
#from datetime import datetime, date, timedelta
#from subprocess import Popen, PIPE, check_call
from backupcommon import BackupLock, BackupLogger, info, debug, error, exception, Configuration
from restorecommon import RestoreDB
#from ConfigParser import SafeConfigParser
from tempfile import TemporaryFile

def printheader():
    print "This utility will start a copy of a database restored to a specified point in time."
    print "THIS UTILITY SHOULD NOT BE RUN ON A SYSTEM WITH ACCESS TO PRODUCTION STORAGE!"
    print "THIS UTILITY IS NOT INTENDED FOR EMERGENCY PRODUCTION RESTORE."
    print ""

def printhelp():
    print "Usage: restore.py <configuration_file_name without directory> [config]"
    sys.exit(2)

def is_dir_writable(path):
    try:
        f = TemporaryFile(dir = path)
        f.close()
    except OSError as e:
        if e.errno == errno.EACCES:
            return False
        e.filename = path
        raise
    return True

def ask_directory(question, demand_empty=True):
    path = None
    while True:
        answer = raw_input(question)
        if answer is None or answer.strip() == "":
            print "Answer is required"
            continue
        path = answer.strip()
        if not os.path.exists(path) or not os.path.isdir(path):
            print "Specified path does not exist or is not directory"
            continue
        if not is_dir_writable(path):
            print "Specified path is not writable"
            continue
        if demand_empty and os.listdir(path):
            print "Specified path must be empty"
            continue
        break
    return path

def ask_yn(question):
    answer = None
    while True:
        answer = raw_input("%s? (y/n) " % question)
        answer = answer.strip().upper()
        if answer not in ['Y','N']:
            print "Invalid input"
            continue
        break
    return answer

def ask_timestamp(question):
    answer = None
    while True:
        answer = raw_input("%s? (yyyy-mm-dd hh24:mi:ss) " % question)
        break
    return answer

def ask_user_input():
    is_safe = ask_yn("Is this system isolated with no access to production database storage")
    if is_safe != "Y":
        print "Exiting. Please execute this script in an isolated environment."
        exitvalue = 1
        return
    mountpath = ask_directory("Directory where to mount clone: ", False)
    #
    print "######################################"
    print ""
    print "Clone mount path: %s" % mountpath
    print ""
    is_ok = ask_yn("Are these parameters correct")
    if is_ok != "Y":
        print "Exiting. Please execute this script again."
        exitvalue = 1
        return

# Main UI

exitvalue = 0
printheader()
if len(sys.argv) not in [3]:
    printhelp()

if os.geteuid() == 0:
    print "No, I will not run as root."
    sys.exit(0)

ask_user_input()

sys.exit(exitvalue)
