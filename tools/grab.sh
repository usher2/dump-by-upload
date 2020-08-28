#!/bin/sh

apiurl=$1
if [ -z "$apiurl" ]; then
        echo "apiurl is empty"
        exit 1
fi

token=$2
if [ -z "$token" ]; then
        echo "token is empty"
        exit 1
fi

datadir=$3
if [ -z "$datadir" ]; then
        datadir="./"
fi

find ${datadir} -name "*.xml" -print | while read datafile; do
        if [ -f "$datafile" ]; then
                echo "${datafile}"
                result=`curl -f -s -X POST -H "Authorization: Bearer ${token}" -F "file=@${datafile}" ${apiurl}/upload`
                if [ $? -eq 0 ]; then
                        echo "${datafile} was successfully uploaded!"
                else
                        echo "Something wrong with ${datafile}"
                        continue
                fi
        fi
done
