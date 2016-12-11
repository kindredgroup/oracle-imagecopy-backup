import os, sys
from subprocess import Popen, PIPE
from backupcommon import BackupLogger, info, debug, error, exception
from datetime import datetime, timedelta
from tempfile import mkstemp, TemporaryFile

class OracleExec(object):
    oraclehome = None
    tnspath = None
    oraclesid = None

    def __init__(self, oraclehome, tnspath, sid=None):
        self.oraclehome = oraclehome
        self.tnspath = tnspath
        if sid is not None:
            self.oraclesid = sid
        debug("Oracle home: %s" % self.oraclehome)

    def _setenv(self):
        if self.oraclesid is None and os.environ.get('ORACLE_SID'):
            del os.environ['ORACLE_SID']
        if self.oraclesid is not None:
            os.environ['ORACLE_SID'] = self.oraclesid
        os.environ['ORACLE_HOME'] = self.oraclehome
        os.environ['NLS_DATE_FORMAT'] = 'yyyy-mm-dd hh24:mi:ss'
        os.environ['TNS_ADMIN'] = self.tnspath

    def rman(self, finalscript):
        self._setenv()
        debug("RMAN execution starts")
        BackupLogger.close()
        starttime = datetime.now()
        with TemporaryFile() as f:
            p = Popen([os.path.join(self.oraclehome, 'bin', 'rman'), "log", BackupLogger.logfile, "append"], stdout=f, stderr=f, stdin=PIPE)
            # Send the script to RMAN
            p.communicate(input=finalscript)
        endtime = datetime.now()
        BackupLogger.init()
        debug("RMAN execution time %s" % (endtime-starttime))
        # If RMAN exists with any code except 0, then there was some error
        if p.returncode != 0:
            error("RMAN execution failed with code %d" % p.returncode)
            raise Exception('rman', "RMAN exited with code %d" % p.returncode)
        else:
            debug("RMAN execution successful")

    def sqlplus(self, finalscript, silent=False):
        self._setenv()
        with TemporaryFile() as f:
            args = [os.path.join(self.oraclehome, 'bin', 'sqlplus')]
            if silent:
                args.append('-S')
            args.append('/nolog')
            debug("SQL*Plus execution starts")
            BackupLogger.close()
            p = Popen(args, stdout=f, stderr=f, stdin=PIPE)
            p.communicate(input=finalscript)
            BackupLogger.init()
            if p.returncode != 0:
                error("SQL*Plus exited with code %d" % p.returncode)
                raise Exception('sqlplus', "sqlplus exited with code %d" % p.returncode)
            else:
                debug("SQL*Plus execution successful")
            if silent:
                f.seek(0,0)
                return f.read()

    def sqlldr(self, login, finalscript):
        self._setenv()
        debug("SQLLDR execution starts")
        f1 = mkstemp(suffix=".ctl")
        ftmp = os.fdopen(f1[0], "w")
        ftmp.write(finalscript)
        ftmp.close()
        f2 = mkstemp(suffix=".log")
        os.close(f2[0])
        with TemporaryFile() as f:
            # Added direct=true to work around sqlld hang after upgrading to 12c PDB
            p = Popen([os.path.join(self.oraclehome, 'bin', 'sqlldr'), login, "control=%s" % f1[1], "log=%s" % f2[1], "errors=0", "silent=all"], stdout=f, stderr=None, stdin=None)
            p.communicate()
            if p.returncode != 0:
                error("SQLLDR exited with code %d" % p.returncode)
                raise Exception('sqlldr', "sqlldr exited with code %d" % p.returncode)
            else:
                debug("SQLLDR execution successful")
        os.unlink(f1[1])
        os.unlink(f2[1])
