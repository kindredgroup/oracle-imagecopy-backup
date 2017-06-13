import requests, json, operator, urllib, os
from backupcommon import SnapHandler, Configuration, scriptpath, size2str, info, error, debug
from ConfigParser import SafeConfigParser
from datetime import datetime, timedelta
from urlparse import urlparse

class ZFSHttp(object):
    _jsonheader = {'Content-Type': 'application/json'}
    _timeout = 300 # HTTP call timeout in seconds
    server_address = None

    def __init__(self, baseurl, auth):
        self._baseurl = baseurl
        self._auth = auth
        up = urlparse(self._baseurl)
        if up.netloc.find(":") > -1:
            self.server_address = up.netloc.split(":", 1)[0]
        else:
            self.server_address = up.netloc
        try:
            requestwarning = __import__('requests.packages.urllib3.exceptions', globals(), locals(), ['InsecureRequestWarning'])
            requestwarningclass = getattr(requestwarning, 'InsecureRequestWarning')
            requests.packages.urllib3.disable_warnings(requestwarningclass)
        except AttributeError:
            pass

    def _array2url(self, urlarray):
        # Converts list of url components as a quoted URL
        return '/'.join(map(urllib.quote_plus, urlarray))

    def get(self, urlarray, return_json=True):
        url = self._array2url(urlarray)
        debug("Sending GET to %s" % url)
        r = requests.get("%s/%s" % (self._baseurl, url), auth=self._auth, headers=self._jsonheader, verify=False, timeout=self._timeout)
        debug("Return code: %d" % r.status_code)
        if r.status_code != 200:
            error("GET to %s returned %d" % (url, r.status_code))
            raise Exception('zfssareturncode',"GET request return code is not 200 (%s)" % r.status_code)
        if return_json:
            j = json.loads(r.text)
            return j
        else:
            return None

    def post(self, urlarray, payload):
        url = self._array2url(urlarray)
        debug("Sending POST to %s" % url)
        r = requests.post("%s/%s" % (self._baseurl, url), auth=self._auth, headers=self._jsonheader, verify=False, data=json.dumps(payload), timeout=self._timeout)
        debug("Return code: %d" % r.status_code)
        if r.status_code == 201:
            j = json.loads(r.text)
        else:
            error("POST to %s returned %d" % (url, r.status_code))
            j = {}
        return r.status_code, j

    def put(self, urlarray, payload):
        url = self._array2url(urlarray)
        debug("Sending PUT to %s" % url)
        r = requests.put("%s/%s" % (self._baseurl, url), auth=self._auth, headers=self._jsonheader, verify=False, data=json.dumps(payload), timeout=self._timeout)
        debug("Return code: %d" % r.status_code)
        if r.status_code == 201:
            j = json.loads(r.text)
        else:
            error("PUT to %s returned %d" % (url, r.status_code))
            j = {}
        return r.status_code, j

    def delete(self, urlarray):
        url = self._array2url(urlarray)
        debug("Sending DELETE to %s" % url)
        r = requests.delete("%s/%s" % (self._baseurl, url), auth=self._auth, headers=self._jsonheader, verify=False, timeout=self._timeout)
        debug("Return code: %d" % r.status_code)
        return r.status_code

