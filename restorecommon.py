import os, sys
from tempfile import mkstemp, TemporaryFile
from datetime import datetime, timedelta
from backupcommon import BackupLogger, info, debug, error, exception, Configuration, BackupTemplate, create_snapshot_class, scriptpath
from oraexec import OracleExec

class RestoreDB(object):
    _restoretemplate = None
    _exec = None

    def __init__(self, configname):
        self._restoretemplate = BackupTemplate('restoretemplate.cfg')
        self._exec = OracleExec(oraclehome=Configuration.get('oraclehome', 'generic'), tnspath=os.path.join(scriptpath(), Configuration.get('tnsadmin', 'generic')))

    # Helpers for executing Oracle commands
    def _exec_rman(self, commands):
        finalscript = "%s\n%s\n%s" % (self._restoretemplate.get('rmanheader'), commands, self._restoretemplate.get('rmanfooter'))
        _exec.rman(finalscript)

    def _exec_sqlplus(self, commands, headers=True, returnoutput=False):
        if headers:
            finalscript = "%s\n%s\n%s" % (self._restoretemplate.get('sqlplusheader'), commands, self._restoretemplate.get('sqlplusfooter'))
        else:
            finalscript = commands
        return _exec.sqlplus(finalscript, silent=returnoutput)

    # Restore actions
    def _createinitora(self, filename):
        with open(filename, 'w') as f:
            contents = self._restoretemplate.get('autoinitora')
            if 'cdb' in Configuration.substitutions and Configuration.substitutions['cdb'].upper() == 'TRUE':
                contents+= self._restoretemplate.get('cdbinitora')
            debug("ACTION: Generated init file %s\n%s" % (filename, contents))
            f.write(contents)
