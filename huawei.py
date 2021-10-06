import requests, json, operator, urllib, os, pprint
from backupcommon import SnapHandler, Configuration, scriptpath, size2str, info, error, debug
from ConfigParser import SafeConfigParser
from datetime import datetime, timedelta
from urlparse import urlparse

class HuaweiDoradoHttp(object):
    _header = {'Content-Type': 'application/json'}
    _timeout = 300 # HTTP call timeout in seconds
    _verify = False
    _lastlogin = None
    _logout_time = timedelta(minutes=15)
    server_address = None

    def __init__(self, baseurl, username, password):
        self._configuredurl = baseurl
        self._username = username
        self._password = password
        self._session = None
        if not self._verify:
            try:
                requestwarning = __import__('requests.packages.urllib3.exceptions', globals(), locals(), ['InsecureRequestWarning'])
                requestwarningclass = getattr(requestwarning, 'InsecureRequestWarning')
                requests.packages.urllib3.disable_warnings(requestwarningclass)
            except AttributeError:
                pass
        self._login()

    def _checkerror(self, r, url, method):
        debug("Return code: %d" % r.status_code)
        if r.status_code != 200:
            raise Exception("HuaweiDoradoHttp", "API call to storage failed. Response code: %d" % r.status_code)
        j = r.json()
        if j['error'].get('code', 0) > 0:
            raise Exception("HuaweiDoradoHttp", "API call to storage failed. Error element: %s" % str(j['error']))

    def _login(self):
        # Authenticating to storage, requests session is used for storing session cookies
        if self._session is None:
            self._session = requests.Session()
            url = "%s/xxxxx/sessions" % self._configuredurl
            r = self._session.post(url,
                headers={'Content-Type': 'application/json'}, verify=self._verify, timeout=self._timeout,
                json={'username': self._username, 'password': self._password, 'scope': 0})
            self._checkerror(r, url, "post")
            self._header.update({
                'iBaseToken': r.json()['data']['iBaseToken']
            })
            self._deviceid = r.json()['data']['deviceid']
            self._baseurl = "%s/%s" % (self._configuredurl, urllib.quote_plus(self._deviceid))
            self._lastlogin = datetime.utcnow()
    
    def _checklogin(self):
        # Checking if previous login has timed out
        if self._session is None or datetime.utcnow() - self._lastlogin > self._logout_time:
            self._session = None
            self._login()

    def post(self, url, payload):
        self._checklogin()
        debug("Sending POST to %s" % url)
        r = self._session.post("%s/%s" % (self._baseurl, url), headers=self._header, verify=self._verify, json=payload, timeout=self._timeout)
        self._checkerror(r, url, "post")
        return r.status_code, r.json()

    def get(self, url, return_json=True, payload={}):
        self._checklogin()
        debug("Sending GET to %s" % url)
        r = self._session.get("%s/%s" % (self._baseurl, url), headers=self._header, verify=self._verify, timeout=self._timeout, params=payload)
        self._checkerror(r, url, "get")
        return r.json() if return_json else None

    def delete(self, url, payload={}):
        self._checklogin()
        debug("Sending DELETE to %s" % url)
        r = self._session.delete("%s/%s" % (self._baseurl, url), headers=self._header, verify=self._verify, timeout=self._timeout, params=payload)
        self._checkerror(r, url, "delete")
        return r.status_code