class ZFSSA(SnapHandler):
    _exceptionbase = "zfssnap"

    def __init__(self, configname):
        zfscredfilename = os.path.join(scriptpath(), 'zfscredentials.cfg')
        if not os.path.isfile(zfscredfilename):
          raise Exception(self._exceptionbase, "Configuration file %s not found" % zfscredfilename)
        # Authentication information
        zfscredconfig = SafeConfigParser()
        zfscredconfig.read(zfscredfilename)
        zfsauth = (zfscredconfig.get('zfscredentials','zfsuser'), zfscredconfig.get('zfscredentials','zfspassword'))
        #
        zfssaurl = "%s/api/storage/v1" % Configuration.get('url', 'zfssa')
        self._pool = Configuration.get('pool', 'zfssa')
        self._project = Configuration.get('project', 'zfssa')
        self._filesystem = configname
        #
        self._http = ZFSHttp(zfssaurl, zfsauth)
        super(ZFSSA, self).__init__(configname)

    def str2date(self, zfsdate):
        # ZFS returned string to datetime object
        # 20150803T13:31:42
        # Result is in UTC!
        d = datetime.strptime(zfsdate, '%Y%m%dT%H:%M:%S')
        return d

    # Public interfaces

    def filesystem_info(self, filesystemname=None):
        urlarray = ['pools', self._pool, 'projects', self._project, 'filesystems']
        if filesystemname is not None:
            urlarray.append(filesystemname)
        j = self._http.get(urlarray)
        if filesystemname is None:
            return j["filesystems"]
        else:
            return j["filesystem"]

    def listclones(self):
        output = []
        for s in self.filesystem_info():
            if "origin" in s:
                origin = s["origin"]
                if origin["project"] == self._project and origin["share"] == self._filesystem:
                    yield { 'clonename': s["name"], 'origin': origin["snapshot"], 'mountpoint': s["mountpoint"] }

    def mountstring(self, filesystemname):
        info = self.filesystem_info(filesystemname)
        return "%s:%s" % (self._http.server_address if self._http.server_address is not None else 'zfs_server_address', info['mountpoint'])


    def snap(self):
        snapname = "%s-%s" % (self._filesystem, datetime.now().strftime('%Y%m%dT%H%M%S'))
        payload = { 'name': snapname }
        r,j = self._http.post(['pools', self._pool, 'projects', self._project, 'filesystems', self._filesystem, 'snapshots'], payload)
        if r != 201:
            raise Exception(self._exceptionbase,"Creating snapshot failed with return code %d" % r)
        return snapname

    def dropsnap(self, snapid):
        ret_code = self._http.delete(['pools', self._pool, 'projects', self._project, 'filesystems', self._filesystem, 'snapshots', snapid])
        if ret_code != 204:
            raise Exception(self._exceptionbase, "Failed to drop snapshot %s" % snapid)

    def getsnapinfo(self, snapstruct):
        s = snapstruct
        return {'id': s["name"], 'creation': self.str2date(s["creation"]), 'numclones': int(s["numclones"]),
            'space_total': int(s["space_data"]), 'space_unique': int(s["space_unique"])}

    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        j = self._http.get(['pools', self._pool, 'projects', self._project, 'filesystems', self._filesystem, 'snapshots'])
        if not sortbycreation:
            return j["snapshots"]
        else:
            return sorted(j["snapshots"], key=operator.itemgetter('creation'), reverse=sortreverse)

    def clone(self, snapid, clonename):
        payload = { 'project': self._project, 'share': clonename }
        r,j = self._http.put(['pools', self._pool, 'projects', self._project, 'filesystems', self._filesystem, 'snapshots', snapid, 'clone'], payload)
        if r != 201:
          raise Exception(self._exceptionbase, "Creating clone failed with code %d" % r)
        # Remove compression from the clone
        # Do nothing if it errors
        # r,j = self._http.put(['pools', self._pool, 'projects', self._project, 'filesystems', clonename], { 'compression': 'off' } )

    def dropclone(self, cloneid):
        j = self.filesystem_info(cloneid)
        if "origin" not in j:
            raise Exception(self._exceptionbase, 'Specified filesystem is not a clone.')
        origin = j["origin"]
        if origin["project"] != self._project or origin["share"] != self._filesystem:
            raise Excption(self._exceptionbase, "Specified filesystem is not cloned from share %s" % self._filesystem)
        r = self._http.delete(['pools', self._pool, 'projects', self._project, 'filesystems', cloneid])
        if r != 204:
            error("Dropping clone failed. Return code: %d" % r)
            raise Exception(self._exceptionbase, "Dropping clone failed. Return code: %d" % r)

    def createvolume(self):
        payload = { 'name': self._filesystem }
        ret_code = self._http.post(['pools', self._pool, 'projects', self._project, 'filesystems'], payload)
        if ret_code != 201:
            raise Exception(self._exceptionbase, "Failed to create file system")
