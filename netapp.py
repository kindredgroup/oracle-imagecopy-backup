import os, operator
from backupcommon import SnapHandler, Configuration, scriptpath, info, error, debug
from ConfigParser import SafeConfigParser
from datetime import datetime, timedelta
from NaServer import *

class Netapp(SnapHandler):
    _exceptionbase = "netapp"
    _blocksize = 1024

    def __init__(self, configname):
        zfscredfilename = os.path.join(scriptpath(), 'netappcredentials.cfg')
        if not os.path.isfile(zfscredfilename):
          raise Exception(self._exceptionbase, "Configuration file %s not found" % zfscredfilename)
        # Authentication information
        zfscredconfig = SafeConfigParser()
        zfscredconfig.read(zfscredfilename)
        zfsauth = (zfscredconfig.get('netappcredentials','user'), zfscredconfig.get('netappcredentials','password'))
        #
        self._srv = NaServer(Configuration.get('filer', 'netapp'), 1, 1)
        self._srv.set_admin_user(zfscredconfig.get('netappcredentials','user'), zfscredconfig.get('netappcredentials','password'))
        self._volprefix = Configuration.get('volumeprefix', 'netapp')
        self._volname = "%s%s" % (self._volprefix, configname)
        super(Netapp, self).__init__(configname)
    
    def _check_netapp_error(self, output, errmsg):
        if output.results_errno() != 0:
            raise Exception(self._exceptionbase, "%s. %s" % (errmsg, output.results_reason()))

    # Extra functions for zsnapper
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
         
    
    # Public interfaces
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
        output = self._srv.invoke("snapshot-list-info", "volume", self._volname)
        self._check_netapp_error(output, "Failed to list snapshots")
        snapshotlist = output.child_get("snapshots")
        snapshots = []
        if (snapshotlist is not None and snapshotlist):
            for ss in snapshotlist.children_get():
                snapshots.append( {'id': ss.child_get_string("name"), 
                    'creation': datetime.utcfromtimestamp(float(ss.child_get_int("access-time"))), 
                    'numclones':  1 if ss.child_get_string("busy") == "true" else 0,
                    'space_total': ss.child_get_int("cumulative-total")*self._blocksize,
                    'space_unique': ss.child_get_int("total")*self._blocksize } )
        if not sortbycreation:
            return snapshots
        else:
            return sorted(snapshots, key=operator.itemgetter('creation'), reverse=sortreverse)

    def clone(self, snapid, clonename):
        output = self._srv.invoke("volume-clone-create", "parent-volume", self._volname, "parent-snapshot", snapid, "volume", clonename)
        self._check_netapp_error(output, "Creating clone failed")
        output = self._srv.invoke("volume-mount", "junction-path", "/%s" % clonename, "volume-name", clonename)

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
