import os, sys
from tempfile import mkstemp, TemporaryFile
from datetime import datetime, timedelta
from backupcommon import BackupLogger, info, debug, error, exception, Configuration, BackupTemplate, create_snapshot_class, scriptpath
from oraexec import OracleExec

class RestoreDB(object):
    _restoretemplate = None
    _sourcesnapid = 'unknown'
    _mountdest = None
    _restoredest = None
    _exec = None
    _snap = None
    _dbparams = {}

    def __init__(self, configname):
        self._restoretemplate = BackupTemplate('restoretemplate.cfg')
        self._snap = create_snapshot_class(database)
        self._initfile = restoretemplate.get('initoralocation')

    def set_mount_path(self, mountpath):
        if mountdest is None or not os.path.exists(mountdest) or not os.path.isdir(mountdest):
            raise Exception('restore', "Mount directory %s not found or is not a proper directory" % mountdest)
        self._mountdest = mountpath

    def set_restore_path(self, restoredest):
        if restoredest is None or not os.path.exists(restoredest) or not os.path.isdir(restoredest):
            raise Exception('restore', "Restore directory %s not found or is not a proper directory" % restoredest)
        self._restoredest = restoredest

    # Helpers for executing Oracle commands
    def _exec_rman(self, commands):
        finalscript = "%s\n%s\n%s" % (self._restoretemplate.get('rmanheader'), commands, self._restoretemplate.get('rmanfooter'))
        self._exec.rman(finalscript)

    def _exec_sqlplus(self, commands, headers=True, returnoutput=False):
        if headers:
            finalscript = "%s\n%s\n%s" % (self._restoretemplate.get('sqlplusheader'), commands, self._restoretemplate.get('sqlplusfooter'))
        else:
            finalscript = commands
        return self._exec.sqlplus(finalscript, silent=returnoutput)

    # Restore actions
    def _createinitora(self):
        filename = self._initfile
        with open(filename, 'w') as f:
            contents = self._restoretemplate.get('autoinitora')
            if 'cdb' in Configuration.substitutions and Configuration.substitutions['cdb'].upper() == 'TRUE':
                contents+= self._restoretemplate.get('cdbinitora')
            debug("ACTION: Generated init file %s\n%s" % (filename, contents))
            f.write(contents)

    def _clone(self):
        self._sourcesnapid = self._snap.autoclone()

    def _mount(self):
        check_call(['mount', self._mountdest])

    def _unmount(self):
        check_call(['umount', self._mountdest])

    def _set_parameters(self):
        dbconfig = SafeConfigParser()
        dbconfig.read(os.path.join(self._mountdest, 'autorestore.cfg'))
        self._dbparams['dbname'] = dbconfig.get('dbparams','db_name')
        self._dbparams['restoretarget'] = datetime.strptime(dbconfig.get('dbparams','lasttime'), '%Y-%m-%d %H:%M:%S')
        Configuration.substitutions.update({
            'db_name': self._dbparams['dbname'],
            'db_compatible': dbconfig.get('dbparams','compatible'),
            'db_files': dbconfig.get('dbparams','db_files'),
            'db_undotbs': dbconfig.get('dbparams','undo_tablespace'),
            'db_block_size': dbconfig.get('dbparams','db_block_size'),
            'lastscn': dbconfig.get('dbparams','lastscn'),
            'lasttime': dbconfig.get('dbparams','lasttime'),
            'dbid': Configuration.get('dbid', database),
            'instancenumber': Configuration.get('autorestoreinstancenumber', database),
            'thread': Configuration.get('autorestorethread', database),
            'backupfinishedtime': dbconfig.get('dbparams','backup-finished'),
            'bctfile': dbconfig.get('dbparams','bctfile'),
            'initora': self._initfile,
            'autorestoredestination': self._restoredest,
            'mountdestination': self._mountdest,
        })
        try:
            Configuration.substitutions.update({'cdb': dbconfig.get('dbparams','enable_pluggable_database')})
        except:
            Configuration.substitutions.update({'cdb': 'FALSE'})

    # Orchestrator

    def run(self):
        success = False
        self._set_parameters()
        self._exec = OracleExec(oraclehome=Configuration.get('oraclehome', 'generic'),
            tnspath=os.path.join(scriptpath(), Configuration.get('tnsadmin', 'generic')),
            sid=self._dbparams['dbname'])
        self._clone()
        self._set_parameters()
        try:
            self._mount()
        except:
            self._restore_cleanup
            raise Exception('restore', 'Mount failed')
        self._createinitora()
        #
        debug('ACTION: startup nomount')
        exec_sqlplus(restoretemplate.get('startupnomount'))
        debug('ACTION: mount database and catalog files')
        exec_rman(restoretemplate.get('mountandcatalog'))
        if bctfile:
          debug('ACTION: disable block change tracking')
          exec_sqlplus(restoretemplate.get('disablebct'))
        debug('ACTION: create missing datafiles')
        output = exec_sqlplus(restoretemplate.get('switchdatafiles'), returnoutput=True)
        switchdfscript = ""
        for line in output.splitlines():
          if line.startswith('RENAMEDF-'):
            switchdfscript+= "%s\n" % line.strip()[9:]
        debug('ACTION: switch and recover')
        exec_rman("%s\n%s" % (switchdfscript, restoretemplate.get('recoverdatafiles')))
        #
        self.cleanup()

    def verify(self):
        debug('ACTION: opening database to verify the result')
        maxtolerance = timedelta(minutes=int(Configuration.get('autorestoremaxtoleranceminutes','autorestore')))
        verifytime = None
        Configuration.substitutions.update({
            'customverifydate': Configuration.get('customverifydate', database),
        })
        output = self._exec_sqlplus(restoretemplate.get('openandverify'), returnoutput=True)
        for line in output.splitlines():
            if line.startswith('CUSTOM VERIFICATION TIME:'):
                verifytime = datetime.strptime(line.split(':', 1)[1].strip(), '%Y-%m-%d %H:%M:%S')
        if verifytime is None:
            raise Exception('restore', 'Reading verification time failed.')
        #lastrestoretime = datetime.strptime(dbconfig.get('dbparams','lasttime'), '%Y-%m-%d %H:%M:%S')
        verifydiff = self._dbparams['restoretarget'] - verifytime
        verifyseconds = int(verifydiff.seconds + verifydiff.days * 24 * 3600)
        debug("Expected time: %s" % self._dbparams['restoretarget'])
        debug("Verified time: %s" % verifytime)
        debug("VERIFY: Time difference %s" % verifydiff)
        if verifydiff > maxtolerance:
            raise Exception('restore', "Verification time difference %s is larger than allowed tolerance %s" % (verifydiff, maxtolerance))

    def blockcheck(self):
        info("ACTION: Validating database for corruptions")
        # The following command will introduce some corruption to test database validation
        # check_call(['dd','if=/dev/urandom','of=/nfs/autorestore/mnt/data_D-ORCL_I-1373437895_TS-SOE_FNO-5_0sqov4pv','bs=8192','count=10','seek=200','conv=notrunc' ])
        try:
            self._exec_rman(restoretemplate.get('validateblocks'))
        finally:
            self._exec_sqlplus(restoretemplate.get('showcorruptblocks'))

    def cleanup(self):
        try:
            debug('ACTION: In case instance is still running, aborting it')
            self._exec_sqlplus(self._restoretemplate.get('shutdownabort'))
        except:
            pass
        self._unmount()
        self._snap.dropautoclone()
        self._exec = None
