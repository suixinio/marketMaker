# encoding: UTF-8

from __future__ import print_function
from __future__ import print_function
import json
import shelve
from collections import OrderedDict

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

import sys
sys.path.append('.')
sys.path.append('..')
from common.vtFunction import *
from eventEngine import *
from vtGateway import *


from MMEngine import MMEngine
from rmEngine import RmEngine

########################################################################
class MainEngine(object):
    """主引擎"""

    # ----------------------------------------------------------------------
    def __init__(self):

        def print_log_err(event):
            class_data = event.dict_['data'].__dict__
            print(str(event.type_) + ": ")
            print(json.dumps(class_data, encoding="UTF-8"))

        # 创建事件引擎
        self.eventEngine = EventEngine2()
        self.eventEngine.register(EVENT_LOG, print_log_err)
        self.eventEngine.register(EVENT_CTA_LOG, print_log_err)
        self.eventEngine.register(EVENT_ERROR, print_log_err)
        self.eventEngine.start()

        # 创建数据引擎
        self.dataEngine = DataEngine(self.eventEngine)

        # MongoDB数据库相关
        self.dbClient = None  # MongoDB客户端对象

        # 调用一个个初始化函数
        self.initGateway()

        # 扩展模块
        self.mmEngine = MMEngine(self, self.eventEngine)

        self.rmEngine = RmEngine(self, self.eventEngine)
        self.rmEngine.switchEngineStatus()

    # ----------------------------------------------------------------------
    def initGateway(self):
        """初始化接口对象"""
        # 用来保存接口对象的字典
        self.gatewayDict = OrderedDict()

        try:
            from gateway.okcoinGateway import OkcoinGateway
            self._addGateway(OkcoinGateway, EXCHANGE_OKCOIN, True)
        except Exception as e:
            print (str(e))

        try:
            from gateway.zhcoinGateway import ZhcoinGateway
            self._addGateway(ZhcoinGateway, EXCHANGE_ZHCOIN, True)
        except Exception as e:
            print (str(e))
        # try:
        #     from bithumbGateway.bithumbGateway import BithumbGateway
        #     self._addGateway(BithumbGateway, 'BITHUMB')
        #     self.gatewayDict['BITHUMB'].setQryEnabled(True)
        # except Exception, e:
        #     print e
        #
        # try:
        #     from coinoneGateway.coinoneGateway import CoinoneGateway
        #     self._addGateway(CoinoneGateway, 'COINONE')
        #     self.gatewayDict['COINONE'].setQryEnabled(True)
        # except Exception, e:
        #     print e

        # try:
        #     from btccGateway.btccGateway import BtccGateway
        #     self._addGateway(BtccGateway, 'BTCC')
        #     self.gatewayDict['BTCC'].setQryEnabled(True)
        # except Exception as e:
        #     print e

        

    # param: gateway 网关类型 ；gatewayName 接口名
    def _addGateway(self, gateway, gatewayName, needQry):
        self.gatewayDict[gatewayName] = gateway(self.eventEngine, gatewayName)
        self.gatewayDict[gatewayName].setQryEnabled(needQry)

    # ----------------------------------------------------------------------
    def connect(self, gatewayName, params=None):
        """连接特定名称的接口"""
        if gatewayName in self.gatewayDict:
            gateway = self.gatewayDict[gatewayName]
            gateway.connect()
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    def start(self):
        self.mmEngine.initStrategy('Market Balance')  # to run the on.init function and set inited = True
        self.mmEngine.startStrategy('Market Balance')  # to set trading=True, in order to start trading

    # ----------------------------------------------------------------------
    def subscribe(self, subscribeReq, gatewayName):
        """订阅特定接口的行情"""
        if gatewayName in self.gatewayDict:
            gateway = self.gatewayDict[gatewayName]
            gateway.subscribe(subscribeReq)
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    # ----------------------------------------------------------------------
    def tickHaltSwitch(self, gatewayName):
        for gate in gatewayName:
            if gate in self.gatewayDict:
                gateway = self.gatewayDict[gate]
                gateway.api.tickHaltSwitch()
            else:
                self.writeLog(u'接口不存在：%s' % gate)

    # ----------------------------------------------------------------------
    def sendOrder(self, orderReq, gatewayName):
        """对特定接口发单"""
        # 如果风控检查失败则不发单
        if not self.rmEngine.checkRisk(orderReq):
            return ''

        if gatewayName in self.gatewayDict:
            # print gatewayName
            gateway = self.gatewayDict[gatewayName]
            vtOrderID2 = gateway.sendOrder(orderReq)
            # print 'vtOrderID2 %s' % vtOrderID2
            return vtOrderID2
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    # ----------------------------------------------------------------------
    def cancelOrder(self, cancelOrderLst, gatewayName):
        """对特定接口撤单"""
        if gatewayName in self.gatewayDict:
            gateway = self.gatewayDict[gatewayName]
            gateway.cancelOrder(cancelOrderLst)
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    # ----------------------------------------------------------------------
    def cancelAll(self, gatewayName):
        """对特定接口一键撤销所有委托"""
        l = self.getAllWorkingOrders()
        for order in l:
            req = VtCancelOrderReq()
            req.symbol = order.symbol
            req.exchange = order.exchange
            req.frontID = order.frontID
            req.sessionID = order.sessionID
            req.orderID = order.orderID
            if order.gatewayName == gatewayName:  # cancel the orders in the corresponding gateway
                self.cancelOrder(req, order.gatewayName)

    # ----------------------------------------------------------------------
    def qryAccont(self, gatewayName):
        """查询特定接口的账户"""
        if gatewayName in self.gatewayDict:
            gateway = self.gatewayDict[gatewayName]
            gateway.qryAccount()
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    # ----------------------------------------------------------------------
    def qryPosition(self, gatewayName):
        """查询特定接口的持仓"""
        if gatewayName in self.gatewayDict:
            gateway = self.gatewayDict[gatewayName]
            gateway.qryPosition()
        else:
            self.writeLog(u'接口不存在：%s' % gatewayName)

    # ----------------------------------------------------------------------
    def exit(self):
        """退出程序前调用，保证正常退出"""
        # 安全关闭所有接口
        for gateway in self.gatewayDict.values():
            gateway.close()

        # 停止事件引擎
        self.eventEngine.stop()


        # 保存数据引擎里的合约数据到硬盘
        self.dataEngine.saveContracts()

    # ----------------------------------------------------------------------
    def writeLog(self, content):
        """快速发出日志事件"""
        log = VtLogData()
        log.logContent = content
        event = Event(type_=EVENT_LOG)
        event.dict_['data'] = log
        self.eventEngine.put(event)

    # ----------------------------------------------------------------------
    def getContract(self, vtSymbol):
        """查询合约"""
        return self.dataEngine.getContract(vtSymbol)

    # ----------------------------------------------------------------------
    def getAllContracts(self):
        """查询所有合约（返回列表）"""
        return self.dataEngine.getAllContracts()

    # ----------------------------------------------------------------------
    def getOrder(self, vtOrderID):
        """查询委托"""
        return self.dataEngine.getOrder(vtOrderID)

    # ----------------------------------------------------------------------
    def findOrderID(self, targetPrice, orderType):
        """find Order ID"""
        return self.dataEngine.findOrderID(targetPrice, orderType)

    # ----------------------------------------------------------------------
    def getAllWorkingOrders(self):
        """查询所有的活跃的委托（返回列表）"""
        return self.dataEngine.getAllWorkingOrders()


