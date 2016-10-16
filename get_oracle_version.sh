#!/bin/sh

oraclebin="${1}/bin/oracle"
if [ -f "${oraclebin}" ]; then
  strings "${oraclebin}" | grep NLSRTL | cut -d " " -f 3
else
  exit 1
fi

