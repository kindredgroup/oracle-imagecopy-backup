# oracle-imagecopy-backup

Toolset for helping DBAs manage incrementally updated image copy backups of Oracle Databases. Includes automatic restore and verification tests.

Common database backup problems this tool tries to address:

* Databases are getting ever larger, but backing them up using traditional incremental backup methods becomes a bottleneck. Traditional backupsets provide incremental backup option, since they require the full backup to be restored first and only after that all incremental backups can be applied in order. So to keep the restore time minimal, the full backup must be taken regularly. For a database that is tens-or-more terabytes in size, taking a full backup can even take days.
* Oracle Recovery Manager RMAN has another backup type option as incrementally updated image copies, that keeps another copy of datafiles on alternate location and to update them using incremental backups. This allows implementing "incremental forever" backup strategy, where only incremental backups are taken from the database. Recovering from image copy backups is also easy and fast, the files can be directly used instead of the main data files. But image copy backups are quite complex to manage and they also miss many important backup features that are natively built in to backupsets, like history.
* Restoring a large database from traditional backupset is very resource-intensive since the files are not directly usable. The full database needs to be restored, so for a database that is in tens-of-terabytes range this is very time consuming and it also consumes the same amount of storage. This results in a fact that companies rarely test their backups.

Features:

* Orchestration of all steps and between all involved systems necessary to incrementally update image copy backups.

Backup method:

Tested with:

* Oracle DB 11.2.0.4, but any 11gR2 should work
* Oracle DB 12.1.0.2 both non-CDB and CDB
* Oracle Enterprise Linux 6u6 or later
* Oracle Enterprise Linux 7

Requirements:

* Python 2.6
* Pyhton-Requests
* Direct NFS in use for Oracle DB (optional, but highly recommended)
* Storage for backups must support NFS, snapshots, cloning and optionally compression (deduplication)

It currently supports Oracle ZFS Storage Appliance as backup storage system, but it is easy to extend for other systems. It is planned to add support for Netapp next.

## Setting up backup

### Prepare OS

```
yum install python-requests python
```

### Prepare Oracle database home

Compile Direct NFS to Oracle kernel: [You can find a good quide here](http://www.orafaq.com/wiki/Direct_NFS)

Database needs to be bounced before Direct NFS is activated.

Without Direct NFS, Oracle requires HARD mounted NFS mount points, or the following error will be returned: ORA-27054: NFS file system not mounted with correct options

This means that if something happend with backup storage that is becomes unresponsive, OS kernel would not hang the IO database sends to the storage. So database can hang when archivelogs are stored in that mount point.

Direct NFS is only needed to allow backups and archivelogs to be stored in SOFT mounted NFS mount points. This means that IO to unresponsive NFS will not hang, the client will get an error instead.

Essential when Oracle ZFS Storage Appliance is used.

### Prepare Oracle ZFS Storage Appliance for receiving the backups

These are recommendation that make backup and autorestore handling easier.

### Scheduling backups

Option 1

Option 2

[Jenkins](https://jenkins.io/) is a popular continous integration and continous delivery tool that can be also used for scheduling tasks on remote servers over SSH. This solution will provide a good GUI overview of all database backups and also provides full backup log file through Jenkins GUI.

## Setting up automatic restore

NBNB! It is VERY important to use a separate host tu run the autorestore tests that have no access to production database storage! Because it may be possible that Oracle tries to first overwrite or delete the necessary database files from their original locations! It is recommended to use a small server (small virtual machine) that only has access to backup storage over NFS.
