# encoding: UTF-8

'''
vn.okcoin的gateway接入

注意：
1. 前仅支持USD和CNY的现货交易，USD的期货合约交易暂不支持
'''

import os
import json
from datetime import datetime
from copy import copy
from threading import Condition
from Queue import Queue
from threading import Thread

import sys
sys.path.append('..')
from api import vnokcoin
from common.vnlog import *
from vtGateway import *

# 价格类型映射
priceTypeMap = {}
priceTypeMap['buy'] = (DIRECTION_LONG, PRICETYPE_LIMITPRICE)
priceTypeMap['buy_market'] = (DIRECTION_LONG, PRICETYPE_MARKETPRICE)
priceTypeMap['sell'] = (DIRECTION_SHORT, PRICETYPE_LIMITPRICE)
priceTypeMap['sell_market'] = (DIRECTION_SHORT, PRICETYPE_MARKETPRICE)
priceTypeMapReverse = {v: k for k, v in priceTypeMap.items()}

# 方向类型映射
directionMap = {}
directionMapReverse = {v: k for k, v in directionMap.items()}

# 委托状态印射
statusMap = {}
statusMap[-1] = STATUS_CANCELLED
statusMap[0] = STATUS_NOTTRADED
statusMap[1] = STATUS_PARTTRADED
statusMap[2] = STATUS_ALLTRADED
statusMap[4] = STATUS_UNKNOWN

############################################
## 交易合约代码
############################################

# USD
BTC_USD_SPOT = 'BTC_USD_SPOT'
BTC_USD_THISWEEK = 'BTC_USD_THISWEEK'
BTC_USD_NEXTWEEK = 'BTC_USD_NEXTWEEK'
BTC_USD_QUARTER = 'BTC_USD_QUARTER'

LTC_USD_SPOT = 'LTC_USD_SPOT'
LTC_USD_THISWEEK = 'LTC_USD_THISWEEK'
LTC_USD_NEXTWEEK = 'LTC_USD_NEXTWEEK'
LTC_USD_QUARTER = 'LTC_USD_QUARTER'

ETH_USD_SPOT = 'ETH_USD_SPOT'
ETH_USD_THISWEEK = 'ETH_USD_THISWEEK'
ETH_USD_NEXTWEEK = 'ETH_USD_NEXTWEEK'
ETH_USD_QUARTER = 'ETH_USD_QUARTER'

# CNY
# BTC_CNY_SPOT = 'BTC_CNY_SPOT'
# LTC_CNY_SPOT = 'LTC_CNY_SPOT'
# ETH_CNY_SPOT = 'ETH_CNY_SPOT'

# 印射字典
spotSymbolMap = {}
# spotSymbolMap['ltc_usd'] = LTC_USD_SPOT
# spotSymbolMap['btc_usd'] = BTC_USD_SPOT
# spotSymbolMap['eth_usd'] = ETH_USD_SPOT
spotSymbolMap[SYMBOL_BTC_CNY] = 'btc'
spotSymbolMap[SYMBOL_LTC_CNY] = 'ltc'
spotSymbolMap[SYMBOL_ETH_CNY] = 'eth'
spotSymbolMapReverse = {v: k for k, v in spotSymbolMap.items()}

############################################
## Channel和Symbol的印射
############################################
channelSymbolMap = {}

# USD
channelSymbolMap['ok_sub_spotusd_btc_ticker'] = BTC_USD_SPOT
channelSymbolMap['ok_sub_spotusd_ltc_ticker'] = LTC_USD_SPOT
channelSymbolMap['ok_sub_spotusd_eth_ticker'] = ETH_USD_SPOT

channelSymbolMap['ok_sub_spotusd_btc_depth_20'] = BTC_USD_SPOT
channelSymbolMap['ok_sub_spotusd_ltc_depth_20'] = LTC_USD_SPOT
channelSymbolMap['ok_sub_spotusd_eth_depth_20'] = ETH_USD_SPOT

