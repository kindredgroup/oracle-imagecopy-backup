#!/usr/bin/python2

import os, sys, errno, pytz
from datetime import datetime
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
    print "Usage: restore.py <configuration_file_name without directory> <config>"
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
        answer = raw_input("%s " % question)
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
    dt = None
    while True:
        answer = raw_input("%s: (yyyy-mm-dd hh24:mi:ss) " % question)
        answer = answer.strip()
        try:
            dt = datetime.strptime(answer, "%Y-%m-%d %H:%M:%S")
        except ValueError as e:
            print "Input does not match required format"
            continue
        break
    return dt

def ask_string(question, maxlength=None, onlyalnum=False):
    answer = None
    while True:
        answer = raw_input("%s " % question)
        answer = answer.strip()
        if maxlength is not None and answer.length() > maxlength:
            print "Max %d characters allowed" % maxlength
            continue
        if onlyalnum and not answer.isalnum():
            print "Only alphanumeric characters allowed" % maxlength
            continue
        break
    return answer

def ask_user_input():
    global restoreparams

    is_safe = ask_yn("Is this system isolated with no access to production database storage")
    if is_safe != "Y":
        print "Exiting. Please execute this script in an isolated environment."
        exitvalue = 1
        return
    restoreparams['mountpath'] = ask_directory("Directory where to mount clone:", False)
    restoreparams['timepoint'] = ask_timestamp("Restore database to time point")
    restoreparams['sid'] = ask_string("Target instance name:", 8, True)
    #
    print "######################################"
    print ""
    print "Database unique name: %s" % configname
    print "Oracle home: %s" % Configuration("oraclehome", "generic")
    print "Clone mount path: %s" % restoreparams['mountpath']
    print "Restore target time UTC: %s" % restoreparams['timepoint']
    print "Restore target time local: %s" % restoreparams['timepoint']

    print ""
    is_ok = ask_yn("Are these parameters correct")
    if is_ok != "Y":
        print "Exiting. Please execute this script again."
        exitvalue = 1
        return

# Main UI

exitvalue = 0
restoreparams = {}
printheader()
if len(sys.argv) not in [3]:
    printhelp()

if os.geteuid() == 0:
    print "No, I will not run as root."
    sys.exit(0)

configname = sys.argv[2]
Configuration.init(defaultsection=configname, configfilename=sys.argv[1])

ask_user_input()
if exitvalue == 0:
    # start restore
    # if req time > last snap time create a new snapshot
    # after restore open it and run validation query and report user the time difference
    pass

sys.exit(exitvalue)
