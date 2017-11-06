# encoding: UTF-8
import logging
import os
from vtFunction import *


class vnLog(object):
	def __init__(self, name):
		# 创建一个logger
		self.logger = logging.getLogger(name)
		self.logger.setLevel(logging.DEBUG)
		# 创建一个handler，用于写入日志文件
		path = getRootPath()
		path = os.path.join(path, 'log', name)
		fh = logging.FileHandler(path)
		fh.setLevel(logging.DEBUG)
		# 再创建一个handler，用于输出到控制台
		ch = logging.StreamHandler()
		ch.setLevel(logging.DEBUG)
		# 定义handler的输出格式
		formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
		fh.setFormatter(formatter)
		ch.setFormatter(formatter)
		# 给logger添加handler
		self.logger.addHandler(fh)
		self.logger.addHandler(ch)

	def write(self, log):
		self.logger.info(log)
		

if __name__ == '__main__':
	print getRootPath()
	vnlog = vnLog('test_log.txt')
	vnlog.write('hi wu')