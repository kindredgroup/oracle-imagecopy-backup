import os, logging, sys
from tempfile import mkstemp, TemporaryFile
from datetime import datetime, timedelta
from time import sleep
from subprocess import Popen, PIPE
from abc import ABCMeta, abstractmethod
from ConfigParser import SafeConfigParser
from string import Template

# Directory where the executable script is located
def scriptpath():
    scriptpath = os.path.dirname(os.path.realpath(__file__))
    return scriptpath

def getconfigname():
    return Configuration.getconfigname()

def info(msg):
    if BackupLogger.log is not None:
      BackupLogger.log.info(msg)

def debug(msg):
    if BackupLogger.log is not None:
        BackupLogger.log.debug(msg)

def error(msg):
    if BackupLogger.log is not None:
        BackupLogger.log.error(msg)

def exception(msg):
    if BackupLogger.log is not None:
        BackupLogger.log.exception(msg)

def size2str(size):
    # Number of bytes to human readable string
    sint = int(size)
    if sint >= 1099511627776:
        return "%dTB" % round(sint/1099511627776)
    elif sint >= 1073741824:
        return "%dGB" % round(sint/1073741824)
    elif sint >= 1048576:
        return "%dMB" % round(sint/1048576)
    elif sint >= 1024:
        return "%dkB" % round(sint/1024)
    else:
        return "%dB" % sint

def create_snapshot_class(configsection):
    snapclassname = Configuration.get('snapperclass', 'generic')
    snapmod = __import__(Configuration.get('snappermodule', 'generic'), globals(), locals(), [snapclassname])
    snapclass = getattr(snapmod, snapclassname)
    return snapclass(configsection)

class Configuration:
    configfilename = None
    _config = None
    substitutions = {}
    defaultsection = None
    _defaults = {'registercatalog': 'false', 'hasdataguard': 'false',
                 'dosnapshot': 'true', 'gimanaged': 'true',
                 'schedulebackup': 'FREQ=DAILY', 'schedulearchlog': 'FREQ=HOURLY;INTERVAL=6',
                 'snapexpirationmonths': 0, 'backupjobenabled': 'true', 'sectionsize': ''}

    @classmethod
    def getconfigname(cls):
        configfile = ""
        if os.getenv('BACKUPCONFIG'):
            # Configuration file is supplied by environment variable
            configfile = os.getenv('BACKUPCONFIG')
        elif os.path.isfile(os.path.join(scriptpath(), 'backup.cfg')):
            configfile = 'backup.cfg'
        return configfile

    @classmethod
    def init(cls, defaultsection = None, additionaldefaults = None, configfilename = None):
        if configfilename is None:
            cls.configfilename = os.path.join(scriptpath(), getconfigname())
        else:
            cls.configfilename = os.path.join(scriptpath(), configfilename)
        if not os.path.isfile(cls.configfilename):
            raise Exception('configfilenotfound', "Configuration file %s not found" % cls.configfilename)
        cls.defaultsection = defaultsection
        if os.getenv('ORACLE_HOME'):
            cls._defaults.update({'oraclehome': os.getenv('ORACLE_HOME')})
        if additionaldefaults is not None:
            cls._defaults.update(additionaldefaults)
        cls._config = SafeConfigParser(cls._defaults)
        cls._config.read(cls.configfilename)
        if not os.getenv('BACKUPCONFIG'):
            os.environ['BACKUPCONFIG'] = cls.configfilename

    @classmethod
    def sections(cls):
        return cls._config.sections()

    @classmethod
    def get(cls, parameter, section=None):
        if section is not None:
            try:
                return cls._config.get(cls.defaultsection, "%s%s" % (section, parameter))
            except:
                return cls._config.get(section, parameter)
        else:
            return cls._config.get(cls.defaultsection, parameter)

    @classmethod
    def get2(cls, parameter, section=None):
        s = Template(cls.get(parameter, section))
        return s.substitute(cls.substitutions)

class BackupTemplate(object):

    def __init__(self, filename):
        self._configpath = os.path.join(scriptpath(), filename)
        if not os.path.isfile(self._configpath):
            raise Exception('templatefilenotfound', "Template file %s not found" % self._configpath)
        self._config = SafeConfigParser()
        self._config.read(self._configpath)

    def get(self, entry):
        s = Template(self._config.get('template', entry))
        return s.substitute(Configuration.substitutions)

