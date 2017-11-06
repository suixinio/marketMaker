# encoding: UTF-8

import os
import json
from datetime import datetime
from copy import copy
import pandas as pd
from threading import Condition

import sys
sys.path.append('.')
sys.path.append('..')
from api import vnzhcoin
from api.vnzhcoin import ZhcoinApi
from vtGateway import *
from common.vnlog import *


# 价格类型映射
priceTypeMap = {}
priceTypeMap[PRICETYPE_LIMITPRICE] = 'limit'
priceTypeMap[PRICETYPE_MARKETPRICE] = 'market'
priceTypeMapReverse = {v: k for k, v in priceTypeMap.items()}

# 方向类型映射
directionMap = {}
directionMap[DIRECTION_LONG] = 'buy'
directionMap[DIRECTION_SHORT] = 'sell'
directionMapReverse = {v: k for k, v in directionMap.items()}

# order status map
dealStatusMap = {}
# dealStatusMap[STATUS_NOTTRADED] = 'ORDER_NEW'
dealStatusMap[STATUS_PENDING] = 'ORDER_PENDING'
dealStatusMap[STATUS_PARTTRADED] = 'ORDER_PARTIALLY_EXECUTED'
dealStatusMap[STATUS_ALLTRADED] = 'ORDER_FULLY_EXECUTED'
dealStatusMap[STATUS_CANCELLED] = 'ORDER_CANCELLED'
dealStatusMapReverse = {v: k for k, v in dealStatusMap.items()}
dealStatusMapReverse['ORDER_CANCELLED_BY_MARKET'] = STATUS_CANCELLED
dealStatusMapReverse['ORDER_PARTIALLY_EXECUTED_THEN_CANCELLED_BY_MARKET'] = STATUS_CANCELLED

# 从众安交易所订阅需要填的字段
ZHONGAN_EXCHANGE = 'TOKENBANK'
ZHONGAN_BTCCNY = 'btc-cny'
ZHONGAN_ETHCNY = 'eth-cny'

spotSymbolMap = {}
spotSymbolMap[SYMBOL_BTC_CNY] = ZHONGAN_BTCCNY
spotSymbolMap[SYMBOL_ETH_CNY] = ZHONGAN_ETHCNY
spotSymbolMapReverse = {v: k for k, v in spotSymbolMap.items()}