# CNY
channelSymbolMap['ok_sub_spotcny_btc_ticker'] = SYMBOL_BTC_CNY
channelSymbolMap['ok_sub_spotcny_ltc_ticker'] = SYMBOL_LTC_CNY
channelSymbolMap['ok_sub_spotcny_eth_ticker'] = SYMBOL_ETH_CNY

channelSymbolMap['ok_sub_spotcny_btc_depth_20'] = SYMBOL_BTC_CNY
channelSymbolMap['ok_sub_spotcny_ltc_depth_20'] = SYMBOL_LTC_CNY
channelSymbolMap['ok_sub_spotcny_eth_depth_20'] = SYMBOL_ETH_CNY



########################################################################
class OkcoinGateway(VtGateway):
	"""OkCoin接口"""

	# ----------------------------------------------------------------------
	def __init__(self, eventEngine, gatewayName='OKCOIN'):
		"""Constructor"""
		super(OkcoinGateway, self).__init__(eventEngine, gatewayName)

		self.api = Api(self)

		self.leverage = 0
		self.connected = False
		self.qryEnabled = False

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
			host = str(setting['host'])
			apiKey = str(setting['apiKey'])
			secretKey = str(setting['secretKey'])
			trace = setting['trace']
			leverage = setting['leverage']
		except KeyError:
			log = VtLogData()
			log.gatewayName = self.gatewayName
			log.logContent = u'连接配置缺少字段，请检查'
			self.onLog(log)
			return

			# 初始化接口
		self.leverage = leverage

		if host == 'CNY':
			host = vnokcoin.OKCOIN_CNY
		else:
			host = vnokcoin.OKCOIN_USD

		self.api.active = True
		self.api.connect(host, apiKey, secretKey, trace)


		self.api.writeLog(u'接口初始化成功')

		# 启动查询
		self.initQuery()

	# ----------------------------------------------------------------------
	def subscribe(self, subscribeReq):
		self.api._subscribe(subscribeReq.symbol)

	# ----------------------------------------------------------------------
	def sendOrder(self, orderReq):
		"""发单"""
		return self.api.spotSendOrder(orderReq)

	# ----------------------------------------------------------------------
	def cancelOrder(self, cancelOrderReq):
		"""撤单"""
		self.api.spotCancel(cancelOrderReq)

	# ----------------------------------------------------------------------
	def qryAccount(self):
		"""查询账户资金"""
		self.api.spotUserInfo()

	# ----------------------------------------------------------------------
	def qryPosition(self):
		"""查询持仓"""
		pass

	# ----------------------------------------------------------------------
	def close(self):
		"""关闭"""
		self.api.active = False
		self.api.close()

	# ----------------------------------------------------------------------
	def initQuery(self):
		"""初始化连续查询"""
		if self.qryEnabled:
			# 需要循环的查询函数列表
			self.qryFunctionList = [self.qryAccount]

			self.qryCount = 0  # 查询触发倒计时
			self.qryTrigger = 1  # 查询触发点
			self.qryNextFunction = 0  # 上次运行的查询函数索引

			self.startQuery()

	# ----------------------------------------------------------------------
	def query(self, event):
		"""注册到事件处理引擎上的查询函数"""
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
class Api(vnokcoin.OkCoinApi):
	"""OkCoin的API实现"""

	# ----------------------------------------------------------------------
	def __init__(self, gateway):
		"""Constructor"""
		super(Api, self).__init__()

		self.gateway = gateway  # gateway对象
		self.gatewayName = gateway.gatewayName  # gateway对象名称

		self.active = False  # 若为True则会在断线后自动重连

		self.cbDict = {}
		self.tickDict = {}
		self.orderDict = {}

		self.localNo = 0  # 本地委托号
		self.localNoQueue = Queue()  # 未收到系统委托号的本地委托号队列
		self.localNoDict = {}  # key为本地委托号，value为系统委托号
		self.orderIdDict = {}  # key为系统委托号，value为本地委托号
		self.cancelDict = {}  # key为本地委托号，value为撤单请求

		self.initCallback()

	def _subscribe(self, symbol):
		log = "okcoin_gateway _subscribe %s" % symbol
		self.writeLog(log)
		#从系统定义的合约名转为okcoin的合约名

		tick = VtTickData()
		tick.exchange = EXCHANGE_OKCOIN
		tick.symbol = symbol
		tick.vtSymbol = '.'.join([tick.symbol, tick.exchange])
		tick.gatewayName = self.gatewayName
		self.tickDict[symbol] = tick

	# ----------------------------------------------------------------------
	def onMessage(self, ws, evt):
		"""信息推送"""
		data = self.readData(evt)[0]
		channel = data['channel']
		# print '%s %s' % (channel, data)
		callback = self.cbDict[channel]
		callback(data)

	# ----------------------------------------------------------------------
	def onError(self, ws, evt):
		"""错误推送"""
		error = VtErrorData()
		error.gatewayName = self.gatewayName
		error.errorMsg = str(evt)
		self.gateway.onError(error)

	# ----------------------------------------------------------------------
	def onClose(self, ws):
		self.writeLog("okcoin_gateway断开")
		# 如果尚未连上，则忽略该次断开提示
		if not self.gateway.connected:
			return

		self.gateway.connected = False


		# 重新连接
		if self.active:

			def reconnect():
				while not self.gateway.connected:
					self.writeLog(u'等待10秒后重新连接')
					sleep(10)
					if not self.gateway.connected:
						self.reconnect()

			t = Thread(target=reconnect)
			t.start()

	# ----------------------------------------------------------------------
	def onOpen(self, ws):
		self.writeLog("okcoin_gateway连上")
		self.gateway.connected = True

		# 连接后查询账户和委托数据
		self.spotUserInfo()

		# self.spotOrderInfo(vnokcoin.TRADING_SYMBOL_BTC, '-1')
		# self.spotOrderInfo(vnokcoin.TRADING_SYMBOL_LTC, '-1')
		# self.spotOrderInfo(vnokcoin.TRADING_SYMBOL_ETH, '-1')

		# 连接后订阅现货的成交和账户数据
		# self.subscribeSpotTrades()
		# self.subscribeSpotUserInfo()


		for symbol in self.tickDict.keys():
			self.subscribeSpotTicker(spotSymbolMap[symbol])
			self.subscribeSpotDepth(spotSymbolMap[symbol], vnokcoin.DEPTH_20)

		# 如果连接的是USD网站则订阅期货相关回报数据
		if self.currency == vnokcoin.CURRENCY_USD:
			self.subscribeFutureTrades()
			self.subscribeFutureUserInfo()
			self.subscribeFuturePositions()

		# 返回合约信息
		if self.currency == vnokcoin.CURRENCY_CNY:
			l = self.generateCnyContract()
		else:
			l = self.generateUsdContract()

		for contract in l:
			contract.gatewayName = self.gatewayName
			self.gateway.onContract(contract)

	# ----------------------------------------------------------------------
	def writeLog(self, content):
		"""快速记录日志"""
		log = VtLogData()
		log.gatewayName = self.gatewayName
		log.logContent = content
		self.gateway.onLog(log)

	# ----------------------------------------------------------------------
	def initCallback(self):
		"""初始化回调函数"""
		# USD_SPOT
		self.cbDict['ok_sub_spotusd_btc_ticker'] = self.onTicker
		self.cbDict['ok_sub_spotusd_ltc_ticker'] = self.onTicker
		self.cbDict['ok_sub_spotusd_eth_ticker'] = self.onTicker

		self.cbDict['ok_sub_spotusd_btc_depth_20'] = self.onDepth
		self.cbDict['ok_sub_spotusd_ltc_depth_20'] = self.onDepth
		self.cbDict['ok_sub_spotusd_eth_depth_20'] = self.onDepth

		self.cbDict['ok_spotusd_userinfo'] = self.onSpotUserInfo
		self.cbDict['ok_spotusd_orderinfo'] = self.onSpotOrderInfo

		self.cbDict['ok_sub_spotusd_userinfo'] = self.onSpotSubUserInfo
		self.cbDict['ok_sub_spotusd_trades'] = self.onSpotSubTrades

		self.cbDict['ok_spotusd_trade'] = self.onSpotTrade
		self.cbDict['ok_spotusd_cancel_order'] = self.onSpotCancelOrder

		# CNY_SPOT
		self.cbDict['ok_sub_spotcny_btc_ticker'] = self.onTicker
		self.cbDict['ok_sub_spotcny_ltc_ticker'] = self.onTicker
		self.cbDict['ok_sub_spotcny_eth_ticker'] = self.onTicker

		self.cbDict['ok_sub_spotcny_btc_depth_20'] = self.onDepth
		self.cbDict['ok_sub_spotcny_ltc_depth_20'] = self.onDepth
		self.cbDict['ok_sub_spotcny_eth_depth_20'] = self.onDepth

		self.cbDict['ok_spotcny_userinfo'] = self.onSpotUserInfo
		self.cbDict['ok_spotcny_orderinfo'] = self.onSpotOrderInfo

		self.cbDict['ok_sub_spotcny_userinfo'] = self.onSpotSubUserInfo
		self.cbDict['ok_sub_spotcny_trades'] = self.onSpotSubTrades

		self.cbDict['ok_spotcny_trade'] = self.onSpotTrade
		self.cbDict['ok_spotcny_cancel_order'] = self.onSpotCancelOrder

		self.cbDict['login'] = self.onPass
		self.cbDict['addChannel'] = self.onPass
		# USD_FUTURES

	# ----------------------------------------------------------------------
	def onTicker(self, data):
		if 'data' not in data:
			return

		channel = data['channel']
		symbol = channelSymbolMap[channel]

		tick = self.tickDict[symbol]

		rawData = data['data']
		tick.highPrice = float(rawData['high'])
		tick.lowPrice = float(rawData['low'])
		tick.lastPrice = float(rawData['last'])
		tick.volume = float(rawData['vol'])
		tick.date, tick.time = generateDateTime(rawData['timestamp'])
		# newtick = copy(tick)
		self.gateway.onTick(tick)

	# ----------------------------------------------------------------------
	def onPass(self, data):
		""""""
		pass

	# ----------------------------------------------------------------------
	def onDepth(self, data):
		if 'data' not in data:
			return

		channel = data['channel']
		symbol = channelSymbolMap[channel]

		tick = self.tickDict[symbol]
		rawData = data['data']

		tick.bids = []
		tick.asks = []
		for i in range(0,5):
			tick.bids.append(rawData['bids'][i])
			tick.asks.append(rawData['asks'][-i-1])
		tick.bidPrice1, tick.bidVolume1 = [float(i) for i in rawData['bids'][0]]
		tick.bidPrice2, tick.bidVolume2 = [float(i) for i in rawData['bids'][1]]
		tick.bidPrice3, tick.bidVolume3 = [float(i) for i in rawData['bids'][2]]
		tick.bidPrice4, tick.bidVolume4 = [float(i) for i in rawData['bids'][3]]
		tick.bidPrice5, tick.bidVolume5 = [float(i) for i in rawData['bids'][4]]

		tick.askPrice1, tick.askVolume1 = [float(i) for i in rawData['asks'][-1]]
		tick.askPrice2, tick.askVolume2 = [float(i) for i in rawData['asks'][-2]]
		tick.askPrice3, tick.askVolume3 = [float(i) for i in rawData['asks'][-3]]
		tick.askPrice4, tick.askVolume4 = [float(i) for i in rawData['asks'][-4]]
		tick.askPrice5, tick.askVolume5 = [float(i) for i in rawData['asks'][-5]]

		tick.date, tick.time = generateDateTime(rawData['timestamp'])
		self.gateway.onTick(tick)

	# ----------------------------------------------------------------------
	def onSpotUserInfo(self, data):
		"""现货账户资金推送"""
		rawData = data['data']
		info = rawData['info']
		funds = rawData['info']['funds']

		# 持仓信息
		for symbol in ['btc', 'ltc', self.currency]:
			if symbol in funds['free']:
				pos = VtPositionData()
				pos.gatewayName = self.gatewayName

				pos.exchange = EXCHANGE_OKCOIN
				pos.symbol = symbol
				pos.vtSymbol = '.'.join([pos.symbol, pos.exchange])
				# pos.vtSymbol = symbol
				pos.vtPositionName = symbol
				pos.direction = DIRECTION_NET

				pos.frozen = float(funds['freezed'][symbol])
				pos.position = pos.frozen + float(funds['free'][symbol])

				self.gateway.onPosition(pos)

		# 账户资金
		account = VtAccountData()
		account.gatewayName = self.gatewayName
		account.accountID = self.gatewayName
		account.vtAccountID = account.accountID
		account.balance = float(funds['asset']['net'])
		self.gateway.onAccount(account)

	# ----------------------------------------------------------------------
	def onSpotSubUserInfo(self, data):
		"""现货账户资金推送"""
		if 'data' not in data:
			return

		rawData = data['data']
		info = rawData['info']

		# 持仓信息
		for symbol in ['btc', 'ltc', self.currency]:
			if symbol in info['free']:
				pos = VtPositionData()
				pos.gatewayName = self.gatewayName

				pos.exchange = EXCHANGE_OKCOIN
				pos.symbol = symbol
				pos.vtSymbol = '.'.join([pos.symbol, pos.exchange])
				# pos.vtSymbol = symbol
				pos.vtPositionName = symbol
				pos.direction = DIRECTION_NET

				pos.frozen = float(info['freezed'][symbol])
				pos.position = pos.frozen + float(info['free'][symbol])

				self.gateway.onPosition(pos)

	# ----------------------------------------------------------------------
	def onSpotSubTrades(self, data):
		"""成交和委托推送"""
		if 'data' not in data:
			return
		rawData = data['data']
		# print 'onSpotSubTrades %s' % rawData
		# 本地和系统委托号
		orderId = str(rawData['orderId'])
		# orderId = rawData['orderId']
		localNo = self.orderIdDict[orderId]
		# 委托信息
		if orderId not in self.orderDict:
			order = VtOrderData()
			order.gatewayName = self.gatewayName
			order.exchange = EXCHANGE_OKCOIN

			order.symbol = spotSymbolMap[rawData['symbol']]
			order.vtSymbol = '.'.join([order.symbol, order.exchange])
			# order.vtSymbol = order.symbol

			order.orderID = localNo
			# order.vtOrderID = '.'.join([self.gatewayName, order.orderID])
			order.vtOrderID = '.'.join([order.orderID, self.gatewayName])

			order.price = float(rawData['tradeUnitPrice'])
			order.totalVolume = float(rawData['tradeAmount'])
			order.direction, priceType = priceTypeMap[rawData['tradeType']]

			self.orderDict[orderId] = order
		else:
			order = self.orderDict[orderId]

		order.tradedVolume = float(rawData['completedTradeAmount'])
		order.status = statusMap[rawData['status']]
		# print order.__dict__
		self.gateway.onOrder(copy(order))

		# 成交信息
		if 'sigTradeAmount' in rawData and float(rawData['sigTradeAmount']) > 0:
			trade = VtTradeData()
			trade.gatewayName = self.gatewayName
			trade.exchange = EXCHANGE_OKCOIN

			trade.symbol = spotSymbolMap[rawData['symbol']]
			trade.vtSymbol = '.'.join([order.symbol, order.exchange])
			# trade.vtSymbol = order.symbol

			trade.tradeID = str(rawData['id'])
			trade.vtTradeID = '.'.join([self.gatewayName, trade.tradeID])

			trade.orderID = localNo
			# trade.vtOrderID = '.'.join([self.gatewayName, trade.orderID])
			trade.vtOrderID = '.'.join([order.orderID, self.gatewayName])

			trade.price = float(rawData['sigTradePrice'])
			trade.volume = float(rawData['sigTradeAmount'])

			trade.direction, priceType = priceTypeMap[rawData['tradeType']]

			trade.tradeTime = datetime.now().strftime('%H:%M:%S')

			self.gateway.onTrade(trade)

	# ----------------------------------------------------------------------
	def onSpotOrderInfo(self, data):
		"""委托信息查询回调"""
		rawData = data['data']

		for d in rawData['orders']:
			self.localNo += 1
			localNo = str(self.localNo)
			orderId = str(d['order_id'])

			self.localNoDict[localNo] = orderId
			self.orderIdDict[orderId] = localNo

			if orderId not in self.orderDict:
				order = VtOrderData()
				order.gatewayName = self.gatewayName
				order.exchange = EXCHANGE_OKCOIN

				order.symbol = spotSymbolMap[d['symbol']]
				order.vtSymbol = '.'.join([order.symbol, order.exchange])
				# order.vtSymbol = order.symbol

				order.orderID = localNo
				# order.vtOrderID = '.'.join([self.gatewayName, order.orderID])
				order.vtOrderID = '.'.join([order.orderID, self.gatewayName])

				order.price = d['price']
				order.totalVolume = d['amount']
				order.direction, priceType = priceTypeMap[d['type']]

				self.orderDict[orderId] = order
			else:
				order = self.orderDict[orderId]

			order.tradedVolume = d['deal_amount']
			order.status = statusMap[d['status']]

			self.gateway.onOrder(copy(order))

	# ----------------------------------------------------------------------
	def generateSpecificContract(self, contract, symbol):
		"""生成合约"""
		new = copy(contract)
		new.symbol = symbol
		new.vtSymbol = '.'.join([symbol, EXCHANGE_OKCOIN])
		new.name = symbol
		return new

	# ----------------------------------------------------------------------
	def generateCnyContract(self):
		"""生成CNY合约信息"""
		contractList = []

		contract = VtContractData()
		contract.exchange = EXCHANGE_OKCOIN
		contract.productClass = PRODUCT_SPOT
		contract.size = 1
		contract.priceTick = 0.01

		contractList.append(self.generateSpecificContract(contract, SYMBOL_BTC_CNY))
		contractList.append(self.generateSpecificContract(contract, SYMBOL_LTC_CNY))
		contractList.append(self.generateSpecificContract(contract, SYMBOL_ETH_CNY))

		return contractList

	# ----------------------------------------------------------------------
	def generateUsdContract(self):
		"""生成USD合约信息"""
		contractList = []

		# 现货
		contract = VtContractData()
		contract.exchange = EXCHANGE_OKCOIN
		contract.productClass = PRODUCT_SPOT
		contract.size = 1
		contract.priceTick = 0.01

		contractList.append(self.generateSpecificContract(contract, BTC_USD_SPOT))
		contractList.append(self.generateSpecificContract(contract, LTC_USD_SPOT))
		contractList.append(self.generateSpecificContract(contract, ETH_USD_SPOT))

		# 期货
		contract.productClass = PRODUCT_FUTURES

		contractList.append(self.generateSpecificContract(contract, BTC_USD_THISWEEK))
		contractList.append(self.generateSpecificContract(contract, BTC_USD_NEXTWEEK))
		contractList.append(self.generateSpecificContract(contract, BTC_USD_QUARTER))
		contractList.append(self.generateSpecificContract(contract, LTC_USD_THISWEEK))
		contractList.append(self.generateSpecificContract(contract, LTC_USD_NEXTWEEK))
		contractList.append(self.generateSpecificContract(contract, LTC_USD_QUARTER))
		contractList.append(self.generateSpecificContract(contract, ETH_USD_THISWEEK))
		contractList.append(self.generateSpecificContract(contract, ETH_USD_NEXTWEEK))
		contractList.append(self.generateSpecificContract(contract, ETH_USD_QUARTER))

		return contractList

	# ----------------------------------------------------------------------
	def onSpotTrade(self, data):
		"""委托回报"""
		rawData = data['data']
		orderId = str(rawData['order_id'])

		# 尽管websocket接口的委托号返回是异步的，但经过测试是
		# 符合先发先回的规律，因此这里通过queue获取之前发送的
		# 本地委托号，并把它和推送的系统委托号进行映射
		localNo = self.localNoQueue.get_nowait()

		self.localNoDict[localNo] = orderId
		self.orderIdDict[orderId] = localNo

		# 检查是否有系统委托号返回前就发出的撤单请求，若有则进
		# 行撤单操作
		if localNo in self.cancelDict:
			req = self.cancelDict[localNo]
			self.spotCancel(req)
			del self.cancelDict[localNo]

	# ----------------------------------------------------------------------
	def onSpotCancelOrder(self, data):
		"""撤单回报"""
		pass

	# ----------------------------------------------------------------------
	def spotSendOrder(self, req):
		"""发单"""
		symbol = spotSymbolMapReverse[req.symbol][:4]
		type_ = priceTypeMapReverse[(req.direction, req.priceType)]
		self.spotTrade(symbol, type_, str(req.price), str(req.volume))

		# 本地委托号加1，并将对应字符串保存到队列中，返回基于本地委托号的vtOrderID
		self.localNo += 1
		self.localNoQueue.put(str(self.localNo))
		# vtOrderID = '.'.join([self.gatewayName, str(self.localNo)])
		vtOrderID = '.'.join([str(self.localNo), self.gatewayName])
		return vtOrderID

	# ----------------------------------------------------------------------
	def spotCancel(self, req):
		"""撤单"""
		symbol = spotSymbolMapReverse[req.symbol][:4]
		localNo = req.orderID

		if localNo in self.localNoDict:
			orderID = self.localNoDict[localNo]
			self.spotCancelOrder(symbol, orderID)
		else:
			# 如果在系统委托号返回前客户就发送了撤单请求，则保存
			# 在cancelDict字典中，等待返回后执行撤单任务
			self.cancelDict[localNo] = req


