import requests, json, operator, urllib, os, re
from backupcommon import SnapHandler, Configuration, scriptpath, size2str, info, error, debug
from ConfigParser import SafeConfigParser
from datetime import datetime, timedelta

class SoftNASHttp(object):
    _timeout = 300 # HTTP call timeout in seconds
    _cookies = {}

    def __init__(self, baseurl):
        self._baseurl = baseurl
        try:
            requestwarning = __import__('requests.packages.urllib3.exceptions', globals(), locals(), ['InsecureRequestWarning'])
            requestwarningclass = getattr(requestwarning, 'InsecureRequestWarning')
            requests.packages.urllib3.disable_warnings(requestwarningclass)
        except AttributeError:
            pass

    def post(self, url, payload):
        debug("Sending POST to %s" % url)
        r = requests.post("%s/%s" % (self._baseurl, url), cookies=self._cookies, verify=False, data=payload, timeout=self._timeout, allow_redirects=False)
        self._cookies = r.cookies
        debug("Return code: %d" % r.status_code)
        try:
            j = json.loads(r.text)
        except ValueError:
            j = {}
        return j, r.status_code

    def get(self, url):
        debug("Sending GET to %s" % url)
        r = requests.get("%s/%s" % (self._baseurl, url), cookies=self._cookies, verify=False, timeout=self._timeout, allow_redirects=False)
        debug("Return code: %d" % r.status_code)
        return r.status_code

class SoftNAS(SnapHandler):
    _exceptionbase = "softnas"

    def __init__(self, configname):
        credfilename = os.path.join(scriptpath(), 'softnascredentials.cfg')
        if not os.path.isfile(credfilename):
          raise Exception(self._exceptionbase, "Configuration file %s not found" % credfilename)
        # Authentication information
        credconfig = SafeConfigParser()
        credconfig.read(credfilename)
        self._username = credconfig.get('credentials','user')
        self._password = credconfig.get('credentials','password')
        #
        url = "https://%s/softnas" % Configuration.get('serveraddress', 'softnas')
        self._pool = Configuration.get('pool', 'softnas')
        self._filesystem = configname
        #
        self._http = SoftNASHttp(url)
        super(SoftNAS, self).__init__(configname)

    def _login(self):
        j,r = self._http.post('login.php', { 'username': self._username, 'password': self._password })

    def _logout(self):
        r = self._http.get('logout.php')

    def _request(self, opcode, parameters={}):
        payload = {'opcode': opcode}
        payload.update(parameters)
        self._login()
        try:
            j,r = self._http.post('snserver/snserv.php', payload)
            if not j['success']:
                raise Exception('softnas',"Request failed. Return code: %d, message: %s" % (r, j))
        finally:
            self._logout()
        return j

    ###

    def snap(self):
        j = self._request('snapcommand', {'command': 'create', 'pool_name': self._pool, 'volume_name': self._filesystem} )
        i = j['msg'].find('@')
        i2 = j['msg'].find("'", i)
        return j['msg'][i+1:i2]

    def dropsnap(self, snapid):
        j = self._request('snapcommand', {'command': 'delete', 'snapshots': "[%s]" % json.dumps({'snapshot_name': snapid, 'pool_name': self._pool, 'volume_name': self._filesystem}) } )

    def listsnapshots(self, sortbycreation=False, sortreverse=False):
        j = self._request('snapshotlist', {'pool_name': "%s/%s" % (self._pool, self._filesystem)})
        snapshots = []
        for s in j['records']:
            snapshots.append( {'id': s['snapshot_name'],
                'creation': datetime.utcfromtimestamp(float(s['creation'])),
                'numclones':  0,
                'space_total': s['refer'],
                'space_unique': s['refer']
            } )
        if not sortbycreation:
            return snapshots
        else:
            return sorted(snapshots, key=operator.itemgetter('creation'), reverse=sortreverse)

    def filesystem_info(self, filesystemname=None):
        pass

    def listclones(self):
        pass

    def mountstring(self, filesystemname):
        pass

    def getsnapinfo(self, snapstruct):
        return snapstruct

    def clone(self, snapid, clonename):
        pass

    def dropclone(self, cloneid):
        pass

    def createvolume(self):
        pass