########################################################################
class ZhcoinGateway(VtGateway):
	"""ZHCOIN接口"""

	# ----------------------------------------------------------------------
	def __init__(self, eventEngine, gatewayName='ZHCOIN'):
		"""Constructor"""
		super(ZhcoinGateway, self).__init__(eventEngine, gatewayName)

		self.api = Api(self)

		self.qryEnabled = False  # 是否要启动循环查询

	# ----------------------------------------------------------------------
	def connect(self):
		"""连接"""
		# 载入json文件
		fileName = self.gatewayName + '_connect.json' 
		fileName = os.path.join(getRootPath(), 'cfg', fileName)

		try:
			f = file(fileName)
		except IOError:
			log = VtLogData()
			log.gatewayName = self.gatewayName
			log.logContent = u'读取连接配置出错，请检查'
			self.onLog(log)
			return

		# 解析json文件
		setting = json.load(f)
		try:
			restDomain = str(setting['restDomain'])
			token = str(setting['token'])
			accountId = str(setting['accountId'])
			# settingName = str(setting['settingName'])
		except KeyError:
			log = VtLogData()
			log.gatewayName = self.gatewayName
			log.logContent = u'连接配置缺少字段，请检查'
			self.onLog(log)
			return

		# 初始化接口
		self.api.init(restDomain, token, accountId)
		self.api.writeLog(u'众安接口初始化成功')


		# 初始化并启动查询
		self.initQuery()

	# ----------------------------------------------------------------------
	def subscribe(self, subscribeReq):

		self.api.subscribe(subscribeReq)

		contract = VtContractData()
		contract.gatewayName = self.gatewayName
		contract.symbol = subscribeReq.symbol
		contract.exchange = EXCHANGE_ZHCOIN
		contract.vtSymbol = '.'.join([contract.symbol, contract.exchange])
		contract.name = ''
		contract.size = 1
		contract.priceTick = 0.01
		contract.productClass = PRODUCT_SPOT
		self.onContract(contract)

	# ----------------------------------------------------------------------
	def sendOrder(self, orderReq):
		"""发单"""
		vtOrderID = self.api.sendOrder_(orderReq)

		return vtOrderID

	# ----------------------------------------------------------------------
	def cancelOrder(self, orderIdList):
		"""撤单"""
		for id in orderIdList:
			self.api.cancelOrder(ZHONGAN_BTCCNY, id)

	# ----------------------------------------------------------------------
	def qryAccount(self):
		"""查询账户资金"""
		self.api.getAccounts()

	def qryOrder(self):
		#当前委托
		self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_PENDING')
		self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_PARTIALLY_EXECUTED')

	def qryTrade(self):
		#历史委托 ORDER_FULLY_EXECUTED,ORDER_CANCELLED,ORDER_CANCELLED_BY_MARKET,ORDER_PARTIALLY_EXECUTED_THEN_CANCELLED_BY_MARKET
		self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_FULLY_EXECUTED')
		self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_CANCELLED')
		# self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_CANCELLED_BY_MARKET')
		# self.api.getOrders(ZHONGAN_BTCCNY, 'ORDER_PARTIALLY_EXECUTED_THEN_CANCELLED_BY_MARKET')

	def qryPosition(self):
		"""查询持仓"""
		pass


	# ----------------------------------------------------------------------
	def close(self):
		"""关闭"""
		self.api.exit()

	# ----------------------------------------------------------------------
	def initQuery(self):
		"""初始化连续查询"""
		if self.qryEnabled:
			# 需要循环的查询函数列表
			self.qryFunctionList = [self.qryAccount, self.qryOrder, self.qryTrade]


			self.qryCount = 0  # 查询触发倒计时
			self.qryTrigger = 2  # 查询触发点
			self.qryNextFunction = 0  # 上次运行的查询函数索引

			self.startQuery()

	# ----------------------------------------------------------------------
	def query(self, event):
		"""注册到事件处理引擎上的查询函数"""
		if self.qryEnabled:
			 self.qryCount += 1
			 if self.qryCount > self.qryTrigger:
				# 清空倒计时
				self.qryCount = 0

				# 执行查询函数
				function = self.qryFunctionList[self.qryNextFunction]
				function()

				# 计算下次查询函数的索引，如果超过了列表长度，则重新设为0
				self.qryNextFunction += 1
				if self.qryNextFunction == len(self.qryFunctionList):
					self.qryNextFunction = 0

	# ----------------------------------------------------------------------
	def startQuery(self):
		"""启动连续查询"""
		self.eventEngine.register(EVENT_TIMER, self.query)

	# ----------------------------------------------------------------------
	def setQryEnabled(self, qryEnabled):
		"""设置是否要启动循环查询"""
		self.qryEnabled = qryEnabled


