#!/usr/bin/python2

import os, sys
from datetime import datetime, date, timedelta
from backupcommon import BackupLock, BackupLogger, info, debug, error, exception, Configuration, BackupTemplate, scriptpath
from random import randint
from oraexec import OracleExec
from restorecommon import RestoreDB
from tempfile import mkstemp, TemporaryFile

def printhelp():
    print "Usage: autorestore.py <configuration_file_name without directory> [config]"
    print "  [config] is optional, if missed then action is performed on all databases in config file."
    print "  [config] could either be database unique name to be restore or on of the repository actions:"
    print "    --createcatalog"
    print "    --listvalidationdates"
    sys.exit(2)

if len(sys.argv) not in [2,3]:
    printhelp()

if os.geteuid() == 0:
    print "No, I will not run as root."
    sys.exit(0)

if (not os.getenv('AUTORESTORE_SAFE_SANDBOX')) or (os.environ['AUTORESTORE_SAFE_SANDBOX'] != 'TRUE'):
    print "THIS AUTORESTORE PROCESS CAN BE VERY DANGEROUS IF THIS HOST HAS ACCESS TO PRODUCTION DATABASE FILESYSTEM/STORAGE."
    print "THE RESTORE PROCESS CAN OVERWRITE OR DELETE FILES ON THEIR ORIGINAL CONTROL FILE LOCATIONS!"
    print "RUN IT ONLY ON A HOST THAT IS COMPLETELY SANDBOXED FROM PRODUCTION DATABASE ENVIRONMENT."
    print "TO CONTINUE, SET ENVIRONMENT VARIABLE AUTORESTORE_SAFE_SANDBOX TO VALUE TRUE (CASE SENSITIVE)."
    print ""
    sys.exit(3)

Configuration.init('autorestore', configfilename=sys.argv[1], additionaldefaults={'customverifydate': 'select max(time_dp) from sys.smon_scn_time','autorestoreenabled': '1',
    'autorestoreinstancenumber': '1', 'autorestorethread': '1'})
validatechance = int(Configuration.get('autorestorevalidatechance', 'autorestore'))
validatemodulus = int(Configuration.get('autorestoremodulus', 'autorestore'))
oexec = OracleExec(oraclehome=Configuration.get('oraclehome', 'generic'), tnspath=os.path.join(scriptpath(), Configuration.get('tnsadmin', 'generic')))
restoretemplate = BackupTemplate('restoretemplate.cfg')

# Does the backup destination exist?
restoredest = Configuration.get('autorestoredestination','autorestore')
mountdest = Configuration.get('autorestoremountpoint','autorestore')
logdir = Configuration.get('autorestorelogdir','autorestore')
Configuration.substitutions.update({
    'logdir': logdir,
    'autorestorecatalog': Configuration.get('autorestorecatalog','autorestore')
})
if restoredest is None or not os.path.exists(restoredest) or not os.path.isdir(restoredest):
    print "Restore directory %s not found or is not a proper directory" % restoredest
    sys.exit(2)
if mountdest is None or not os.path.exists(mountdest) or not os.path.isdir(mountdest):
    print "Clone mount directory %s not found or is not a proper directory" % mountdest
    sys.exit(2)

exitstatus = 0

# System actions

# Clean destination directory
def cleantarget():
    debug("ACTION: Cleaning destination directory %s" % restoredest)
    for root, dirs, files in os.walk(restoredest, topdown=False):
        for name in files:
            os.remove(os.path.join(root, name))
        for name in dirs:
            os.rmdir(os.path.join(root, name))

def validationdate(database):
    days_since_epoch = (datetime.utcnow() - datetime(1970,1,1)).days
    try:
        hashstring = Configuration.get('stringforvalidationmod', database)
    except:
        hashstring = database
    mod1 = days_since_epoch % validatemodulus
    mod2 = hash(hashstring) % validatemodulus
    validatecorruption = mod1 == mod2
    days_to_next_validation = (mod2-mod1) if mod2 > mod1 else (validatemodulus-(mod1-mod2))
    next_validation = date.today() + timedelta(days=days_to_next_validation)
    return (validatecorruption, days_to_next_validation, next_validation)

