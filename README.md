# unibet/oracle-imagecopy-backup

Toolset for helping DBAs manage incrementally updated image copy backups of Oracle Databases. Includes automatic restore and verification tests.

Common database backup problems this tool tries to address:

* Databases are getting ever larger, but backing them up using traditional incremental backup methods becomes a bottleneck. Traditional backupsets provide incremental backup option, but since they require the full backup to be restored first and only after that all incremental backups can be applied in order. So to keep the restore time minimal, the full backup must be taken regularly. For a database that is tens-or-more terabytes in size, taking a full backup can even take days.
* Oracle Recovery Manager RMAN has another backup type option as incrementally updated image copies, that keeps another copy of datafiles on alternate location and to updates them using incremental backups. This allows implementing **incremental forever** backup strategy, where only incremental backups are taken from the database. Recovering from image copy backups is also easy and fast, no datafile restore is needed, image copy files can be used directly as data files. But image copy backups are quite complex to manage and they also miss many important backup features that are natively built in to backupsets, like history and compression/deduplication.
* Restoring a large database from traditional backupset is very resource-intensive since the files are not directly usable. The full database needs to be restored, so for a database that is in tens-of-terabytes range this is very time consuming and it also consumes the same amount of storage. This results in a fact that companies rarely test their backups.
* To address these problems, some companies take snapshots from the database files directly. But can you really call it a backup? What happens when the main production storage fails?

Possible solution that this script set implements:

* Implement incremental forever backup strategy using incrementally updated image copies that can (and should) be stored on an alternate storage.
* Eliminating the need for separate backup process for archivelog files by setting up an additional archivelog destination on backup storage together with datafile image copies and integrating it to the backup and restore processes.
* Use of advanced storage features to implement features natively missing from image copies:
	* To provide history storage snapshot is created before refreshing image copy. Since archivelogs are stored in the same backup area, then each snapshots is completely self contained and has no dependencies to other snapshots.
	* Backup retention is implemented by just deleting snapshots that are not needed anymore. Since each snapshots is self contained and complete, then this makes it possible to implement even more complex backup retention scenarios than RMAN backupsets are capable of.
	* Backup compression and deduplication are done by storage. No database CPU is wasted on this expensive operation.
	* Storage thin clones can be used to create clones from the database.
* Automate backup testing so it can be run every day and with minimal storage requirements using thin clones.


This script set takes care of all the necessary orchestration steps between all involved systems to successfully implement the mentioned solution.

Tested with:

* Oracle DB 11.2.0.4, but any 11gR2 should work
* Oracle DB 12.1.0.2 both non-CDB and CDB
* Oracle Enterprise Linux 6u6 or later
* Oracle Enterprise Linux 7

Requirements:

* Python 2.6 or Python 2.7
* Pyhton-requests
* Direct NFS in use for Oracle DB (optional, but highly recommended for database availability)
* Storage for backups must support NFS, snapshots, cloning and optionally compression (deduplication)

Supported storage:
* Netapp ONTAP
* Oracle ZFS Storage Appliance
* Others... it is easy to extend for other systems :)

