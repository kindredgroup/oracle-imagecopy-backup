#!/usr/bin/python2.6

import os, sys
from subprocess import Popen, PIPE
from backupcommon import Configuration, scriptpath

def printhelp():
  print "Usage: dbinfo.py <config>"
  sys.exit(2)

if len(sys.argv) != 2:
  printhelp()

# Read configuration
configsection = sys.argv[1]
Configuration.init(configsection)
gimanaged = Configuration.get('gimanaged').upper() == 'TRUE'

if not gimanaged:
  print "gimanaged option is not set to TRUE for this database"
  sys.exit(2)

# Set oracle home, if it is not configured separately, take it from environment
oraclehome = Configuration.get('oraclehome','generic')
if os.environ.get('ORACLE_SID'):
  del os.environ['ORACLE_SID']
os.environ['ORACLE_HOME'] = oraclehome
os.environ['NLS_DATE_FORMAT'] = 'yyyy-mm-dd hh24:mi:ss'

# Get software version
p = Popen([os.path.join(scriptpath(), "get_oracle_version.sh"), oraclehome], stdout=PIPE, stderr=None, stdin=None)
oracleversion,dataerr = p.communicate()
if oracleversion < "11.1":
  print "Detected Oracle software version %s is too old" % oracleversion
  sys.exit(1)

# srvctl config database
print "== Database configuration =="
p = Popen([os.path.join(oraclehome, 'bin', 'srvctl'), 'config', 'database', '-d', configsection], stdout=PIPE, stderr=None, stdin=None)
datain,dataerr = p.communicate()
print datain

def printserviceinfo(si):
  if len(si) > 0 and si['enabled']:
    if oracleversion < "12.1":
      s = "srvctl add service -d %s -s %s -j %s -B %s -r %s" % (configsection, si['name'], si['clb'], si['rlb'], si['preferred'])
      if si['available']:
        s+= " -a %s" % si['available']
      print s
      if si['edition']:
        print "srvctl add service -d %s -s %s -t %s" % (configsection, si['name'], si['edition'])
    else:
      s = "srvctl add service -database %s -service %s -preferred %s" % (configsection, si['name'], si['preferred'])
      if si['available']:
        s+= " -available %s" % si['available']
      if si['edition']:
        s+= " -edition %s" % si['edition']
      if si['pluggable']:
        s+= " -pdb %s" % si['pluggable']
      print s

# srvctl config service
print "== Service configuration =="
p = Popen([os.path.join(oraclehome, 'bin', 'srvctl'), 'config', 'service', '-d', configsection], stdout=PIPE, stderr=None, stdin=None)
datain,dataerr = p.communicate()
print datain
print "== Service configuration parsed =="
services = {}
currentservice = {}
for line in datain.splitlines():
  s = line.split(': ')
  if s[0] == 'Service name':
    printserviceinfo(currentservice)
    currentservice = { 'name': s[1], 'enabled': False }
  elif s[0] == 'Edition':
    currentservice['edition'] = s[1]
  elif s[0] == 'Preferred instances':
    currentservice['preferred'] = s[1]
  elif s[0] == 'Available instances':
    currentservice['available'] = s[1]
  elif s[0] == 'Service is enabled':
    currentservice['enabled'] = True
  elif s[0] == 'Connection Load Balancing Goal':
    currentservice['clb'] = s[1]
  elif s[0] == 'Runtime Load Balancing Goal':
    currentservice['rlb'] = s[1]
  elif s[0] == 'Pluggable database name':
    currentservice['pluggable'] = s[1]
printserviceinfo(currentservice)
