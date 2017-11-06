# encoding: utf-8

import json
import httplib
import requests
from Queue import Queue, Empty
from threading import Thread
import datetime
import time
import sys
sys.path.append('..')
import common.vnlog



FUNCTIONCODE_GETACCOUNTS = 1 #查账户、持仓/v1/accounts
FUNCTIONCODE_GETTRADES = 2 #查成交 /v1/markets/trades
FUNCTIONCODE_GETORDERS = 4 #查委托/v1/orders
FUNCTIONCODE_GETACCOUNTINFO = 5 #暂未实现/v1/markets

FUNCTIONCODE_GETPRICES = 6 #取ticker
FUNCTIONCODE_GETBOOK = 7 #查订单薄
FUNCTIONCODE_GETCANDLES = 8#k线

FUNCTIONCODE_SENDORDER = 9 #下单
FUNCTIONCODE_CANCELORDER = 10 #撤单


########################################################################
class ZhcoinApi(object):
	""""""
	DEBUG = False

	#----------------------------------------------------------------------
	def __init__(self):
		"""Constructor"""
		self.token = ''
		self.accountId = ''
		self.headers = {}
		self.restDomain = ''
		self.streamDomain = ''
		self.session = None

		self.functionSetting = {}

		self.active = False         # API的工作状态

		self.reqID = 0              # 请求编号
		self.reqQueue = Queue()     # 请求队列
		self.reqThread = Thread(target=self.processQueue)   # 请求处理线程

		self.taskInterval = 5
		self.symList = []  # 订阅的合约
		self.taskThread = Thread(target=self.run)  # 处理任务的线程


		self.lastQryTime = None

	#----------------------------------------------------------------------
	def init(self, restDomain, token, accountId):
		"""初始化接口"""
		self.restDomain = restDomain
		self.session = requests.Session()

		self.token = token
		self.accountId = accountId


		self.headers['Authorization'] = self.token
		self.headers['Accept'] = 'application/json'
		self.headers['Content-type'] = 'application/json'


		self.initFunctionSetting(FUNCTIONCODE_GETACCOUNTS, {'path': '/v1/accounts',
														  'method': 'GET'})

		self.initFunctionSetting(FUNCTIONCODE_GETTRADES, {'path': '/v1/markets/trades',
																'method': 'GET'})

		self.initFunctionSetting(FUNCTIONCODE_GETORDERS, {'path': '/v1/orders',
														  'method': 'GET'})

		self.initFunctionSetting(FUNCTIONCODE_GETACCOUNTINFO, {'path': '/v1/markets',
														  'method': 'GET'})


		self.initFunctionSetting(FUNCTIONCODE_GETPRICES, {'path': '/v1/markets/ticker',
														  'method': 'GET'})

		self.initFunctionSetting(FUNCTIONCODE_GETBOOK, {'path': '/v1/markets/book',
														  'method': 'GET'})

		self.initFunctionSetting(FUNCTIONCODE_GETCANDLES, {'path': '/v1/markets/candles',
														  'method': 'GET'})


		self.initFunctionSetting(FUNCTIONCODE_SENDORDER, {'path': '/v1/orders',
															 'method': 'POST'})

		self.initFunctionSetting(FUNCTIONCODE_CANCELORDER, {'path': '/v1/orders/cancel',
															 'method': 'GET'})

		self.active = True
		self.reqThread.start()
		self.taskThread.start()
		# self.streamEventsThread.start()
		# self.streamPricesThread.start()

	#----------------------------------------------------------------------
	def exit(self):
		"""退出接口"""
		if self.active:
			self.active = False
			self.reqThread.join()
			self.taskThread.join()

	def run(self):
		"""连续运行"""
		while self.active:
			try:
				for sym_exch_tuple in self.symList:
					self.getTicker(sym_exch_tuple[0], sym_exch_tuple[1])
					self.getDepth(sym_exch_tuple[0], 'LEVEL_1', 'L05')
			except Exception as e:
				print e

			time.sleep(self.taskInterval)



	#----------------------------------------------------------------------
	def initFunctionSetting(self, code, setting):
		"""初始化API功能字典"""
		self.functionSetting[code] = setting

	#----------------------------------------------------------------------
	def processRequest(self, req):
		"""发送请求并通过回调函数推送数据结果"""
		url = req['url']
		method = req['method']
		params = req['params']
		code   = req['code']
		path = req['path']

		# print 'request:', url,method,params

		stream = False
		if 'stream' in req:
			stream = req['stream']

		r = None
		error = None

		if method in ['GET', 'DELETE']:
			self.lastQryTime = datetime.datetime.now()
			myreq = requests.Request(method, url, headers=self.headers, params=params)

			pre = myreq.prepare()
			try:
				rr = self.session.send(pre, stream=stream)
				if self.DEBUG:
					print 'status code: %s' % rr.status_code
					print 'reason: %s' %rr.reason
				if rr.reason == 'OK':
					r = rr.json()
				else:
					error = rr.json()
			except Exception, e:
				error = e

		elif method in ['POST', 'PUT']:
			try:
				conn = httplib.HTTPConnection(host=self.restDomain.replace("http://",""))
				conn.request(method, path, json.JSONEncoder().encode(params), self.headers)
				response = conn.getresponse()

				if self.DEBUG:
					print response.status
					print response.reason

				if response.status in [200, 201]:
					r = json.loads(response.read())
				else:
					error = json.loads(response.read().replace("'", "\""))

				conn.close()

			except Exception, e:
				error = e
		if self.DEBUG:
			print 'path: %s' %path
			print 'r: %s' %r
			print 'error: %s' %error
		# print 'response: %s' % r
		return r, error

	#----------------------------------------------------------------------
	def processQueue(self):
		"""处理请求队列中的请求"""
		while self.active:
			try:
				req = self.reqQueue.get(block=True, timeout=1)  # 获取请求的阻塞为一秒
				callback = req['callback']
				reqID = req['reqID']

				r, error = self.processRequest(req)

				if not error:
					try:
						# data = r.json()
						data = r
						if self.DEBUG:
							print callback.__name__

						callback(data, reqID, req['params'])

					except Exception as e:
						self.onError(str(e), reqID)
				else:
					self.onError(str(error), reqID)
			except Empty:
				pass


	#----------------------------------------------------------------------
	def sendRequest(self, code, params, callback, optional=''):
		"""发送请求"""
		setting = self.functionSetting[code]

		url = self.restDomain + setting['path']
		if optional:
			url = url + '/' + optional

		self.reqID += 1

		req = {'url': url,
			   'method': setting['method'],
			   'path': setting['path'],
			   'params': params,
			   'callback': callback,
			   'reqID': self.reqID,
			   'code': code}
		self.reqQueue.put(req)

		return self.reqID

	#----------------------------------------------------------------------
	def onError(self, error, reqID):
		"""错误信息回调"""
		print error, reqID



	def subscribe(self, symbol, exchange):
		self.symList.append((symbol,exchange))

	def getTicker(self, market_id, exchange):
		"""查询价格"""
		return self.sendRequest(FUNCTIONCODE_GETPRICES, {'market_id':market_id, 'exchange':exchange}, self.onTicker)

	def onTicker(self, data, reqID, params):
		print 'onTicker', data

	def getDepth(self, market_id, level, depth):
		return self.sendRequest(FUNCTIONCODE_GETBOOK, {'market_id': market_id, 'level': level, 'depth':depth},   self.onDepth)

	def onDepth(self, data, reqID, params):
		print 'onDepth', data

	def getCandles(self, market_id, start, end, granularity, limit, exchange):
		return self.sendRequest(FUNCTIONCODE_GETCANDLES, {'market_id': market_id, 'start': start, 'end': end, 'granularity':granularity, 'limit':limit, 'exchange':exchange}, self.onCandles)

	def onCandles(self, data, reqID, params):
		pass

	def getAccounts(self):
		"""查询用户的所有账户"""
		return self.sendRequest(FUNCTIONCODE_GETACCOUNTS, {}, self.onGetAccounts)

	def onGetAccounts(self, data, reqID, params):
		print 'onGetAccounts', data


	def getOrders(self, market_id, status):
		"""查委托  , 'page.skip':0, 'page.take':0"""
		return self.sendRequest(FUNCTIONCODE_GETORDERS, {'market_id':market_id, 'status':status}, self.onGetOrders)

	def onGetOrders(self, data, reqID, params):
		"""回调函数"""
		pass

	#----------------------------------------------------------------------
	def sendOrder(self, market_id, side, price, size):
		return self.sendRequest(FUNCTIONCODE_SENDORDER, {'market_id':market_id, 'side':side, 'type':'limit', 'price':float(price), 'size':float(size)}, self.onSendOrder)

	#----------------------------------------------------------------------
	def onSendOrder(self, data, reqID, params):
		print 'onSendOrder', data


	#----------------------------------------------------------------------
	def cancelOrder(self, market_id, order_ids):
		return self.sendRequest(FUNCTIONCODE_CANCELORDER, {'market_id':market_id, 'order_ids':order_ids}, self.onCancelOrder)

	def onCancelOrder(self, data, reqID, params):
		print 'onCancelOrder', data



	#----------------------------------------------------------------------
	def onPrice(self, data):
		"""行情推送"""
		print data

	#----------------------------------------------------------------------
	def onEvent(self, data):
		"""事件推送（成交等）"""
		print data

	#----------------------------------------------------------------------
	def processStreamPrices(self):
		"""获取价格推送"""
		# 首先获取所有合约的代码
		setting = self.functionSetting[FUNCTIONCODE_GETINSTRUMENTS]
		req = {'url': self.restDomain + setting['path'],
			   'method': setting['method'],
			   'params': {'accountId': self.accountId}}
		r, error = self.processRequest(req)
		if r:
			try:
				data = r.json()
				symbols = [d['instrument'] for d in data['instruments']]
			except Exception, e:
				self.onError(e, -1)
				return
		else:
			self.onError(error, -1)
			return

		# 然后订阅所有的合约行情
		setting = self.functionSetting[FUNCTIONCODE_STREAMPRICES]
		params = {'accountId': self.accountId,
				  'instruments': ','.join(symbols)}
		req = {'url': self.streamDomain + setting['path'],
			   'method': setting['method'],
			   'params': params,
			   'stream': True}
		r, error = self.processRequest(req)

		if r:
			try:
				for line in r.iter_lines():
					if line:
						try:
							msg = json.loads(line)

							if self.DEBUG:
								print self.onPrice.__name__

							self.onPrice(msg)
						except Exception as e:
							self.onError(e, -1)

					if not self.active:
						break
			except Exception as e:
				self.onError(e, -1)
		else:
			self.onError(error, -1)

	#----------------------------------------------------------------------
	def processStreamEvents(self):
		"""获取事件推送"""
		setting = self.functionSetting[FUNCTIONCODE_STREAMEVENTS]
		req = {'url': self.streamDomain + setting['path'],
			   'method': setting['method'],
			   'params': {},
			   'stream': True}
		r, error = self.processRequest(req)
		if r:
			for line in r.iter_lines():
				if line:
					try:
						msg = json.loads(line)

						if self.DEBUG:
							print self.onEvent.__name__

						self.onEvent(msg)
					except Exception, e:
						self.onError(e, -1)

				if not self.active:
					break
		else:
			self.onError(error, -1)


if __name__ == '__main__':
	import os
	api = ZhcoinApi()
	root_path = common.vnlog.getRootPath()
	cfg_file_path = os.path.join(root_path,'cfg','ZHCOIN_connect.json')
	f= file(cfg_file_path)
	setting = json.load(f)

	api.init(setting['restDomain'], setting['token'], '')

	while 1:
		time.sleep(1)
		input = raw_input('0.查价格 1.查深度图 2.订阅价格 3.买 4.卖 5.撤\n')
		if input == '0':
			api.getTicker('BTC-CNY', 'TOKENBANK')
		elif input == '1':
			api.getDepth('BTC-CNY', 'LEVEL_1', 'L20')
		elif input == '2':
			api.subscribe('BTC-CNY', 'TOKENBANK')
		elif input=='3':
			price = raw_input('price:')
			volume = raw_input('volume:')
			api.sendOrder('BTC-CNY', 'buy', price, volume)
		elif input == '4':
			price = raw_input('price:')
			volume = raw_input('volume:')
			api.sendOrder('BTC-CNY', 'sell', price, volume)
		elif input=='5':
			order_id = raw_input('order_id:')
			api.cancelOrder('BTC-CNY', order_id)


	api.exit()

