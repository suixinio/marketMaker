# encoding: UTF-8

"""
包含一些开发中常用的函数
"""

import os
import decimal
import json
from datetime import datetime
import sys


MAX_NUMBER = 10000000000000
MAX_DECIMAL = 4

def getRootPath():
	path = ''
	for i in range(1,10):
		path = os.path.abspath(os.path.dirname('../'*i))
		path = path 
		if os.path.exists(path+'/log/') and os.path.exists(path+'/cfg/'):
			break
	return path

ROOT_PATH = getRootPath()
def appendPath(path):
	files = os.listdir(path)
	for fi in files:
		fi_d = os.path.join(path, fi)
		if os.path.isdir(fi_d):
			sys.path.append(fi_d)
			appendPath(fi_d)

appendPath(ROOT_PATH)

#----------------------------------------------------------------------
def safeUnicode(value):
    """检查接口数据潜在的错误，保证转化为的字符串正确"""
    # 检查是数字接近0时会出现的浮点数上限
    if type(value) is int or type(value) is float:
        if value > MAX_NUMBER:
            value = 0
    
    # 检查防止小数点位过多
    if type(value) is float:
        d = decimal.Decimal(str(value))
        if abs(d.as_tuple().exponent) > MAX_DECIMAL:
            value = round(value, ndigits=MAX_DECIMAL)
    
    return unicode(value)

#----------------------------------------------------------------------
def loadMongoSetting():
    """载入MongoDB数据库的配置"""
    fileName = 'VT_setting.json'
    fileName = os.path.join(ROOT_PATH, 'cfg', fileName)  
    
    try:
        f = file(fileName)
        setting = json.load(f)
        host = setting['mongoHost']
        port = setting['mongoPort']
    except:
        host = 'localhost'
        port = 27017
        
    return host, port

#----------------------------------------------------------------------
def todayDate():
    """获取当前本机电脑时间的日期"""
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)    

def priceUniform(price):
    return int(round(float(price)*100))/100.0

def volumeUniform(volume):
    return int(round(float(volume) * 10000)) / 10000.0
