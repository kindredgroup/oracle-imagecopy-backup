import os, sys
from tempfile import mkstemp, TemporaryFile
from datetime import datetime, timedelta
from backupcommon import BackupLogger, info, debug, error, exception, Configuration, BackupTemplate, create_snapshot_class, scriptpath
from oraexec import OracleExec
from ConfigParser import SafeConfigParser
from subprocess import check_call

class RestoreDB(object):
    _restoretemplate = None
    _sourcesnapid = 'unknown'
    _mountdest = None
    _restoredest = None
    _exec = None
    _snap = None
    _dbparams = {}
    _configname = None
    _validatecorruption = False
    verifyseconds = -1
    sourcesnapid = ""
    _successful_clone = False
    _successful_mount = False

    def __init__(self, configname):
        self._restoretemplate = BackupTemplate('restoretemplate.cfg')
        self._configname = configname
        self._snap = create_snapshot_class(configname)

    def set_mount_path(self, mountdest):
        if mountdest is None or not os.path.exists(mountdest) or not os.path.isdir(mountdest):
            raise Exception('restore', "Mount directory %s not found or is not a proper directory" % mountdest)
        self._mountdest = mountdest

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
        self.sourcesnapid = self._snap.autoclone()
        self._successful_clone = True

    def _mount(self):
        check_call(['mount', self._mountdest])
        self._successful_mount = True

    def _unmount(self):
        check_call(['umount', self._mountdest])

    def _set_parameters(self):
        dbconfig = SafeConfigParser()
        dbconfig.read(os.path.join(self._mountdest, 'autorestore.cfg'))
        self._dbparams['dbname'] = dbconfig.get('dbparams','db_name')
        self._dbparams['restoretarget'] = datetime.strptime(dbconfig.get('dbparams','lasttime'), '%Y-%m-%d %H:%M:%S')
        self._dbparams['bctfile'] = dbconfig.get('dbparams','bctfile')
        Configuration.substitutions.update({
            'db_name': self._dbparams['dbname'],
            'db_compatible': dbconfig.get('dbparams','compatible'),
            'db_files': dbconfig.get('dbparams','db_files'),
            'db_undotbs': dbconfig.get('dbparams','undo_tablespace'),
            'db_block_size': dbconfig.get('dbparams','db_block_size'),
            'lastscn': dbconfig.get('dbparams','lastscn'),
            'lasttime': dbconfig.get('dbparams','lasttime'),
            'dbid': Configuration.get('dbid', self._configname),
            'instancenumber': Configuration.get('autorestoreinstancenumber', self._configname),
            'thread': Configuration.get('autorestorethread', self._configname),
            'backupfinishedtime': dbconfig.get('dbparams','backup-finished'),
            'bctfile': self._dbparams['bctfile'],
            'autorestoredestination': self._restoredest,
            'mountdestination': self._mountdest,
        })
        try:
            Configuration.substitutions.update({'cdb': dbconfig.get('dbparams','enable_pluggable_database')})
        except:
            Configuration.substitutions.update({'cdb': 'FALSE'})
        self._initfile = self._restoretemplate.get('initoralocation')
        Configuration.substitutions.update({
            'initora': self._initfile,
        })

    # Orchestrator

    def run(self):
        self.starttime = datetime.now()
        info("Starting to restore")
        #
        success = False
        self._clone()
        try:
            self._mount()
        except:
            self.cleanup()
            raise Exception('restore', 'Mount failed')
        self._set_parameters()
        self._createinitora()
        self._exec = OracleExec(oraclehome=Configuration.get('oraclehome', 'generic'),
            tnspath=os.path.join(scriptpath(), Configuration.get('tnsadmin', 'generic')),
            sid=self._dbparams['dbname'])
        #
        debug('ACTION: startup nomount')
        self._exec_sqlplus(self._restoretemplate.get('startupnomount'))
        debug('ACTION: mount database and catalog files')
        self._exec_rman(self._restoretemplate.get('mountandcatalog'))
        if self._dbparams['bctfile']:
          debug('ACTION: disable block change tracking')
          self._exec_sqlplus(self._restoretemplate.get('disablebct'))
        debug('ACTION: create missing datafiles')
        output = self._exec_sqlplus(self._restoretemplate.get('switchdatafiles'), returnoutput=True)
        switchdfscript = ""
        for line in output.splitlines():
          if line.startswith('RENAMEDF-'):
            switchdfscript+= "%s\n" % line.strip()[9:]
        debug('ACTION: switch and recover')
        self._exec_rman("%s\n%s" % (switchdfscript, self._restoretemplate.get('recoverdatafiles')))

    def verify(self):
        debug('ACTION: opening database to verify the result')
        maxtolerance = timedelta(minutes=int(Configuration.get('autorestoremaxtoleranceminutes','autorestore')))
        verifytime = None
        Configuration.substitutions.update({
            'customverifydate': Configuration.get('customverifydate', self._configname),
        })
        output = self._exec_sqlplus(self._restoretemplate.get('openandverify'), returnoutput=True)
        for line in output.splitlines():
            if line.startswith('CUSTOM VERIFICATION TIME:'):
                verifytime = datetime.strptime(line.split(':', 1)[1].strip(), '%Y-%m-%d %H:%M:%S')
        if verifytime is None:
            raise Exception('restore', 'Reading verification time failed.')
        #lastrestoretime = datetime.strptime(dbconfig.get('dbparams','lasttime'), '%Y-%m-%d %H:%M:%S')
        verifydiff = self._dbparams['restoretarget'] - verifytime
        self.verifyseconds = int(verifydiff.seconds + verifydiff.days * 24 * 3600)
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
            self._exec_rman(self._restoretemplate.get('validateblocks'))
            self._validatecorruption = True
        finally:
            self._exec_sqlplus(self._restoretemplate.get('showcorruptblocks'))

    def cleanup(self):
        try:
            debug('ACTION: In case instance is still running, aborting it')
            self._exec_sqlplus(self._restoretemplate.get('shutdownabort'))
        except:
            pass
        if self._successful_mount:
            try:
                self._unmount()
            except:
                exception("Error unmounting")
        if self._successful_clone:
            try:
                self._snap.dropautoclone()
            except:
                exception("Error dropping clone")
        self.endtime = datetime.now()