class BackupLogger:
    log = None
    logfile = None
    _logdir = None
    config = None
    _cleaned = False

    @classmethod
    def init(cls, logfile=None, config=None):
        # Initialize/reinitialize all loggers
        if config is not None:
            cls.config = config
            if cls.log is not None:
                cls.close(True)
                del cls.log
                cls.log = None
        if logfile is not None:
            cls.logfile = logfile
            cls._logdir = os.path.dirname(cls.logfile)
            if not os.path.exists(cls._logdir):
                os.makedirs(cls._logdir)
        if cls.log is None:
            cls.log = logging.getLogger(config)
            cls.log.setLevel(logging.DEBUG)
            streamformatter = logging.Formatter('%(asctime)s %(levelname)-8s %(name)-15s %(message)s', '%H:%M:%S')
            mystream = logging.StreamHandler(sys.stdout)
            mystream.setFormatter(streamformatter)
            mystream.setLevel(logging.INFO)
            cls.log.addHandler(mystream)
        myformatter = logging.Formatter('%(asctime)s %(levelname)-8s %(name)-15s %(message)s')
        myhandler = logging.FileHandler(cls.logfile)
        myhandler.setFormatter(myformatter)
        cls.log.addHandler(myhandler)

    @classmethod
    def close(cls, closeall=False):
        # This just closes the FileHandlers so we could write to the log file from rman/sqlplus directly
        handlers = cls.log.handlers[:]
        for handler in handlers:
            if closeall or handler.__class__ is logging.FileHandler:
                handler.flush()
                handler.close()
                cls.log.removeHandler(handler)

    @classmethod
    def clean(cls):
        if not cls._cleaned:
            retentiondays = int(Configuration.get('logretention', 'generic'))
            # Clear old logfiles
            for fname in os.listdir(cls._logdir):
                if fname[-4:] == ".log":
                    fullpath = os.path.join(cls._logdir, fname)
                    if os.path.isfile(fullpath) and ( datetime.now() - datetime.fromtimestamp(os.path.getmtime(fullpath)) > timedelta(days=retentiondays) ):
                        if cls.log is not None:
                            cls.log.debug("Removing log: %s" % fullpath)
                        os.remove(fullpath)
            cls._cleaned = True

class BackupLock(object):

    def _createlock(self):
        if not os.path.exists(self._lockfile):
            try:
                os.link(self._tmplockfile, self._lockfile)
                return True
            except:
                info("Getting lock %s failed!" % self._lockfile)
                return False
        else:
            info("Locked! File %s exists." % self._lockfile)
            return False

    def __init__(self, lockdir, maxlockwait=30):
        self._lockfile = os.path.join(lockdir, 'backup.lck')
        tmpf,self._tmplockfile = mkstemp(suffix='.lck', dir=lockdir)
        # Add here some more useful information about the locker
        os.write(tmpf, "%s\n%s\n%d" % (os.uname(), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), os.getpid()) )
        os.close(tmpf)
        # Try getting a lock
        lockstart = datetime.now()
        locksuccess = False
        while (datetime.now() - lockstart < timedelta(minutes=maxlockwait)):
            if self._createlock():
                locksuccess = True
                break
            else:
                sleep(5)
        if not locksuccess:
            error("Did not manage to get the lock in time.")
            sys.exit(2)

    def release(self):
        if os.path.exists(self._lockfile):
            os.remove(self._lockfile)
        if os.path.exists(self._tmplockfile):
            os.remove(self._tmplockfile)