########################################################################
class Api(vnzhcoin.ZhcoinApi):
	"""ZHCOIN的API实现"""

	# ----------------------------------------------------------------------
	def __init__(self, gateway):
		"""Constructor"""
		super(Api, self).__init__()

		self.gateway = gateway  # gateway对象
		self.gatewayName = gateway.gatewayName  # gateway对象名称

		self.tickDict = {}
		self.lastOrderID = ''

		self.cond = Condition()

	def subscribe(self, subscribeReq):
		self.symList.append((spotSymbolMap[subscribeReq.symbol], ZHONGAN_EXCHANGE))

		tick = VtTickData()
		tick.exchange = EXCHANGE_ZHCOIN
		tick.symbol = subscribeReq.symbol
		tick.vtSymbol = '.'.join([tick.symbol, tick.exchange])
		tick.gatewayName = self.gatewayName
		self.tickDict[tick.symbol] = tick

	# ----------------------------------------------------------------------
	def onError(self, error, reqID):
		"""错误信息回调"""
		err = VtErrorData()
		err.gatewayName = self.gatewayName
		err.errorMsg = error
		self.gateway.onError(err)

	#----------------------------------------------------------------------
	def onGetAccounts(self, data, reqID, params):
		ls = data['data'][0]['subaccounts']
		for l in ls:
			account = VtAccountData()
			account.accountID = data['data'][0]['userId']
			account.vtAccountID = '.'.join([self.gatewayName, account.accountID])

			account.balance = float(l['balance'])
			account.available = float(l['available'])
			#account.margin = float(l['order_hold'])
			account.currency = l['currency']
			self.gateway.onAccount(account)

	#----------------------------------------------------------------------
	def onSendOrder(self, data, reqID, params):
		l = data['data']

		order = VtOrderData()
		order.gatewayName = self.gatewayName

		order.symbol = spotSymbolMapReverse[l['market_id']]
		order.exchange = EXCHANGE_ZHCOIN
		order.vtSymbol = '.'.join([order.symbol, order.exchange])

		order.orderID = l['id']
		order.vtOrderID = '.'.join([self.gatewayName, order.orderID])

		order.direction = DIRECTION_LONG if l['side'][0] == 'B'else DIRECTION_SHORT
		order.offset = OFFSET_NONE

		order.status = dealStatusMapReverse.get(l['status'], STATUS_UNKNOWN)
		order.price = float(l['price'])
		order.totalVolume = l['original_size']
		order.orderTime = l['updated_at']

		self.gateway.onOrder(order)

	# ----------------------------------------------------------------------
	def onCancelOrder(self, data, reqID, params):
		"""回调函数"""

		ls = data['data']

		for l in ls:
			order = VtOrderData()
			order.gatewayName = self.gatewayName
			order.symbol = spotSymbolMapReverse[l['market_id']]
			order.exchange = EXCHANGE_ZHCOIN
			order.vtSymbol = '.'.join([order.symbol, order.exchange])

			order.orderID = l['id']
			order.vtOrderID = '.'.join([self.gatewayName, order.orderID])

			order.direction = DIRECTION_LONG if l['side'][0] == 'B'else DIRECTION_SHORT
			order.offset = OFFSET_NONE

			order.status = dealStatusMapReverse.get(l['status'], STATUS_UNKNOWN)
			order.price = float(l['price'])
			order.totalVolume = l['original_size']

			order.orderTime = l['created_at']
			order.cancelTime = l['updated_at']

			self.gateway.onOrder(order)


	#----------------------------------------------------------------------
	def onTicker(self, data, reqID, params):
		if 'data' not in data:
			return

		l = data['data']
		if not l:
			return

		symbol = spotSymbolMapReverse[params['market_id']]

		tick = self.tickDict[symbol]

		tick.date = datetime.now().date().strftime("%Y%m%d")
		tick.time = datetime.now().time().strftime("%H:%M:%S")
		tick.highPrice = l['daily_high']
		#tick.lowPrice = l['daily_low']
		tick.lastPrice = l['last_price']
		tick.volume = l['daily_volume']

		self.gateway.onTick(tick)

	def onDepth(self, data, reqID, params):
		symbol = spotSymbolMapReverse[params['market_id']]
		tick = self.tickDict[symbol]


		#交易所 目前bids为卖  asks为买  ---2017.8.10吴典
		d = data['data']
		if d.has_key('bids'):
			bids = d['bids']
			# tick.asks = []
			# for i in range(0, len(bids)):
			#     tick.asks.append([bids[i]['price'], bids[i]['size']])
			tick.bids = []
			for i in range(0, len(bids)):
				tick.bids.append([bids[i]['price'], bids[i]['size']])

		if d.has_key('asks'):
			asks = d['asks']
			# tick.bids = []
			# for i in range(0, len(asks)):
			#     tick.bids.append([asks[i]['price'], asks[i]['size']])
			tick.asks = []
			for i in range(0, len(asks)):
				tick.asks.append([asks[i]['price'], asks[i]['size']])

		now = datetime.now()
		tick.time = now.strftime('%H:%M:%S')
		tick.date = now.strftime('%Y%m%d')

		self.gateway.onTick(tick)

	def onCandles(self, data, reqID, params):
		pass
	# ----------------------------------------------------------------------
	def onGetOrders(self, data, reqID, params):
		if not 'data' in data:
			return

		l = data['data']

		for d in l:
			# d = res.ix[i]
			order = VtOrderData()
			order.gatewayName = self.gatewayName

			order.symbol = spotSymbolMapReverse[d['market_id']]
			order.exchange = EXCHANGE_ZHCOIN
			order.vtSymbol = '.'.join([order.symbol, order.exchange])

			order.orderID = str(d['id'])
			order.vtOrderID = '.'.join([self.gatewayName, order.orderID])

			order.direction = d['side'][0]
			order.offset = OFFSET_NONE
			order.price = float(d['price'])
			order.totalVolume = float(d['original_size'])
			#order.tradedVolume = float(d.get('executed_size'))
			order.status = dealStatusMapReverse.get(d['status'], STATUS_UNKNOWN)

			order.orderTime = d['updated_at']

			dt = datetime.strptime(order.orderTime, '%Y-%m-%d %H:%M:%S').date()
			dt2 = datetime.strptime('2017-8-16 0:0:0', '%Y-%m-%d %H:%M:%S').date()
			dt3 = datetime.now().date()
			if dt >= dt3:
				self.gateway.onOrder(order)
			# else:
			# 	self.gateway.onOrder(order)
			# 	print dt.date(), datetime.now().date()



		# self.orderCondition3.acquire()
		# self.orderCondition3.notify()
		# self.orderCondition3.release()




	# ----------------------------------------------------------------------
	def onEvent(self, data):
		pass

	# ----------------------------------------------------------------------
	def writeLog(self, logContent):
		"""发出日志"""
		log = VtLogData()
		log.gatewayName = self.gatewayName
		log.logContent = logContent
		self.gateway.onLog(log)

	def sendOrder_(self, orderReq):
		market_id = spotSymbolMap[orderReq.symbol]
		side = directionMap.get(orderReq.direction, '')
		price = orderReq.price
		size = orderReq.volume
		self.sendOrder(market_id, side, price, size)
		print market_id, side, price, size
		vtOrderID = '.'.join([self.gatewayName, self.lastOrderID])
		self.lastOrderID = ''
		return vtOrderID



