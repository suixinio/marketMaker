# encoding: UTF-8

'''
本文件中实现了行情数据记录引擎，用于汇总TICK数据，并生成K线插入数据库。

使用DR_setting.json来配置需要收集的合约，以及主力合约代码。
'''

import json
import os
import copy
from collections import OrderedDict
from datetime import datetime, timedelta
from Queue import Queue
from threading import Thread

from eventEngine import *
from vtGateway import VtSubscribeReq, VtLogData
from drBase import *
from vtFunction import *
import vnlog

########################################################################
class DrEngine(object):
	"""数据记录引擎"""
	settingFileName = 'DR_setting.json'
	settingFileName = getRootPath() + '/cfg/' + settingFileName

	#----------------------------------------------------------------------
	def __init__(self, mainEngine, eventEngine):
		"""Constructor"""
		self.mainEngine = mainEngine
		self.eventEngine = eventEngine
		
		# 当前日期
		self.today = todayDate()
		
		# 主力合约代码映射字典，key为具体的合约代码（如IF1604），value为主力合约代码（如IF0000）
		self.activeSymbolDict = {}
		
		# Tick对象字典
		self.tickDict = {}
		
		# K线对象字典
		self.barDict = {}
		
		# 负责执行数据库插入的单独线程相关
		self.active = False                     # 工作状态
		self.queue = Queue()                    # 队列
		self.thread = Thread(target=self.run)   # 线程
		
		# 载入设置，订阅行情
		self.loadSetting()
		
		self.logger = vnlog.vnLog('vtDR.log')
		
	#----------------------------------------------------------------------
	def loadSetting(self):
		"""载入设置"""
		with open(self.settingFileName) as f:
			drSetting = json.load(f)
			
			# 如果working设为False则不启动行情记录功能
			working = drSetting['working']
			if not working:
				return

			CTP_working = drSetting['CTP_working'] 

			if 'tick' in drSetting:
				l = drSetting['tick']
				
				for setting in l:
					symbol = setting[0]
					vtSymbol = symbol

					if setting[1] in ['OKCOIN', 'BTCC']:
						vtSymbol = '.'.join([symbol, setting[1]])

					req = VtSubscribeReq()
					req.symbol = setting[0]

					self.mainEngine.subscribe(req, setting[1])
					
					drTick = DrTickData()           # 该tick实例可以用于缓存部分数据（目前未使用）
					self.tickDict[vtSymbol] = drTick
					
			if 'bar' in drSetting:
				l = drSetting['bar']
				
				for setting in l:
					symbol = setting[0]
					vtSymbol = symbol
					
					req = VtSubscribeReq()
					req.symbol = symbol                    

					if len(setting)>=3:
						req.exchange = setting[2]
						vtSymbol = '.'.join([symbol, req.exchange])

					if len(setting)>=5:
						req.currency = setting[3]
						req.productClass = setting[4]                    
					
					self.mainEngine.subscribe(req, setting[1])  
					
					bar = DrBarData() 
					self.barDict[vtSymbol] = bar
					
			if 'active' in drSetting:
				d = drSetting['active']
				
				# 注意这里的vtSymbol对于IB和LTS接口，应该后缀.交易所
				for activeSymbol, vtSymbol in d.items():
					self.activeSymbolDict[vtSymbol] = activeSymbol
			
			# 启动数据插入线程
			self.start()
			
			# 注册事件监听
			self.registerEvent()    

	#----------------------------------------------------------------------
	def procecssTickEvent(self, event):
		"""处理行情推送"""
		tick = event.dict_['data']
		# print tick.__dict__
		vtSymbol = tick.vtSymbol


		# 转化Tick格式
		drTick = DrTickData()
		d = drTick.__dict__
		for key in d.keys():
			if key != 'datetime':
				d[key] = tick.__getattribute__(key)
		if tick.exchange in ["HUOBI", "BTCC", "HUOBIETH"]:
			drTick.datetime = datetime.strptime(' '.join([tick.date, tick.time]), '%Y%m%d %H:%M:%S')
		else:
			drTick.datetime = datetime.strptime(' '.join([tick.date, tick.time]), '%Y%m%d %H:%M:%S.%f')

		#print drTick.datetime, vtSymbol
		#self.logger.write(vtSymbol)
		# 更新Tick数据
		# if vtSymbol in self.tickDict:
		# record all data from API
		if 1:
			self.insertData(TICK_DB_NAME, vtSymbol, drTick)
			
			if vtSymbol in self.activeSymbolDict:
				activeSymbol = self.activeSymbolDict[vtSymbol]
				self.insertData(TICK_DB_NAME, activeSymbol, drTick)

			# print drTick.__dict__
			# 发出日志
			# self.writeDrLog(u'记录Tick数据%s，时间:%s, last:%s, bid1:%s, bid2:%s, bid3:%s, bid4:%s, bid5:%s, ask1:%s, ask2:%s, ask3:%s, ask4:%s, ask5:%s'
			#                 %(drTick.vtSymbol, drTick.time, drTick.lastPrice, drTick.bidPrice1, drTick.bidPrice2, drTick.bidPrice3, drTick.bidPrice4, drTick.bidPrice5
			#                   , drTick.askPrice1, drTick.askPrice2, drTick.askPrice3, drTick.askPrice4, drTick.askPrice5))
			self.writeDrLog(
				u'记录Tick数据%s，时间:%s, last:%s, bid1:%s, bid2:%s, bid3:%s, ask1:%s, ask2:%s, ask3:%s'
				% (drTick.vtSymbol, drTick.time, drTick.lastPrice, drTick.bidPrice1, drTick.bidPrice2, drTick.bidPrice3,
				   drTick.askPrice1, drTick.askPrice2, drTick.askPrice3))
			
		# 更新分钟线数据
		if vtSymbol in self.barDict:
			bar = self.barDict[vtSymbol]
			
			# 如果第一个TICK或者新的一分钟
			if not bar.datetime or bar.datetime.minute != drTick.datetime.minute:    
				if bar.vtSymbol:
					newBar = copy.copy(bar)
					self.insertData(MINUTE_DB_NAME, vtSymbol, newBar)
					
					if vtSymbol in self.activeSymbolDict:
						activeSymbol = self.activeSymbolDict[vtSymbol]
						self.insertData(MINUTE_DB_NAME, activeSymbol, newBar)                    
					
					self.writeDrLog(u'记录分钟线数据%s，时间:%s, O:%s, H:%s, L:%s, C:%s' 
									%(bar.vtSymbol, bar.time, bar.open, bar.high, 
									  bar.low, bar.close))
						 
				bar.vtSymbol = drTick.vtSymbol
				bar.symbol = drTick.symbol
				bar.exchange = drTick.exchange
				
				bar.open = drTick.lastPrice
				bar.high = drTick.lastPrice
				bar.low = drTick.lastPrice
				bar.close = drTick.lastPrice
				
				bar.date = drTick.date
				bar.time = drTick.time
				bar.datetime = drTick.datetime
				bar.volume = drTick.volume
				bar.openInterest = drTick.openInterest        
			# 否则继续累加新的K线
			else:                               
				bar.high = max(bar.high, drTick.lastPrice)
				bar.low = min(bar.low, drTick.lastPrice)
				bar.close = drTick.lastPrice            

	#----------------------------------------------------------------------
	def registerEvent(self):
		"""注册事件监听"""
		self.eventEngine.register(EVENT_TICK, self.procecssTickEvent)

	#----------------------------------------------------------------------
	def insertData(self, dbName, collectionName, data):
		"""插入数据到数据库（这里的data可以是CtaTickData或者CtaBarData）"""
		self.queue.put((dbName, collectionName, data.__dict__))
		
	#----------------------------------------------------------------------
	def run(self):
		"""运行插入线程"""
		while self.active:
			try:
				dbName, collectionName, d = self.queue.get(block=True, timeout=1)
				self.mainEngine.dbInsert(dbName, collectionName, d)
			except Empty:
				pass
	#----------------------------------------------------------------------
	def start(self):
		"""启动"""
		self.active = True
		self.thread.start()
		
	#----------------------------------------------------------------------
	def stop(self):
		"""退出"""
		if self.active:
			self.active = False
			self.thread.join()
		
	#----------------------------------------------------------------------
	def writeDrLog(self, content):
		"""快速发出日志事件"""
		log = VtLogData()
		log.logContent = content
		event = Event(type_=EVENT_DATARECORDER_LOG)
		event.dict_['data'] = log
		self.eventEngine.put(event)   