class HuaweiDorado(SnapHandler):
    _exceptionbase = "huaweidorado"
    _vid = None
    _fid = None

    def __init__(self, configname):
        # Get credentials
        if 'DBBACKUPSUITE_HUAWEI_USER' in os.environ and 'DBBACKUPSUITE_HUAWEI_PASSWORD' in os.environ:
            # From environment variables
            zfsauth = (os.environ['DBBACKUPSUITE_HUAWEI_USER'], os.environ['DBBACKUPSUITE_HUAWEI_PASSWORD'])
        elif os.path.isfile(os.path.join(os.path.expanduser("~"), '.dbbackupsuite_storage.cfg')):
            # From $HOME/.dbbackupsuite_storage.cfg
            zfscredconfig = SafeConfigParser()
            zfscredconfig.read(os.path.join(os.path.expanduser("~"), '.dbbackupsuite_storage.cfg'))
            zfsauth = (zfscredconfig.get('huaweicredentials','user'), zfscredconfig.get('huaweicredentials','password'))
        else:
            credfilename = os.path.join(scriptpath(), 'huaweidoradocredentials.cfg')
            if not os.path.isfile(credfilename):
                raise Exception(self._exceptionbase, "Configuration file %s not found" % credfilename)
            # Authentication information
            zfscredconfig = SafeConfigParser()
            zfscredconfig.read(credfilename)
            zfsauth = (zfscredconfig.get('credentials','user'), zfscredconfig.get('credentials','password'))
        #
        baseurl = "%s/deviceManager/rest" % Configuration.get('url', 'huaweidorado')
        self._filesystem = configname
        self._vstore = Configuration.get('vstore', 'huaweidorado')
        #
        self._http = HuaweiDoradoHttp(baseurl, zfsauth[0], zfsauth[1])
        super(HuaweiDorado, self).__init__(configname)

    # Returns the vstore id that matches the configured vstore name
    def _vstoreid(self):
        if self._vid is None:
            r = self._http.get("vstore")
            for v in r['data']:
                if v['NAME'].upper() == self._vstore.upper():
                    self._vid = v['ID']
        return self._vid

    # Returns the filesystem id that matches the configured filesystem name
    def _fsid(self, fsname = None):
        if self._fid is None or fsname is not None:
            n = fsname if fsname is not None else self._filesystem
            r = self._http.get(url="filesystem", payload={'vstoreid': self._vstoreid(), 'filter': "NAME:%s" % n})
            for v in r['data']:
                if v['NAME'].upper() == n.upper():
                    if fsname is None:
                        self._fid = v['ID']
                    else:
                        return v['ID']
        return self._fid

    def snap(self):
        snapname = "%s-%s" % (self._filesystem, datetime.now().strftime('%Y%m%dT%H%M%S'))
        r,j = self._http.post("fssnapshot", payload={'NAME': snapname, 'PARENTID': self._fsid(), 'PARENTTYPE': '40'})
        return snapname

    def dropsnap(self, snapid):
        for snap in self.listsnapshots():
            if snap['id'] == snapid:
                r = self._http.delete("fssnapshot/%s" % snap['internal_id'])
                break

    def _getclones(self, snapname=None):
        clones = []
        # DIDN't WORK: 'filter': "ISCLONEFS:true"
        r = self._http.get("filesystem", payload={'vstoreId': self._vstoreid()})
        for rx in r['data']:
            if rx['ISCLONEFS'] == 'true' and rx['PARENTFILESYSTEMNAME'].upper() == self._filesystem.upper() and (snapname is None or rx['PARENTSNAPSHOTNAME'].upper() == snapname.upper()):
                clones.append({'internal_id': rx['ID'], 'clonename': rx['NAME'], 'origin': rx['PARENTSNAPSHOTNAME'], 'mountpoint': self._get_share(rx['NAME'])['path']})
        return clones

    def getsnapinfo(self, snapstruct):
        #r = self._http.get("fssnapshot/%s" % snapstruct['internal_id'], payload={'vstoreid': self._vstoreid()})
        #s = r['data']
        #print(s)
        clones = self._getclones(snapstruct['id'])
        return {
            'id': snapstruct['id'],
            'intrnal_id': snapstruct['internal_id'],
            'creation': snapstruct['creation'],
            'numclones': len(clones),
            'space_total': snapstruct['space_total'], # Not supported yet by huawei
            'space_unique': snapstruct['space_unique'] # Not supported yet by huawei
        }

    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        r = self._http.get("fssnapshot", payload={'PARENTID': self._fsid()})
        #pp.pprint(r.json())
        snaps = []
        for snap in r['data']:
            snaps.append({
                'id': snap['NAME'],
                'internal_id': snap['ID'],
                'creation': datetime.utcfromtimestamp(int(snap['utcTimeStamp'])),
                'numclones': 0, # Need to make an extra call
                'space_total': -1,'space_unique': -1 # Not supported yet by Huawei
            })
        if not sortbycreation:
            return snaps
        else:
            return sorted(snaps, key=operator.itemgetter('creation'), reverse=sortreverse)

    def _get_share(self, filesystemname=None):
        fid = self._fsid(filesystemname)
        if fid is None:
            raise Exception(self._exceptionbase, "Can't find filesystem")
        r = self._http.get("NFSHARE", payload={'vstoreId': self._vstoreid(), 'filter': "FSID:%s" % fid})
        for rx in r['data']:
            return {'id': rx['ID'], 'path': rx['SHAREPATH']}


    def filesystem_info(self, filesystemname=None):
        r = self._http.get("filesystem", payload={'vstoreId': self._vstoreid(), 'filter': "NAME:%s" % filesystemname if filesystemname is not None else self._filesystem})
        rx = r['data']
        return {'internal_id': rx['ID'], 'clonename': rx['NAME'], 'origin': rx['PARENTSNAPSHOTNAME'], 'mountpoint': self._get_share(rx['NAME'])}

    def listclones(self):
        return self._getclones()

    def mountstring(self, filesystemname):
        return "%s:%s" % (Configuration.get('mounthost', 'huaweidorado'), self._get_share(filesystemname)['path'])

    def clone(self, snapid, clonename):
        snap_internal_id = None
        for snap in self.listsnapshots():
            if snap['id'] == snapid:
                snap_internal_id = snap['internal_id']
                break
        if snap_internal_id is None:
            raise Exception(self._exceptionbase, "Snapshot for cloning not found")
        # Create clone
        r,j = self._http.post("filesystem",
            payload={'NAME': clonename, 'ISCLONEFS': 'true', 'PARENTFILESYSTEMID': self._fsid(), 'PARENTSNAPSHOTID': snap_internal_id, 'vstoreId': self._vstoreid()})
        clone_fs_id = j['data']['ID']
        # Create NFS share
        r,j = self._http.post("NFSHARE",
            payload={'SHAREPATH': "/%s/" % clonename, 'FSID': clone_fs_id, 'vstoreId': self._vstoreid()})
        clone_share_id = j['data']['ID']
        # Add NFS clients
        r,j = self._http.post("NFS_SHARE_AUTH_CLIENT",
            payload={'NAME': Configuration.get('restorenfsclient', 'huaweidorado'), 'PARENTID': clone_share_id, 'vstoreId': self._vstoreid(), 'ACCESSVAL': 1, 'SYNC': 0, 'ALLSQUASH': 1, 'ROOTSQUASH': 1})

    def dropclone(self, cloneid):
        # Search for filesystem
        clone_fs_id = None
        r = self._http.get("filesystem", payload={'vstoreId': self._vstoreid()})
        for rx in r['data']:
            if rx['NAME'] == cloneid:
                clone_fs_id = rx['ID']
                break
        if clone_fs_id is None:
            raise Exception(self._exceptionbase, "Clone filesystem not found")
        # Search for share
        r = self._http.get("NFSHARE", payload={'vstoreId': self._vstoreid(), 'filter': "FSID:%s" % clone_fs_id})
        for rx in r['data']:
            ret = self._http.delete("NFSHARE/%s" % rx['ID'], payload={'vstoreId': self._vstoreid()})
        # Delete filesystem
        r = self._http.delete("filesystem/%s" % clone_fs_id, payload={'vstoreId': self._vstoreid()})

    def createvolume(self):
        # Create clone
        r,j = self._http.post("filesystem",
            payload={
                'NAME': self._filesystem
                , 'vstoreId': self._vstoreid()
                , 'CAPACITY': 1000000000
                , 'SECTORSIZE': 8192
                , 'ENABLECOMPRESSION': 'true'
                , 'unixPermissions': '770'
                , 'ISSHOWSNAPDIR': 'false'
                , 'PARENTID': '0'
                , 'AUTODELSNAPSHOTENABLE': 'false'
                , 'ENABLEDEDUP': 'true'
                , 'COMPRESSION': '1'
            })
        clone_fs_id = j['data']['ID']
        # Create NFS share
        r,j = self._http.post("NFSHARE",
            payload={'SHAREPATH': "/%s/" % self._filesystem, 'FSID': clone_fs_id, 'vstoreId': self._vstoreid()})
        clone_share_id = j['data']['ID']
        # Add NFS clients
        r,j = self._http.post("NFS_SHARE_AUTH_CLIENT",
            payload={'NAME': Configuration.get('restorenfsclient', 'huaweidorado'), 'PARENTID': clone_share_id, 'vstoreId': self._vstoreid(), 'ACCESSVAL': 1, 'SYNC': 0, 'ALLSQUASH': 1, 'ROOTSQUASH': 1})