# ----------------------------------------------------------------------
def getTime(t):
	"""把ZHCOIN返回的时间格式转化为简单的时间字符串"""
	return  datetime.datetime.fromtimestamp(t/1e3).strftime("%H:%M:%S")

def getDate(t):
	"""把ZHCOIN返回的时间格式转化为简单的日期字符串"""
	return  datetime.datetime.fromtimestamp(t/1e3).strftime("%Y-%m-%d")

def generateDateTime(s):
	"""生成时间"""
	dt = datetime.datetime.fromtimestamp(float(s) / 1e3)
	time = dt.strftime("%H:%M:%S.%f")
	date = dt.strftime("%Y%m%d")
	return date, time


if __name__ == '__main__':
	import sys
	# from PyQt4.QtCore import QCoreApplication
	import pandas as pd
	import random
	from datetime import datetime, timedelta

	# app = QCoreApplication(sys.argv)


	ee = EventEngine2()
	ee.start()


	def print_data(event):
		data = event.dict_['data']
		print event.type_,
		if event.type_ == 'eOrder.':
			print data.status, data.price, data.totalVolume, data.orderTime, data.orderID
		elif event.type_ == 'eTick.':
			print data.symbol, data.lastPrice, 'asks:', data.asks, 'bids:',data.bids, 'last_price:'
		elif event.type_ == 'eAccount.':
			print 'currency:',data.currency, 'balance:', data.balance,'available:', data.available
		elif event.type_ == 'eError.' or event.type_=='eContract.':
			print json.dumps(data.__dict__, encoding="UTF-8")


	# ee.register(EVENT_TICK, print_data)
	ee.register(EVENT_CONTRACT, print_data)
	ee.register(EVENT_CANDLE, print_data)
	ee.register(EVENT_TRADE, print_data)
	ee.register(EVENT_ACCOUNT, print_data)
	ee.register(EVENT_ERROR, print_data)

	zhcoin_gateway = ZhcoinGateway(ee, 'ZHCOIN')

	req = VtSubscribeReq()
	req.symbol = SYMBOL_BTC_CNY
	zhcoin_gateway.subscribe(req)
	req.symbol = SYMBOL_ETH_CNY
	# zhcoin_gateway.subscribe(req)

	zhcoin_gateway.setQryEnabled(False)
	zhcoin_gateway.connect()


	order_ids = []
	buyVolume = []
	sellVolume = []
	def onOrder(event):
		print_data(event)
		order = event.dict_['data']
		if order.status==STATUS_PARTTRADED or order.status==STATUS_PENDING:
			order_ids.append(order.orderID)
			if order.direction == 'B':
				buyVolume.append(order.totalVolume)
			else:
				sellVolume.append(order.totalVolume)

	ee.register(EVENT_ORDER, onOrder)

	while 1:
		sleep(1)
		input = raw_input('1:查账户 2.查委托 3.查成交 4.买 5.卖 6.撤 7.全撤 8.反向吃单  \n')
		if input == '1':
			zhcoin_gateway.qryAccount()
		elif input == '2':
			zhcoin_gateway.qryOrder()
		elif input == '3':
			zhcoin_gateway.qryTrade()
		elif input == '4':
			price = raw_input('price:')
			volume = raw_input('volume:')
			zhcoin_gateway.api.sendOrder('btc-cny','buy',price, volume)
		elif input=='5':
			price = raw_input('price:')
			volume = raw_input('volume:')
			zhcoin_gateway.api.sendOrder('btc-cny', 'sell', price, volume)
		elif input=='6':
			order_id = raw_input('order_id:')
			zhcoin_gateway.api.cancelOrder('btc-cny', order_id)
		elif input=='7':
			for order_id in order_ids:
				zhcoin_gateway.api.cancelOrder('btc-cny', order_id)
				# sleep(1)
			order_ids = []
		elif input == '8':
			zhcoin_gateway.api.sendOrder('btc-cny', 'sell', 28000, 13)
			zhcoin_gateway.api.sendOrder('btc-cny', 'buy', 30000, 20)
			buyVolume = []
			sellVolume = []







