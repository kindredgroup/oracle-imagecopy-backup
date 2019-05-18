import os, operator
from backupcommon import SnapHandler, Configuration, scriptpath, info, error, debug, UIElement
from ConfigParser import SafeConfigParser, NoOptionError, NoSectionError
from datetime import datetime, timedelta
from NaServer import *
from time import sleep

class Netapp(SnapHandler):
    _exceptionbase = "netapp"
    _blocksize = 1024
    _filer = None
    _cacert = None
    _multivol = False

    def _read_netapp_config(self, attribute, zfscredconfig):
        # Try reading Netapp configuration first from the credentials file, then fail over to main configuration file
        value = None
        try:
            return zfscredconfig.get('netapp', attribute)
        except (NoOptionError, NoSectionError) as e:
            pass
        try:
            return Configuration.get(attribute, 'netapp')
        except (NoOptionError, NoSectionError) as e:
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
        except:
            self._cacert = None
        if self._cacert:
            self._srv.set_ca_certs(self._cacert)
            self._srv.set_server_cert_verification(True)
            self._srv.set_hostname_verification(False)
        #
        self._srv.set_admin_user(zfscredconfig.get('netappcredentials','user'), zfscredconfig.get('netappcredentials','password'))
        try:
            self._mounthost = self._read_netapp_config('mounthost', zfscredconfig)  
        except:
            self._mounthost = self._filer
        # Get volume name
        self._volprefix = self._read_netapp_config('volumeprefix', zfscredconfig)
        self._volname = []
        self._volname.append("%s%s" % (self._volprefix, configname))
        try:
            for suffix in Configuration.get('additionalvolumesuffixes').split(','):
                self._volname.append("%s%s%s" % (self._volprefix, configname, suffix))
            self._multivol = True
        except NoOptionError:
            pass
        debug("List of all volumes for this database: %s" % self._volname)
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

    def _get_volume_info(self, volname):
        debug("Querying info for volume: %s" % volname)
        elem = NaElement("volume-get-iter")
        elem.child_add_string("max-records", "1")
        #
        query = NaElement("query")
        query_vol = NaElement('volume-attributes')
        query_volume_id = NaElement("volume-id-attributes")
        query_volume_id.child_add_string("name", volname)
        query_vol.child_add(query_volume_id)
        query.child_add(query_vol)
        elem.child_add(query)
        #
        attr = NaElement("desired-attributes")
        attr_vol = NaElement('volume-attributes')
        exp = NaElement("volume-export-attributes")
        exp.child_add(NaElement("policy"))
        attr_vol.child_add(exp)
        id = NaElement("volume-id-attributes")
        id.child_add(NaElement("containing-aggregate-name"))
        id.child_add(NaElement("junction-path"))
        attr_vol.child_add(id)
        attr_vol.child_add(NaElement('volume-clone-attributes'))
        attr.child_add(attr_vol)
        elem.child_add(attr)
        #
        #print(elem.sprintf())
        output = self._srv.invoke_elem(elem)
        #print(output.sprintf())
        self._check_netapp_error(output, "Getting volume information failed")
        attrlist = output.child_get("attributes-list")
        info = {}
        if (attrlist is not None and attrlist):
            for ss in attrlist.children_get():
                info['aggregate'] = ss.child_get('volume-id-attributes').child_get_string('containing-aggregate-name')
                info['mountpoint'] = ss.child_get('volume-id-attributes').child_get_string('junction-path')
                info['export-policy'] = ss.child_get('volume-export-attributes').child_get_string('policy')
                cloneattr = ss.child_get('volume-clone-attributes')
                if cloneattr and cloneattr.child_get('volume-clone-parent-attributes'):
                    info['origin'] = cloneattr.child_get('volume-clone-parent-attributes').child_get_string('name')
                else:
                    info['origin'] = None
        return info

    def _dropvolume(self, volname):
        debug("Dropping volume: %s" % volname)
        output = self._srv.invoke("volume-unmount", "volume-name", volname)
        self._check_netapp_error(output, "Unmounting volume %s failed" % volname)
        sleep(10)
        output = self._srv.invoke("volume-offline", "name", volname)
        self._check_netapp_error(output, "Offlining volume %s failed" % volname)
        sleep(10)
        output = self._srv.invoke("volume-destroy", "name", volname)
        self._check_netapp_error(output, "Dropping volume %s failed" % volname)
    
    # Public interfaces
    def filesystem_info(self, filesystemname=None):
        sourceinfo = self._get_volume_info(filesystemname)
        info = {}
        info['origin'] = sourceinfo['origin']
        info['clonename'] = filesystemname
        info['mountpoint'] = sourceinfo['mountpoint']
        return info

    def listclones(self):
        # TODO: support multiple volumes - self._volname as array
        elem = NaElement("volume-clone-get-iter")
        elem.child_add_string("max-records", "50")
        query = NaElement("query")
        query.child_add_string("parent-volume", self._volname[0])
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
        return "%s:%s" % (self._mounthost, info['mountpoint'])

    def snap(self):
        snapname = "%s_%s" % (self.configname, datetime.now().strftime('%Y%m%dT%H%M%S'))
        debug("Snapshot name: %s" % snapname)
        for vol in self._volname:
            debug("Snapshotting volume: %s" % vol)
            output = self._srv.invoke("snapshot-create", "volume", vol, "snapshot", snapname)
            self._check_netapp_error(output, "Creating snapshot failed")
        return snapname

    def dropsnap(self, snapid):
        for counter, vol in enumerate(self._volname):
            debug("Dropping snapshot on volume: %s" % vol)
            output = self._srv.invoke("snapshot-delete", "volume", vol, "snapshot", snapid)
            if counter == 0:
                # Only check netapp error on the first volume, because user may add volumes later and on the new volumes older snapshot ID-s do not exist
                self._check_netapp_error(output, "Failed to drop snapshot %s" % snapid)

    def getsnapinfo(self, snapstruct):
        return snapstruct

    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        snapshots = []
        for volname in self._volname:
            debug("Getting volume info for volume: %s" % volname)
            output = self._srv.invoke("volume-size", "volume", volname)
            self._check_netapp_error(output, "Failed to get volume size information")
            volsize = self._volsize_to_num(output.child_get_string("volume-size"))
            pct_limit = round(2147483648*100/(volsize/self._blocksize))
            output = self._srv.invoke("snapshot-list-info", "volume", volname)
            self._check_netapp_error(output, "Failed to list snapshots")
            snapshotlist = output.child_get("snapshots")
            if (snapshotlist is not None and snapshotlist):
                for ss in snapshotlist.children_get():
                    snapinfo = {'id': ss.child_get_string("name"),
                        'creation': datetime.utcfromtimestamp(float(ss.child_get_int("access-time"))),
                        'numclones':  1 if ss.child_get_string("busy") == "true" else 0,
                        'space_total': ss.child_get_int("cumulative-total")*self._blocksize if ss.child_get_int("cumulative-percentage-of-total-blocks") < pct_limit else round(volsize*ss.child_get_int("cumulative-percentage-of-total-blocks")/100),
                        'space_unique': ss.child_get_int("total")*self._blocksize if ss.child_get_int("percentage-of-total-blocks") < pct_limit else round(volsize*ss.child_get_int("percentage-of-total-blocks")/100) 
                    }
                    try:
                        existingitem = next(item for item in snapshots if item['id'] == snapinfo['id'])
                        existingitem['space_total'] += snapinfo['space_total']
                        existingitem['space_unique'] += snapinfo['space_unique']
                        existingitem['numclones'] = max(existingitem['numclones'], snapinfo['numclones'])
                    except StopIteration:
                        snapshots.append(snapinfo)
        if not sortbycreation:
            return snapshots
        else:
            return sorted(snapshots, key=operator.itemgetter('creation'), reverse=sortreverse)
    
    def clone(self, snapid, clonename):
        # Create root namespace
        if self._multivol:
            debug("Multivolume mode enabled")
            junction_prefix = "/%s" % clonename
            # Query current volume to get aggregate and export_policy
            volinfo = self._get_volume_info(self._volname[0])
            aggregate = volinfo['aggregate']
            export_policy = volinfo['export-policy']
            # Create new volume just for namespace root
            debug("Creating volume %s with junction path %s" % (clonename, junction_prefix))
            output = self._srv.invoke(
                "volume-create", "volume", clonename, "containing-aggr-name", aggregate, "efficiency-policy", "default", "export-policy", export_policy, 
                "group-id", os.getgid(), "user-id", os.getuid(), "unix-permissions", "0770", 
                "junction-path", junction_prefix, "percentage-snapshot-reserve", 0, "size", "1G", "volume-state", "online")
            self._check_netapp_error(output, "Creating new root namespace volume failed")
        else:
            debug("Multivolume mode disabled")
            junction_prefix = "" 
        # Create the clones
        for counter, vol in enumerate(self._volname):
            cname = clonename if not self._multivol else "%s%d" % (clonename, counter)
            debug("Cloning volume %s from snapshot %s as volume %s with junction path %s/%s" % (vol, snapid, cname, junction_prefix, cname))
            output = self._srv.invoke("volume-clone-create", "parent-volume", vol, "parent-snapshot", snapid, "volume", cname)
            self._check_netapp_error(output, "Creating clone failed")
            output = self._srv.invoke("volume-mount", "junction-path", "%s/%s" % (junction_prefix, cname), "volume-name", cname)
            self._check_netapp_error(output, "Mounting clone failed")
            output = self._srv.invoke("volume-set-option", "option-name", "nosnapdir", "option-value", "on", "volume", cname)
            self._check_netapp_error(output, "Setting attribute on clone failed")

    def dropclone(self, cloneid):
        info = self.filesystem_info(cloneid if not self._multivol else cloneid + "0")
        if info['origin'] != self._volname[0]:
            raise Exception(self._exceptionbase, "This clone does not belong to parent %s" % self._volname)
        for counter, vol in enumerate(self._volname):
            self._dropvolume(cloneid if not self._multivol else "%s%d" % (cloneid, counter))
        # Drop the root namespace
        if self._multivol:
            self._dropvolume(cloneid)

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
            "volume-create", "volume", self._volname[0], "containing-aggr-name", aggregate, "efficiency-policy", "default", "export-policy", export_policy, 
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
        volid.child_add_string("name", self._volname[0])
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