########################################################################
# 管理合约、委托、持仓等数据
class DataEngine(object):
    """数据引擎"""
    contractFileName = 'ContractData.vt'

    # ----------------------------------------------------------------------
    def __init__(self, eventEngine):
        """Constructor"""
        self.eventEngine = eventEngine

        # 保存合约详细信息的字典
        self.contractDict = {}

        # 保存委托数据的字典
        self.orderDict = {}

        # 保存活动委托数据的字典（即可撤销）
        self.workingOrderDict = {}

        # 读取保存在硬盘的合约数据
        self.loadContracts()

        # 注册事件监听
        self.registerEvent()

        self.posDict = {}

    # ----------------------------------------------------------------------
    def updatePosition(self, event):
        pos = event.dict_['data']

        print (pos.__dict__)

    # ----------------------------------------------------------------------
    def updateContract(self, event):
        """更新合约数据"""
        contract = event.dict_['data']
        self.contractDict[contract.vtSymbol] = contract
        self.contractDict[contract.symbol] = contract  # 使用常规代码（不包括交易所）可能导致重复

    # ----------------------------------------------------------------------
    def getContract(self, vtSymbol):
        """查询合约对象"""
        try:
            return self.contractDict[vtSymbol]
        except KeyError:
            return None

    # ----------------------------------------------------------------------
    def getAllContracts(self):
        """查询所有合约对象（返回列表）"""
        return self.contractDict.values()

    # ----------------------------------------------------------------------
    def saveContracts(self):
        """保存所有合约对象到硬盘"""
        pass
        # f = shelve.open(self.contractFileName)
        # f['data'] = self.contractDict
        # f.close()

    # ----------------------------------------------------------------------
    def loadContracts(self):
        """从硬盘读取合约对象"""
        pass
        # f = shelve.open(self.contractFileName)
        # if 'data' in f:
        #     d = f['data']
        #     for key, value in d.items():
        #         self.contractDict[key] = value
        # f.close()

    # ----------------------------------------------------------------------
    def updateOrder(self, event):
        order = event.dict_['data']
        self.orderDict[order.vtOrderID] = order

        if order.status in [STATUS_PARTTRADED, STATUS_PENDING]:
            if not self.workingOrderDict.has_key(order.vtOrderID):
                self.workingOrderDict[order.vtOrderID] = order
        else:
            if order.vtOrderID in self.workingOrderDict:
                del self.workingOrderDict[order.vtOrderID]

    # ----------------------------------------------------------------------
    def getOrder(self, vtOrderID):
        """查询委托"""
        try:
            return self.orderDict[vtOrderID]
        except KeyError:
            return None

    # ----------------------------------------------------------------------
    def findOrderID(self, targetPrice, orderType):
        """查询委托"""
        try:
            lst = []
            for i in self.orderDict:
                if orderType == DIRECTION_LONG:
                    if self.orderDict[i].__getattribute__('direction') == DIRECTION_LONG:
                        if  self.orderDict[i].__getattribute__('price') >= targetPrice:
                            lst.append(i)
                elif orderType == DIRECTION_SHORT:
                    if self.orderDict[i].__getattribute__('direction') == DIRECTION_SHORT:
                        if self.orderDict[i].__getattribute__('price') <= targetPrice:
                            lst.append(i)
            return lst
        except KeyError:
            return None

    # ----------------------------------------------------------------------
    def getAllWorkingOrders(self):
        """查询所有活动委托（返回列表）"""
        return self.workingOrderDict.values()
        # return self.workingOrderDict.keys()

    # ----------------------------------------------------------------------
    def registerEvent(self):
        """注册事件监听"""
        self.eventEngine.register(EVENT_CONTRACT, self.updateContract)
        self.eventEngine.register(EVENT_ORDER, self.updateOrder)
        # self.eventEngine.register(EVENT_POSITION, self.updatePosition)

if __name__ == '__main__':
    """测试"""
    # from PyQt4 import QtCore
    import sys

    # app = QtCore.QCoreApplication(sys.argv)

    me = MainEngine()

    subscribeReq = VtSubscribeReq()
    subscribeReq.symbol = SYMBOL_BTC_CNY

    me.subscribe(subscribeReq, EXCHANGE_OKCOIN)
    me.connect(EXCHANGE_OKCOIN)
    me.subscribe(subscribeReq, EXCHANGE_ZHCOIN)
    me.connect(EXCHANGE_ZHCOIN)

    me.start()

    while True:
        sleep(100)
    # sys.exit(app.exec_())