def runrestore(database):
    global exitstatus
    #
    Configuration.defaultsection = database
    # Reinitialize logging
    BackupLogger.init(os.path.join(logdir, "%s-%s.log" % (datetime.now().strftime('%Y%m%dT%H%M%S'), database)), database)
    BackupLogger.clean()
    #
    restore = RestoreDB(database)
    restore.set_mount_path(mountdest)
    restore.set_restore_path(restoredest)
    #
    info("Logfile: %s" % BackupLogger.logfile)
    Configuration.substitutions.update({
        'logfile': BackupLogger.logfile
    })
    cleantarget()
    #
    success = False
    #
    if validatemodulus > 0:
        # Validation based on modulus
        validationinfo = validationdate(database)
        validatecorruption = validationinfo[0]
        if not validatecorruption:
            debug("Next database validation in %d days: %s" % ( validationinfo[1], validationinfo[2] ))
    else:
        # Validation based on random
        validatecorruption = (validatechance > 0) and (randint(1, validatechance) == validatechance)
    if validatecorruption:
        debug("Database will be validated during this restore session")
    # Start restore
    try:
        restore.run()
        restore.verify()
        if validatecorruption:
            restore.blockcheck()
        success = True
    except:
        exitstatus = 1
        exception("Error happened, but we can continue with the next database.")
    finally:
        restore.cleanup()
    # Log result to catalog
    Configuration.substitutions.update({
        'log_dbname': database,
        'log_start': restore.starttime.strftime('%Y-%m-%d %H-%M-%S'),
        'log_stop': restore.endtime.strftime('%Y-%m-%d %H-%M-%S'),
        'log_success': '1' if success else '0',
        'log_diff': restore.verifyseconds,
        'log_snapid': restore.sourcesnapid,
        'log_validated': '1' if validatecorruption else '0'
    })
    debug('Logging the result to catalog.')
    try:
        oexec.sqlldr(Configuration.get('autorestorecatalog','autorestore'), restoretemplate.get('sqlldrlog'))
    except:
        debug("Sending the logfile to catalog failed.")
    try:
        oexec.sqlplus(restoretemplate.get('insertlog'), silent=False)
    except:
        debug("Logging the result to catalog failed.")
    # Finish up
    info("Restore %s, elapsed time: %s" % ('successful' if success else 'failed', restore.endtime-restore.starttime))
    BackupLogger.close(True)

# UI

def loopdatabases():
    excludelist = ['generic','rman','zfssa','autorestore']
    for configname in Configuration.sections():
        if configname not in excludelist:
            if Configuration.get('autorestoreenabled', configname) == '1':
                yield configname

action = None
if len(sys.argv) == 3:
    action = sys.argv[2]

if action == '--listvalidationdates':
    # This action does not need a lock
    if validatemodulus > 0:
        for configname in loopdatabases():
            validationinfo = validationdate(configname)
            print "%s: %s (in %d days)" % (configname, validationinfo[2], validationinfo[1])
    else:
        if validatechance > 0:
            print "Validation is based on chance, probability 1/%d" % validatechance
        else:
            print "Database validation is not turned on"
else:
    # Actions that need a lock
    lock = BackupLock(Configuration.get('autorestorelogdir','autorestore'))
    try:
        if action is not None:
            if action.startswith('--'):
                if action == '--createcatalog':
                    BackupLogger.init(os.path.join(logdir, "%s-config.log" % (datetime.now().strftime('%Y%m%dT%H%M%S'))), 'config')
                    info("Logfile: %s" % BackupLogger.logfile)
                    Configuration.substitutions.update({'logfile': BackupLogger.logfile})
                    oexec.sqlplus(restoretemplate.get('createcatalog'), silent=False)
            else:
                runrestore(action)
        else:
            # Loop through all sections
            for configname in loopdatabases():
                runrestore(configname)
            # Run ADRCI to clean up diag
            adrage = int(Configuration.get('logretention','generic'))*1440
            f1 = mkstemp(suffix=".adi")
            ftmp = os.fdopen(f1[0], "w")
            ftmp.write("set base %s\n" % logdir)
            ftmp.write("show homes\n")
            ftmp.close()
            f2 = mkstemp(suffix=".adi")
            ftmp2 = os.fdopen(f2[0], "w")
            ftmp2.write("set base %s\n" % logdir)
            with TemporaryFile() as f:
                try:
                    oexec.adrci(f1[1], f)
                    f.seek(0,0)
                    output = f.read()
                    startreading = False
                    for line in output.splitlines():
                        if line.startswith('ADR Homes:'):
                            startreading = True
                        elif startreading:
                            ftmp2.write("set home %s\n" % line.strip())
                            ftmp2.write("purge -age %d\n" % adrage)
                    ftmp2.close()
                    oexec.adrci(f2[1], f)
                except:
                    print "Executing ADRCI failed."
                finally:
                    os.unlink(f1[1])
                    os.unlink(f2[1])
    finally:
        lock.release()

    print "Exitstatus is %d" % exitstatus
    sys.exit(exitstatus)