class SnapHandler(object):
    __metaclass__ = ABCMeta
    configname = None

    @abstractmethod
    def __init__(self, configname):
        self.configname = configname

    @abstractmethod
    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        pass

    @abstractmethod
    def snap(self):
        # Must return snapshot ID
        pass

    @abstractmethod
    def dropsnap(self, snapid):
        pass

    @abstractmethod
    def getsnapinfo(self, snapstruct):
        # s is element in list that listsnapshots returns
        # Must return dict with elements id (string), creation (with type datetime in UTC), numclones (int), space_total (int in bytes), space_unique (int in bytes)
        pass

    def snap2str(self, s):
        # Convert the snap information to one nice string value
        # Input must come from getsnapinfo
        return "%s [%s UTC] total=%s unique=%s clones=%s" % (s["id"], s["creation"], size2str(s["space_total"]), size2str(s["space_unique"]), s["numclones"])

    def clone2str(self, s):
        # Convert clone information to a nice string value
        return "%s [%s] [mount point: %s]" % (s["clonename"], s["origin"], s["mountpoint"])

    @abstractmethod
    def clone(self, snapid, clonename):
        pass

    @abstractmethod
    def dropclone(self, cloneid):
        pass

    @abstractmethod
    def filesystem_info(self, filesystemname=None):
        # Must return dict with the following information about a (cloned) volume/filesystem
        # origin - parent volume name of the clone
        # clonename - name of the clone volume
        # mountpoint - storage system mount path for this volume
        pass

    @abstractmethod
    def listclones(self):
        # Array of dicts that lists all clones of the parent volume
        # origin - parent volume name of the clone
        # clonename - name of the clone volume
        # mountpoint - storage system mount path for this volume
        pass

    @abstractmethod
    def mountstring(self, filesystemname):
        # Outputs string on how to mount the volume/filesystem
        # For example:
        # 10.10.10.10:/clonename
        pass

    @abstractmethod
    def createvolume(self):
        # Creates new volume for storing backups
        pass

    def clean(self):
        max_age_days = int(Configuration.get('snapexpirationdays'))
        max_age_months = int(Configuration.get('snapexpirationmonths'))
        sorted_snaps = self.listsnapshots(sortbycreation=True)
        output = []
        number_of_snaps = len(sorted_snaps)
        for idx, snapstruct in enumerate(sorted_snaps):
          s = self.getsnapinfo(snapstruct)
          d = s["creation"]
          age = datetime.utcnow() - d
          status = "valid"
          drop_allowed = False
          dropped = False
          # Check snap expiration
          if age > timedelta(days=max_age_days):
            if age > timedelta(days=max_age_months*31):
              # Drop is allowed if monthly expiration has also passed
              drop_allowed = True
            else:
              if idx+1 < number_of_snaps:
                # The last snap of each month is retained
                previnfo = self.getsnapinfo(sorted_snaps[idx+1])
                drop_allowed = str(s["creation"])[0:7] == str(previnfo["creation"])[0:7]
          if drop_allowed and s["numclones"] != 0:
            status = "has a clone"
            drop_allowed = False
          # Do the actual drop
          if drop_allowed:
            try:
              self.dropsnap(s["id"])
              dropped = True
              status = "dropped"
            except:
              status = "DROP FAILED"
          yield {'snapid': s["id"], 'dropped': dropped, 'status': status, 'infostring': "%s %s" % (self.snap2str(s), status)}

    def autoclone(self):
        # Returns source snap id
        maxsnapage = timedelta(hours = int(Configuration.get('autorestoresnapage', 'autorestore')), minutes=0 )
        # Find the snap for cloning
        sorted_snaps = self.listsnapshots(sortbycreation=True, sortreverse=True)
        sourcesnap = None
        for idx, snaprecord in enumerate(sorted_snaps):
            s = self.getsnapinfo(snaprecord)
            d = s["creation"]
            age = datetime.utcnow() - d
            if age >= maxsnapage:
                sourcesnap = s["id"]
                break
        if sourcesnap is None:
            raise Exception('snap','Suitable snapshot not found for cloning.')
        else:
            # Clone the snap
            debug("Snapshot id for autoclone: %s" % sourcesnap)
            self.clone(sourcesnap, Configuration.get('autorestoreclonename', 'autorestore'))
            return sourcesnap

    def dropautoclone(self):
        self.dropclone(Configuration.get('autorestoreclonename', 'autorestore'))

    # Finds the correct snapshot to clone based on restore target time
    # Targettime must be in UTC
    def search_recovery_snapid(self, targettime):
        sorted_snaps = self.listsnapshots(sortbycreation=True, sortreverse=False)
        sourcesnap = None
        for idx, snaprecord in enumerate(sorted_snaps):
            s = self.getsnapinfo(snaprecord)
            if s['creation'].replace(tzinfo=None) >= targettime.replace(tzinfo=None):
                sourcesnap = s['id']
                break
        return sourcesnap

# Class for outputting some UI elements, like prompts
class UIElement(object):

    def __init__(self):
        pass
    
    def _is_dir_writable(self, path):
        try:
            f = TemporaryFile(dir = path)
            f.close()
        except OSError as e:
            if e.errno == errno.EACCES:
                return False
            e.filename = path
            raise
        return True
    
    def ask_directory(self, question, demand_empty=True, demand_writable=True):
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
            if demand_writable and not self._is_dir_writable(path):
                print "Specified path is not writable"
                continue
            if demand_empty and os.listdir(path):
                print "Specified path must be empty"
                continue
            break
        return path
    
    def ask_yn(self, question):
        answer = None
        while True:
            answer = raw_input("%s? (y/n) " % question)
            answer = answer.strip().upper()
            if answer not in ['Y','N']:
                print "Invalid input"
                continue
            break
        return answer
    
    def ask_timestamp(self, question):
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
    
    def ask_string(self, question, maxlength=None, onlyalnum=False):
        answer = None
        while True:
            answer = raw_input("%s " % question)
            answer = answer.strip()
            if maxlength is not None and len(answer) > maxlength:
                print "Max %d characters allowed" % maxlength
                continue
            if onlyalnum and not answer.isalnum():
                print "Only alphanumeric characters allowed" % maxlength
                continue
            break
        return answer
    
    def ask_size(self, question):
        answer = None
        while True:
            answer = raw_input("%s (suffix with unit M, G or T): " % question)
            answer = answer.strip().upper()
            if answer[-1:] not in ['M','G','T']:
                print "Suffix your input with unit M, G or T"
                continue
            if not answer[:-1].isdigit():
                print "%s is not an integer" % answer[:-1]
                continue
            break
        return answer