# ----------------------------------------------------------------------
def generateDateTime(s):
	"""生成时间"""
	dt = datetime.fromtimestamp(float(s) / 1e3)
	time = dt.strftime("%H:%M:%S.%f")
	date = dt.strftime("%Y%m%d")
	return date, time



if __name__ == '__main__':
	import sys
	# from PyQt4.QtCore import QCoreApplication

	logger = vnLog('test_okcoin_gateway.log')

	# app = QCoreApplication(sys.argv)

	ee = EventEngine2()
	ee.start()

	def print_data(event):
		data = event.dict_['data']
		log = '%s %s' %(event.type_, data.vtSymbol)
		logger.write(log)

	def print_log(event):
		data = event.dict_['data']
		log = '%s %s' % (event.type_, data.logContent)
		logger.write(log)

	ee.register(EVENT_TICK, print_data)
	ee.register(EVENT_LOG, print_log)

	okcoin_gateway = OkcoinGateway(ee, 'OKCOIN')

	req = VtSubscribeReq()
	req.symbol = SYMBOL_BTC_CNY
	okcoin_gateway.subscribe(req)
	req.symbol = SYMBOL_LTC_CNY
	okcoin_gateway.subscribe(req)
	req.symbol = SYMBOL_ETH_CNY
	okcoin_gateway.subscribe(req)


	okcoin_gateway.connect()

	while True:
		sleep(100)

	# app.exec_()