When running on Amazon Web Services (AWS) cloud platform, I recommend using [Netapp Cloud Storage Solutions](https://aws.amazon.com/testdrive/netapp/) to provide the backup storage, since currently Amazon Elastic File System (EFS) does not implement the features required for this system to work.

## Setting up backup

### Prepare OS

Oracle Linux 6

```
yum install python-requests python pytz python-tzlocal
```

Oracle Linux 7

```
yum install python-requests python2 pytz python-tzlocal
```

### Prepare Oracle database home

Compile Direct NFS to Oracle kernel: [You can find a good quide here](http://www.orafaq.com/wiki/Direct_NFS)

Database needs to be bounced before Direct NFS is activated.

Without Direct NFS, Oracle requires HARD mounted NFS mount points, or the following error will be returned: ORA-27054: NFS file system not mounted with correct options

This means that if something happens with backup storage that is becomes unresponsive, OS kernel would not hang the IO database sends to the storage. So database can hang when archivelogs are stored in that mount point.

Direct NFS is only needed to allow backups and archivelogs to be stored in SOFT mounted NFS mount points. This means that IO to unresponsive NFS will not hang, the client will get an error instead.

Essential when Oracle ZFS Storage Appliance is used.

### Prepare Oracle ZFS Storage Appliance for receiving the backups

* Enable REST API
* Create project. One project for each configuration file, then multiple autorestore configurations can run at the same time.
* Configure backup filesystem settings on project level: set compression, filesystem user privileges and what hosts can mount the filesystems.
* Create filesystem under the project **for each** database to be backed up. Filesystem name must be the same as db\_unique\_name value for that database.
* Mount all backup filesystems under common directory, for example under **/nfs/backup**
Here is an example from /etc/fstab

```
zfssa.example.com:/export/demo-backup/orcl	/nfs/backup/orcl	nfs	rw,bg,soft,nointr,tcp,vers=3,timeo=600,rsize=32768,wsize=32768,actimeo=0	0 0
```

* Prepare user in ZFSSA with the following privileges

### Setting up the scripts

I assume that you have already cloned the oracle-imagecopy-backup project git repository and I'll refer to the script directory as **$BACKUPSCRIPTS**.

Highly recommended steps:

* Fork the repository for your own use, so you could also version your database backup configurations (commit your configurations to git, excluding wallets).
* Keep configurations from separate "environments" or clusters separate and symlink the correct configuration file in each environment (example is below).
* If you use clustered environment, have only a single copy of $BACKUPSCRIPTS directory that is stored on a shared filesystem (NFS or ACFS) mounted on all nodes.

Copy sample backup file to a new configuration and link it as a default configuration file (scripts search for file **backup.cfg**). Here I'm using backup.demo.cfg as new configuration file.

```
cd $BACKUPSCRIPTS
cp backup.sample.cfg backup.demo.cfg
ln -s backup.demo.cfg backup.cfg
```

Open backup.demo.cfg and look over the parameters specified there.
You must check the following parameters:

```
[generic]
# Root directory for backups, backups will be placed under $backupdest/db_unique_name
backupdest: /nfs/backup
# Directory where tnsnames.ora and sqlnet.ora are located in relation to the backup scripts directory
tnsadmin: tns.demo
# OS user that executes the backup
osuser: oracle
# Oracle home
oraclehome: /u01/app/oracle/product/12.1.0.2/db
```

If you are using ZFS Storage Appliance to store the backups, then also change the following parameters:

```
[zfssa]
# ZFSSA REST API URL
url: https://zfssa.example.com:215
# Disk pool and project where database backup filesystems are located
pool: disk-pool1
project: db-backup1
```

Also prepare file with ZFSSA login credentials

```
cd $BACKUPSCRIPTS
cp zfscredentials.cfg.sample zfscredentials.cfg

# Edit file zfscredentials.cfg and add there ZFSSA user credentials
```

Configure each database in backup.demo.cfg, at the end of the file add a section for each database.

```
# Section name has to be the same as db_unique_name parameter in the database
[orcl]
# Database ID: select to_char(dbid) from v$database
dbid: 1433672784
# How many days of archivelogs to keep on disk
# No need to make it a large number it has no effect on snapshot retention and older archivelogs are still stored in snapshots
recoverywindow: 2
# Backup parallelism (Enterprise Edition feature only)
parallel: 4
# How many days to keep daily backups
snapexpirationdays: 31
# How many months to keep one backup per month (the last backup created for each month)
snapexpirationmonths: 6
# Is RMAN catalog also in use
registercatalog: false
# Does this database also have a Data Guard standby (Enterprise Edition feature only)
hasdataguard: false
# DBMS_SCHEDULER calendar expressions when the backup jobs should run
schedulebackup: FREQ=DAILY;BYHOUR=10;BYMINUTE=0
schedulearchlog: FREQ=HOURLY;BYHOUR=21
```

Create wallet for storing database login credentials.

```
cd $BACKUPSCRIPTS

# First create a directory for storing the wallet,
# if directory name begins with "wallet", then it will be automatically excluded by .gitignore
mkdir wallet.demo

# Initialize wallet
$ORACLE_HOME/bin/mkstore -wrl $BACKUPSCRIPTS/wallet.demo -create

# For each database add its SYS credentials to the wallet
$ORACLE_HOME/bin/mkstore -wrl $BACKUPSCRIPTS/wallet.demo -createCredential ORCL sys

# If RMAN catalog is in use, then also add RMAN catalog credentials to the same wallet
$ORACLE_HOME/bin/mkstore -wrl $BACKUPSCRIPTS/wallet.demo -createCredential RMAN rmancataloguser
```

Copy database connection information. This is just a custom **TNS_ADMIN** directory that contains **sqlnet.ora** (for wallet information) and **tnsnames.ora** (connection information for RMAN catalog and each database) files.

```
cd $BACKUPSCRIPTS

# Create a separate directory for each configuration, also version this directory contents
mkdir tns.demo

# Copy sample sqlnet.ora and tnsnames.ora files
cp tns.sample/*.ora tns.demo/

cd tns.demo

# Check sqlnet.ora contents, make sure it points to the created wallet
WALLET_LOCATION =
  (SOURCE =
   (METHOD = FILE)
    (METHOD_DATA =
     (DIRECTORY = /home/oracle/oracle-imagecopy-backup/wallet.demo)
    )
  )

SQLNET.WALLET_OVERRIDE = TRUE

# Add each database connection information to tnsnames.ora
# Local TNS name must match database DB_UNIQUE_NAME
# Use dedicated service name for taking backups, then in RAC you get automatic backup load balancing

ORCL =
  (DESCRIPTION=
    (ADDRESS=
      (PROTOCOL=TCP)
      (HOST=localhost)
      (PORT=1521)
    )
    (CONNECT_DATA=
      (SERVICE_NAME=orcl_backup)
    )
  )
```

Test if wallet and TNS configuration is correctly set up:

```
export TNS_ADMIN=$BACKUPSCRIPTS/tns.demo

# Try logging in using slqplus and rman to each database, using connection string /@db_unique_name
$ORACLE_HOME/bin/sqlplus /@orcl as sysdba
$ORACLE_HOME/bin/rman target /@orcl catalog /@rman
```

Run configuration for each database (orcl in this example). The config command will:

* Configure RMAN backup location, parallel degree, controlfile autobackup location, snapshot controlfile location, backup optimization, recovery window and archivelog deletion policy
* Place one additional OPTIONAL archivelog destination to backup filesystem
* Configure Block Change Tracking (only when Enterprise Edition is in use)

```
backup.py orcl config
```

Set **ARCHIVE\_LAG\_TARGET** parameter manually on the database. This step is optional, but if you set it, then database archives redo logs every specified amount of time. So at maximum your recovery point (the amount of data you loose in case of recovery) is limited to the time period set to **ARCHIVE\_LAG\_TARGET**.

Run backup

```
backup.py orcl imagecopywithsnap
```

Check if archivelogs exist on backup filesystem

```
backup.py orcl missingarchlog
```

### Scheduling backups

#### Option 1

Each database can schedule its own backup using DBMS\_SCHEDULER external jobs. This option is the easiest to set up.
**schedulebackup** and **schedulearchlog** are DBMS\_SCHEDULER calendar expressions when the backup jobs should run.

To monitor backup job completion, then you just need to monitor the DBMS\_SCHEDULER job executions. If backup fails, then also the corresponding DBMS\_SCHEDULER job executions fails.

To create the DBMS\_SCHEDULER jobs automatically for database **orcl** execute:

```
backup.py orcl setschedule
```

It creates a new database user BACKUPEXEC (or C##BACKUPEXEC for CDB) with minimal privileges and creates DBMS_SCHEDULER jobs to execute the backup and check that archivelogs exists on both destinations.

Even if you choose some other scheduling method it is still highly recommended to execute missing archivelog check from DBMS_SCHEDULER, since under normal circumstances it will then just execute a lightweight dictionary query and not execute any external script at all. External script will be executed only when some archivelog is missing from backup area.

#### Option 2

[Jenkins](https://jenkins.io/) is a popular continous integration and continous delivery tool that can be also used for scheduling tasks on remote servers over SSH. This solution will provide a good GUI overview of all database backups, automatic emails when backup jobs fail and also provides full backup log file through Jenkins GUI.

Execute the following SSH command from Jenkins in order to get the full backup log to Jenkins also:

```
BACKUP_LOG_TO_SCREEN=TRUE /path/to/scripts/backup.py orcl imagecopywithsnap
```

#### Option X

There are plenty of other schedulers out there, but I'd just like to make a comment, that please avoid using crontab in clustered environment, then backups will keep running in case the server hosting the crontab scheduler fails :)

## Recovering the backup

TODO

1. Snapshot the current backup area if need to restore the latest backup. DO NOT recover directly from files located in the backup area, because you will destroy the latest backup then.
2. Clone the backup that contains the time period you want to restore. NB! Snapshot time must be larger than that time you need to recover to. In pseudo SQL code: **SELECT MIN(snaptime) restore\_from\_snapshot FROM snapshots WHERE snaptime > recoverytime;**
3. Mount the clone
4. Catalog files from clone
4. Switch database to copy
5. Recover database
6. alter database open resetlogs
7. Either open the database and while the database is open copy the files to production storage; or copy the files first and then open the database

## Setting up automatic restore tests

NBNB! It is VERY important to use a separate host to run the autorestore tests that have no access to production database storage! Because it may be possible that Oracle tries to first overwrite or delete the necessary database files from their original locations! It is recommended to use a small server (small virtual machine) that only has access to backup storage over NFS.

### Autorestore method

1. Clone backup snapshot to be checked
2. Mount the created clone
3. Create a temporary instance that will switch datafiles to cloned image copy and apply ALL archivelogs present in the cloned snapshot area
4. Open database read only and run either a custom query or default SCN-to-timestamp based query to verify the latest database time and if the validated time difference is too large, fail the restore test
4. Either randomly or every X days check all datafile blocks for corruption
5. Log autorestore results and log file to separate autorestore log database
5. Drop the clone

### Setting up autorestore

Autorestore settings are in the backup configuration file, backup.demo.cfg when following the example in this document, section **autorestore**.

```
[autorestore]
# The directory to be used for restore tests. Must be empty, everything under it is destroyed.
# NB! This directory WILL BE CLEANED.
autorestoredestination: /nfs/autorestore/dest
# Clone name
autorestoreclonename: autorestore
# Place to mount the clone. This MUST be already described in /etc/fstab as mountable by user (with options fg,noauto,user).
# For example:
# zfssa.example.com:/export/demo-backup/autorestore       /nfs/autorestore/mnt    nfs     rw,fg,soft,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600,noauto,user      0 0
autorestoremountpoint: /nfs/autorestore/mnt
# Restore will be done using the latest snapshot that is at least this many hours old
autorestoresnapage: 0
# Autorestore log files
autorestorelogdir: /nfs/autorestore/log
# Maximum difference between auto restore target date and customverifydate in hours
autorestoremaxtoleranceminutes: 5
# Autorestorecatalog connect string
# First create user manually in the database:
#    create user autorestore identified by complexpassword;
#    grant create session,create table,create sequence,create procedure,create view,create job to autorestore;
#    alter user autorestore quota unlimited on users;
# Then add it to password wallet and run ./autorestore.py configfilename --createcatalog
autorestorecatalog: /@autorestore
# Chance of doing a full database validation after restore, the larger the integer, the less likely it will be. Set to 0 to disable, set to 1 to always validate. 7 means 1/7 chance.
autorestorevalidatechance: 0
# Modulus of doing a full database validation. Validation is done if: mod(current day, autorestoremodulus) == mod(hash(database unique name), autorestoremodulus).
# This quarantees that validation for each database is done after every x days consistently. 0 means disable.
# Set either autorestorevalidatechance or autorestoremodulus to 0, they will not work concurrently, modulus if preferred.
autorestoremodulus: 30
```

Description for each parameter in the the configuration file, but the basic workflow is the following:

Create a schema in some database to be used as autorestore catalog. All autorestore results, statistics and logs will be stored there.

```
create user autorestore identified by complexpassword;
grant create session,create table,create sequence,create procedure,create view,create job to autorestore;
alter user autorestore quota unlimited on users;
```

Add its password to wallet and connection string to tnsnames.ora. Wallet and tnsnames.ora are the same files used to connect to backed up databases from the same configuration file.

```
# Add password to wallet
$ORACLE_HOME/bin/mkstore -wrl $BACKUPSCRIPTS/wallet.demo -createCredential AUTORESTORE autorestore complexpassword

# Add database connection information to tns.demo/tnsnames.ora
AUTORESTORE =
  (DESCRIPTION=
    (ADDRESS=
      (PROTOCOL=TCP)
      (HOST=dbhost.example.com)
      (PORT=1521)
    )
    (CONNECT_DATA=
      (SERVICE_NAME=autorestorecatalog.sample.example.com)
    )
  )
```

Create catalog schema

```
./autorestore.py backup.demo.cfg --createcatalog
```

Create directories in local filesystem to be used by autorestore files. To be able to run multiple autorestore configuration runs in parallel, then these directories should be different for each configuration.

The directories are configured using parameters:

```
autorestoredestination: /nfs/autorestore/dest
autorestoremountpoint: /nfs/autorestore/mnt
autorestorelogdir: /nfs/autorestore/log
```

Create these directories on the file system, they must be readable and writable by oracle user.

```
mkdir -p /nfs/autorestore/dest
mkdir -p /nfs/autorestore/mnt
mkdir -p /nfs/autorestore/log
```

The script will choose a snapshot to restore and clone it under the name **autorestore** as a new file system in the storage. To avoid the need to run any root commands, already create a on-demand user mountable mount point in /etc/fstab. It must have the following mount parameters set:

* **fg** - mount will be done in foregriund and mount command returns when file system is mounted
* **noauto** - do not mount file system automatically, since the file system only exists during autorestore run
* **user** - file system is mountable by regular users, this is to avoid any need for running something as root
* **rw** - file system is readable and writable

```
# Add autorestore mountpoint to /etc/fstab
zfssa.example.com:/export/demo-backup/autorestore       /nfs/autorestore/mnt    nfs     rw,fg,soft,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600,noauto,user      0 0
```

The following parameters in for configuration file control how often all database blocks are validated using RMAN command **backup validate check logical database**. Use only one of these options.

* **autorestorevalidatechance** - Validation is random, chance of validating for the current run is 1/autorestorevalidatechance. Set to 0 to disable.
* **autorestoremodulus** - Validation is run every specified amount of days. Validation is done if: mod(current day, autorestoremodulus) == mod(hash(database unique name), autorestoremodulus). Set to 0 to disable.

After every successful restore, database will be opened read only to compare the database time to the time snapshot was created. The allowed time difference is configured using parameter **autorestoremaxtoleranceminutes**. If the time difference exceeds the tolerance (specified in minutes), the restore will be marked as a failure.

The default query to get the database restore time is: **select max(time\_dp) from sys.smon\_scn\_time**, but this timestamp is updated by database every few hours only, so it is not a very good and precise time. So whenever possible, set a custom query to seturn the database time, for example maximum date from a busy transaction table. The specified custom query must return a single DATE value.

Custom time query is set in the database configuration section for each database:

```
[orcl]
dbid: 1433672784
recoverywindow: 2
parallel: 4
snapexpirationdays: 31
snapexpirationmonths: 6
registercatalog: true
hasdataguard: false
schedulebackup: FREQ=DAILY;BYHOUR=10;BYMINUTE=0
schedulearchlog: FREQ=HOURLY;BYHOUR=21
customverifydate: select max(d) from (select cast(max(createtime) as date) d from exampleapp.transactions union all select current_dt from dbauser.autorestore_ping_timestamp)
```

### Executing autorestore

Just running autorestore.py will not work, you have to acknowledge that the system running autorestore tests is not connected to production database storage.

```
$ ./autorestore.py backup.demo.cfg
THIS AUTORESTORE PROCESS CAN BE VERY DANGEROUS IF THIS HOST HAS ACCESS TO PRODUCTION DATABASE FILESYSTEM/STORAGE.
THE RESTORE PROCESS CAN OVERWRITE OR DELETE FILES ON THEIR ORIGINAL CONTROL FILE LOCATIONS!
RUN IT ONLY ON A HOST THAT IS COMPLETELY SANDBOXED FROM PRODUCTION DATABASE ENVIRONMENT.
TO CONTINUE, SET ENVIRONMENT VARIABLE AUTORESTORE_SAFE_SANDBOX TO VALUE TRUE (CASE SENSITIVE).
```

Read through the warning and do what is asked to continue. autorestore.py takes the configuration file name as argument and restores all databases registered in the configuration file.

If different configuration files use different ZFSSA projects and different mount points on OS side (as recommended), then autorestore from different configuration files can run in parallel.

## Extending to other storage systems

* Create new python module to implement class **SnapHandler** from backupcommon.py.
* Change parameters **snappermodule** and **snapperclass** in backup.cfg to point to the new class.
* ZFSSA is implemented in module zfssa.py

## Helper tools

### restore.py

Restores database to a user specified time and opens it up as a separate instance on a separate server.

```
[oracle@autorestore oracle-imagecopy-backup]$ ./restore.py backup.cfg orcl
This utility will start a copy of a database restored to a specified point in time.
THIS UTILITY SHOULD NOT BE RUN ON A SYSTEM WITH ACCESS TO PRODUCTION STORAGE!
THIS UTILITY IS NOT INTENDED FOR EMERGENCY PRODUCTION RESTORE.

Is this system isolated with no access to production database storage? (y/n) y
Directory where to mount clone: /tmp/mnt
Restore database to time point: (yyyy-mm-dd hh24:mi:ss) 2016-12-17 20:40:35
Was the timestamp in UTC (answer N for local time)? (y/n) n
Target instance name: orclrest
######################################

Database unique name: orcl
Oracle home: /u01/app/oracle/product/12.1.0.2/db
Clone mount path: /tmp/mnt
Target instance SID: orclrest
Restore target time UTC: 2016-12-17 19:40:35+00:00
Restore target time local: 2016-12-17 20:40:35+01:00
Restored from snapshot: orcl-20161217T204443

Are these parameters correct? (y/n) y

######################################
Please execute the following command as root to mount the backup volume:

mount -t nfs -o rw,bg,hard,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600 \
  zfs_server_address:/export/demo-backup/restore_orcl_20161217_221147 /tmp/mnt

Did you execute it? (y/n) y
Session log file: /tmp/restore_20161217T221218_orcl.log
22:12:18 INFO     root            Starting database restore
22:12:58 INFO     root            Database restore complete
22:12:58 INFO     root            SID: orclrest
22:12:58 INFO     root            Requested target time: 2016-12-17 20:40:35+01:00
22:12:58 INFO     root            Verified restored database time: 2016-12-17 20:40:32
22:12:58 INFO     root            Difference from target: 0:00:03

Commands to clean up:
1. Shut down database instance orclrest
2. Execute as root: umount /tmp/mnt
3. Drop clone:
   BACKUPCONFIG=backup.cfg /home/oracle/oracle-imagecopy-backup/zsnapper.py orcl \
     dropclone restore_orcl_20161217_221147
```

### zsnapper.py
Tool to run storage snapshot/clone operations from command line directly.

Create a new snapshot of database orcl backup area

```
[oracle@backup oracle-imagecopy-backup]$ ./zsnapper.py orcl create
Snapshot created: orcl-20161017T093914
```

Clone a snapshot

```
[oracle@backup oracle-imagecopy-backup]$ ./zsnapper.py orcl clone orcl-20161017T093914
Clone created.
Clone name: orcl-20161017T093914-clone-20161017T094050
Mount point: /export/demo-backup/orcl-20161017T093914-clone-20161017T094050
Mount command (execute as root and replace zfs ip address and mount directory):
mount -t nfs -o rw,bg,soft,nointr,rsize=32768,wsize=32768,tcp,vers=3,timeo=600 <zfs_ip_address>:/export/demo-backup/orcl-20161017T093914-clone-20161017T094050 <mount_directory_here>
```

List all snapshots from database orcl

```
$ ./zsnapper.py orcl list
orcl-20160524T153531 [2016-03-13 17:33:23 UTC] total=165MB unique=165MB clones=0
orcl-20160524T155055 [2016-05-24 13:50:55 UTC] total=164MB unique=1MB clones=0
orcl-20160524T155205 [2016-05-24 13:52:05 UTC] total=165MB unique=276kB clones=0
orcl-20161017T093914 [2016-10-17 07:39:14 UTC] total=177MB unique=0B clones=1
```

List all clones

```
[oracle@backup oracle-imagecopy-backup]$ ./zsnapper.py orcl listclones
orcl-20161017T093914-clone-20161017T094050 [orcl-20161017T093914] [mount point: /export/demo-backup/orcl-20161017T093914-clone-20161017T094050]
```

Drop clone

```
[oracle@backup oracle-imagecopy-backup]$ ./zsnapper.py orcl dropclone orcl-20161017T093914-clone-20161017T094050
Clone dropped.
```

Check the latest snapshot age, for example for use with Nagios (exits with code 1 for warning state and 2 for critical state)

```
[oracle@backup oracle-imagecopy-backup]$ ./zsnapper.py orcl checkage
OK: The latest snapshot age 0:03:57.763734
```

### exec_all.py

Execute backup.py action on all databases in a configuration file (in serial). Exclusion list can be added.

```
$ ./exec_all.py
Usage: exec_all.py <action for backup.py> [comma separated exclude list]

$ ./exec_all.py imagecopywithsnap
```

### report.py

Detailed backup report for all configured databases.

### autorestore_check.sql

Sample PL/SQL procedures and queries to automatically monitor autorestore status (from Nagios for example). Use these as pseudo code and adapt them to your monitoring system use.

## Configure support for Netapp storage (ONTAP)

Support for Netapp storage requires Netapp Manageability SDK 5.6 Python bindings.

1. [Download NetApp Manageability SDK 5.6 from MySupport](http://mysupport.netapp.com/NOW/cgi-bin/software/?product=NetApp+Manageability+SDK&platform=All+Platforms)
2. Unzip it on your computer
3. Copy all files under **netapp-manageability-sdk-5.6/lib/python/NetApp**, namely _DfmErrno.py, NaElement.py, NaErrno.py, NaServer.py_ to the root directory of backup scripts (the directory where netapp.py and backup.py are located)
