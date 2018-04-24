import os, operator
from backupcommon import SnapHandler, Configuration, scriptpath, info, error, debug, UIElement
from ConfigParser import SafeConfigParser, NoOptionError
from datetime import datetime, timedelta
from NaServer import *

class Netapp(SnapHandler):
    _exceptionbase = "netapp"
    _blocksize = 1024
    _filer = None
    _cacert = None

    def _read_netapp_config(self, attribute, zfscredconfig):
        # Try reading Netapp configuration first from the credentials file, then fail over to main configuration file
        value = None
        try:
            return zfscredconfig.get('netapp', attribute)
        except NoOptionError:
            pass
        try:
            return Configuration.get(attribute, 'netapp')
        except NoOptionError:
            raise NoOptionError("Attribute %s not found" % attribute)
        
    def __init__(self, configname):
        zfscredfilename = os.path.join(scriptpath(), 'netappcredentials.cfg')
        if not os.path.isfile(zfscredfilename):
            raise Exception(self._exceptionbase, "Configuration file %s not found" % zfscredfilename)
        # Authentication information
        zfscredconfig = SafeConfigParser()
        zfscredconfig.read(zfscredfilename)
        #
        self._filer = self._read_netapp_config('filer', zfscredconfig)
        self._srv = NaServer(self._filer, 1, 1)
        # Check if CA certificate validation is needed
        try:
            self._cacert = os.path.join(scriptpath(), 'certs', self._read_netapp_config('cacert', zfscredconfig))
        except NoOptionError:
            self._cacert = None
        if self._cacert:
            self._srv.set_ca_certs(self._cacert)
            self._srv.set_server_cert_verification(True)
            self._srv.set_hostname_verification(False)
        #
        self._srv.set_admin_user(zfscredconfig.get('netappcredentials','user'), zfscredconfig.get('netappcredentials','password'))
        self._volprefix = self._read_netapp_config('volumeprefix', zfscredconfig)
        self._volname = "%s%s" % (self._volprefix, configname)
        super(Netapp, self).__init__(configname)

    def _check_netapp_error(self, output, errmsg):
        if output.results_errno() != 0:
            raise Exception(self._exceptionbase, "%s. %s" % (errmsg, output.results_reason()))
    
    def _volsize_to_num(self, volsize):
        unit = volsize[-1].lower()
        factor = 1
        if unit == "m":
            factor = 20
        elif unit == "g":
            factor = 30
        elif unit == "t":
            factor = 40
        elif unit == "p":
            factor = 50
        return round(float(volsize[:-1])*(2**factor))

    # Public interfaces
    def filesystem_info(self, filesystemname=None):
        info = {}
        output = self._srv.invoke("volume-clone-get", "volume", filesystemname)
        self._check_netapp_error(output, "Getting clone info failed")
        attrlist = output.child_get("attributes")
        if (attrlist is None or attrlist == ""):
            raise Exception(self._exceptionbase, "No attributes found for clone.")
        for ss in attrlist.children_get():
            info['origin'] = ss.child_get_string('parent-volume')
            info['clonename'] = filesystemname
            info['mountpoint'] = ss.child_get_string('junction-path')
        return info

    def listclones(self):
        elem = NaElement("volume-clone-get-iter")
        elem.child_add_string("max-records", "50")
        query = NaElement("query")
        query.child_add_string("parent-volume", self._volname)
        elem.child_add(query)
        #
        output = self._srv.invoke_elem(elem)
        self._check_netapp_error(output, "List clones failed")
        attrlist = output.child_get("attributes-list")
        if (attrlist is not None and attrlist):
            for ss in attrlist.children_get():
                info = {}
                info['origin'] = ss.child_get_string('parent-volume')
                info['clonename'] = ss.child_get_string('volume')
                info['mountpoint'] = ss.child_get_string('junction-path')
                yield info

    def mountstring(self, filesystemname):
        info = self.filesystem_info(filesystemname)
        return "%s:%s" % (self._filer, info['mountpoint'])

    def snap(self):
        snapname = "%s_%s" % (self._volname, datetime.now().strftime('%Y%m%dT%H%M%S'))
        output = self._srv.invoke("snapshot-create", "volume", self._volname, "snapshot", snapname)
        self._check_netapp_error(output, "Creating snapshot failed")
        return snapname

    def dropsnap(self, snapid):
        output = self._srv.invoke("snapshot-delete", "volume", self._volname, "snapshot", snapid)
        self._check_netapp_error(output, "Failed to drop snapshot %s" % snapid)

    def getsnapinfo(self, snapstruct):
        return snapstruct

    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        output = self._srv.invoke("volume-size", "volume", self._volname)
        self._check_netapp_error(output, "Failed to get volume size information")
        volsize = self._volsize_to_num(output.child_get_string("volume-size"))
        pct_limit = round(2147483648*100/(volsize/self._blocksize))
        output = self._srv.invoke("snapshot-list-info", "volume", self._volname)
        self._check_netapp_error(output, "Failed to list snapshots")
        snapshotlist = output.child_get("snapshots")
        snapshots = []
        if (snapshotlist is not None and snapshotlist):
            for ss in snapshotlist.children_get():
                snapshots.append( {'id': ss.child_get_string("name"),
                    'creation': datetime.utcfromtimestamp(float(ss.child_get_int("access-time"))),
                    'numclones':  1 if ss.child_get_string("busy") == "true" else 0,
                    'space_total': ss.child_get_int("cumulative-total")*self._blocksize if ss.child_get_int("cumulative-percentage-of-total-blocks") < pct_limit else round(volsize*ss.child_get_int("cumulative-percentage-of-total-blocks")/100),
                    'space_unique': ss.child_get_int("total")*self._blocksize if ss.child_get_int("percentage-of-total-blocks") < pct_limit else round(volsize*ss.child_get_int("percentage-of-total-blocks")/100) 
                } )
        if not sortbycreation:
            return snapshots
        else:
            return sorted(snapshots, key=operator.itemgetter('creation'), reverse=sortreverse)

    def clone(self, snapid, clonename):
        output = self._srv.invoke("volume-clone-create", "parent-volume", self._volname, "parent-snapshot", snapid, "volume", clonename)
        self._check_netapp_error(output, "Creating clone failed")
        output = self._srv.invoke("volume-mount", "junction-path", "/%s" % clonename, "volume-name", clonename)
        self._check_netapp_error(output, "Mounting clone failed")
        output = self._srv.invoke("volume-set-option", "option-name", "nosnapdir", "option-value", "on", "volume", clonename)
        self._check_netapp_error(output, "Setting attribute on clone failed")

    def dropclone(self, cloneid):
        info = self.filesystem_info(cloneid)
        if info['origin'] != self._volname:
            raise Exception(self._exceptionbase, "This clone does not belong to parent %s" % self._volname)
        output = self._srv.invoke("volume-unmount", "volume-name", cloneid)
        self._check_netapp_error(output, "Unmounting volume %s failed" % cloneid)
        output = self._srv.invoke("volume-offline", "name", cloneid)
        self._check_netapp_error(output, "Offlining volume %s failed" % cloneid)
        output = self._srv.invoke("volume-destroy", "name", cloneid)
        self._check_netapp_error(output, "Dropping volume %s failed" % cloneid)

    def createvolume(self):
        uid = os.getuid()
        gid = os.getgid()
        permissions = "0770"
        #
        ui = UIElement()
        aggregate = ui.ask_string("Aggregate name:", 50)
        volume_size = ui.ask_size("Volume size:")
        path = ui.ask_string("Parent namespace:", 50)
        export_policy = ui.ask_string("Export policy:", 50)
        #
        output = self._srv.invoke(
            "volume-create", "volume", self._volname, "containing-aggr-name", aggregate, "efficiency-policy", "default", "export-policy", export_policy, 
            "group-id", gid, "user-id", os.getuid(), "unix-permissions", permissions, 
            "junction-path", os.path.join(path, self.configname), "percentage-snapshot-reserve", 0, "size", volume_size, "volume-state", "online")
        self._check_netapp_error(output, "Creating volume failed")        
        #
        rootelem = NaElement("volume-modify-iter")
        attrelem1 = NaElement("attributes")
        attrelem = NaElement("volume-attributes")
        attrelem1.child_add(attrelem)
        queryelem1 = NaElement("query")
        queryelem = NaElement("volume-attributes")
        queryelem1.child_add(queryelem)
        volid = NaElement("volume-id-attributes")
        volid.child_add_string("name", self._volname)
        queryelem.child_add(volid)
        snapattr = NaElement("volume-snapshot-attributes")
        snapattr.child_add_string("auto-snapshots-enabled", "false")
        snapattr.child_add_string("snapdir-access-enabled", "false")
        autosizeattr = NaElement("volume-autosize-attributes")
        autosizeattr.child_add_string("mode", "grow")
        attrelem.child_add(snapattr)
        attrelem.child_add(autosizeattr)
        rootelem.child_add(attrelem1)
        rootelem.child_add(queryelem1)
        rootelem.child_add_string("max-records", "1")
        output = self._srv.invoke_elem(rootelem)
        self._check_netapp_error(output, "Setting volume options failed.")
        print "Volume created. Please disable automatic snapshot creation through GUI, for some reason it does not work through API."
