#!/usr/bin/python2

import os, sys, errno, pytz
from datetime import datetime
from backupcommon import BackupLock, BackupLogger, info, debug, error, exception, Configuration, scriptpath, UIElement
from restorecommon import RestoreDB
from tempfile import TemporaryFile
from tzlocal import get_localzone

def printheader():
    print "This utility will start a copy of a database restored to a specified point in time."
    print "THIS UTILITY SHOULD NOT BE RUN ON A SYSTEM WITH ACCESS TO PRODUCTION STORAGE!"
    print "THIS UTILITY IS NOT INTENDED FOR EMERGENCY PRODUCTION RESTORE."
    print ""

def printhelp():
    print "Usage: restore.py <configuration_file_name without directory> <config>"
    sys.exit(2)


def ask_user_input():
    global restoreparams, exitvalue

    is_safe = ui.ask_yn("Is this system isolated with no access to production database storage")
    if is_safe != "Y":
        print "Exiting. Please execute this script in an isolated environment."
        exitvalue = 1
        return
    restoreparams['mountpath'] = ui.ask_directory("Directory where to mount clone:", False)
    restoreparams['timepoint'] = ui.ask_timestamp("Restore database to time point")
    is_utc = ui.ask_yn("Was the timestamp in UTC (answer N for local time)")
    if is_utc == "Y":
        tz = pytz.utc
    else:
        tz = get_localzone()
    restoreparams['timepoint'] = tz.localize(restoreparams['timepoint'])
    restore.set_restore_target_time(restoreparams['timepoint'])
    restoreparams['sid'] = ui.ask_string("Target instance name:", 8, True)
    #
    splitter = "######################################"
    print splitter
    print ""
    print "Database unique name: %s" % configname
    print "Oracle home: %s" % Configuration.get("oraclehome", "generic")
    print "Clone mount path: %s" % restoreparams['mountpath']
    print "Target instance SID: %s" % restoreparams['sid']
    print "Restore target time UTC: %s" % restoreparams['timepoint'].astimezone(pytz.utc)
    print "Restore target time local: %s" % restoreparams['timepoint'].astimezone(get_localzone())
    print "Restored from snapshot: %s" % restore.sourcesnapid
    #
    print ""
    is_ok = ui.ask_yn("Are these parameters correct")
    if is_ok != "Y":
        print "Exiting. Please execute this script again."
        exitvalue = 1
        return
    print ""
    print splitter

def exec_restore():
    global exitvalue

    restore.clone(False)
    print "Please execute the following command as root to mount the backup volume:"
    print ""
    print "mount -t nfs -o rw,bg,hard,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600 %s %s" % (restore.mountstring, restoreparams['mountpath'])
    print ""
    while ui.ask_yn("Did you execute it") == "N":
        print "Please execute it then."
    # Verify that clone is mounted
    autorestorefile = os.path.join(restoreparams['mountpath'], 'autorestore.cfg')
    if not os.path.isfile(autorestorefile):
        print "The mounted path does not look correct, file %s not found" % autorestorefile
        exitvalue = 1
        return
    #
    debug("Oracle home: %s" % Configuration.get("oraclehome", "generic"))
    debug("Clone mount path: %s" % restoreparams['mountpath'])
    debug("Target instance SID: %s" % restoreparams['sid'])
    debug("Restore target time UTC: %s" % restoreparams['timepoint'].astimezone(pytz.utc))
    debug("Restore target time local: %s" % restoreparams['timepoint'].astimezone(get_localzone()))
    debug("Restored from snapshot: %s" % restore.sourcesnapid)
    info("Starting database restore")
    #
    try:
        restore.pit_restore(restoreparams['mountpath'], restoreparams['sid'])
        restore.verify(False)
        info("Database restore complete")
        info("SID: %s" % restoreparams['sid'])
        info("Requested target time: %s" % restoreparams['timepoint'].astimezone(get_localzone()))
        info("Verified restored database time: %s" % restore.verifytime)
        info("Difference from target: %s" % restore.verifydiff)
    except:
        exception("Database restore failed")
        exitvalue = 1
    print ""
    print "Commands to clean up:"
    print "1. Shut down database instance %s" % restoreparams['sid']
    print "2. Execute as root: umount %s" % restoreparams['mountpath']
    print "3. Drop clone: BACKUPCONFIG=%s %s %s dropclone %s" % (os.path.basename(Configuration.configfilename),
        os.path.join(scriptpath(), 'zsnapper.py'), configname, restore.clonename)

# Main UI

exitvalue = 0
restoreparams = {}
printheader()
if len(sys.argv) not in [3]:
    printhelp()

if os.geteuid() == 0:
    print "No, I will not run as root."
    sys.exit(0)

ui = UIElement()
configname = sys.argv[2]
Configuration.init(defaultsection=configname, configfilename=sys.argv[1], additionaldefaults={'customverifydate': 'select max(time_dp) from sys.smon_scn_time',
    'autorestoreenabled': '1', 'autorestoreinstancenumber': '1', 'autorestorethread': '1'})

BackupLogger.init('/tmp/restore_%s_%s.log' % (datetime.now().strftime('%Y%m%dT%H%M%S'), configname), configname)
BackupLogger.clean()
Configuration.substitutions.update({
    'logdir': '/tmp',
    'logfile': BackupLogger.logfile
})
print "Session log file: %s" % BackupLogger.logfile

restore = RestoreDB(configname)

ask_user_input()
if exitvalue == 0:
    exec_restore()

sys.exit(exitvalue)
