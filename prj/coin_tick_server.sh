#! /bin/bash

cond=`ps -ef |grep python|grep OKCOIN`
if [ -z "$cond" ]; then
	echo "start okcoin_tick_server"
	/usr/local/anaconda2/bin/python /home/wudian/py_prj/prj/dataRecorder/vtDR.py OKCOIN &
else
	echo "okcoin_tick_server is running"
fi
