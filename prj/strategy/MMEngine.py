# encoding: UTF-8

'''
本文件中实现了CTA策略引擎，针对CTA类型的策略，抽象简化了部分底层接口的功能。

关于平今和平昨规则：
1. 普通的平仓OFFSET_CLOSET等于平昨OFFSET_CLOSEYESTERDAY
2. 只有上期所的品种需要考虑平今和平昨的区别
3. 当上期所的期货有今仓时，调用Sell和Cover会使用OFFSET_CLOSETODAY，否则
   会使用OFFSET_CLOSE
4. 以上设计意味着如果Sell和Cover的数量超过今日持仓量时，会导致出错（即用户
   希望通过一个指令同时平今和平昨）
5. 采用以上设计的原因是考虑到vn.trader的用户主要是对TB、MC和金字塔类的平台
   感到功能不足的用户（即希望更高频的交易），交易策略不应该出现4中所述的情况
6. 对于想要实现4中所述情况的用户，需要实现一个策略信号引擎和交易委托引擎分开
   的定制化统结构（没错，得自己写）
'''

import json
import os
import traceback
from collections import OrderedDict
from datetime import datetime, timedelta
from time import sleep

from MMBase import *
from MMSetting import STRATEGY_CLASS
import sys
sys.path.append('.')
sys.path.append('..')
from vtFunction import *
from eventEngine import *
from vtConstant import *
from vtGateway import VtSubscribeReq, VtOrderReq, VtCancelOrderReq, VtLogData



########################################################################
class MMEngine(object):
    """CTA策略引擎"""
    settingFileName = 'MM_setting.json'
    settingFileName = os.path.join(ROOT_PATH, 'cfg', settingFileName)

    #----------------------------------------------------------------------
    def __init__(self, mainEngine, eventEngine):
        """Constructor"""
        self.mainEngine = mainEngine
        self.eventEngine = eventEngine
        
        # 当前日期
        self.today = todayDate()
        
        # 保存策略实例的字典
        # key为策略名称，value为策略实例，注意策略名称不允许重复
        self.strategyDict = {}
        
        # 保存vtSymbol和策略实例映射的字典（用于推送tick数据）
        # 由于可能多个strategy交易同一个vtSymbol，因此key为vtSymbol
        # value为包含所有相关strategy对象的list
        self.tickStrategyDict = {}
        
        # 保存vtOrderID和strategy对象映射的字典（用于推送order和trade数据）
        # key为vtOrderID，value为strategy对象
        self.orderStrategyDict = {}     
        
        # 本地停止单编号计数
        self.stopOrderCount = 0
        # stopOrderID = STOPORDERPREFIX + str(stopOrderCount)
        
        # 本地停止单字典
        # key为stopOrderID，value为stopOrder对象
        self.stopOrderDict = {}             # 停止单撤销后不会从本字典中删除
        self.workingStopOrderDict = {}      # 停止单撤销后会从本字典中删除
        
        # 持仓缓存字典
        # key为vtSymbol，value为PositionBuffer对象
        self.posBufferDict = {}

        # 成交号集合，用来过滤已经收到过的成交推送
        self.tradeSet = set()

        # 引擎类型为实盘
        self.engineType = ENGINETYPE_TRADING
        
        # 注册事件监听
        self.registerEvent()

        self.loadSetting()
 
    #----------------------------------------------------------------------
    def sendOrder(self, vtSymbol, orderType, price, volume, strategy):
        """发单"""
        # self.mainEngine.tickHaltSwitch(['OKCOIN', 'ZHCOIN']) # halt receiving the tick info from OKCOIN and ZHCOIN
        # print 'aaa'
        contract = self.mainEngine.getContract(vtSymbol)
        # print 'contract %s' % contract.__dict__
        req = VtOrderReq()
        req.symbol = contract.symbol
        req.exchange = contract.exchange
        req.price = price
        req.volume = volume
        
        req.productClass = strategy.productClass
        req.currency = strategy.currency        
        
        # 设计为CTA引擎发出的委托只允许使用限价单
        req.priceType = PRICETYPE_LIMITPRICE    
        
        # CTA委托类型映射
        if orderType == CTAORDER_BUY:
            req.direction = DIRECTION_LONG
            req.offset = OFFSET_OPEN
            
        elif orderType == CTAORDER_SELL:
            req.direction = DIRECTION_SHORT
            
            # 只有上期所才要考虑平今平昨
            if contract.exchange != EXCHANGE_SHFE:
                req.offset = OFFSET_CLOSE
            else:
                # 获取持仓缓存数据
                posBuffer = self.posBufferDict.get(vtSymbol, None)
                # 如果获取持仓缓存失败，则默认平昨
                if not posBuffer:
                    req.offset = OFFSET_CLOSE
                # 否则如果有多头今仓，则使用平今
                elif posBuffer.longToday:
                    req.offset= OFFSET_CLOSETODAY
                # 其他情况使用平昨
                else:
                    req.offset = OFFSET_CLOSE
                
        elif orderType == CTAORDER_SHORT:
            req.direction = DIRECTION_SHORT
            req.offset = OFFSET_OPEN
            
        elif orderType == CTAORDER_COVER:
            req.direction = DIRECTION_LONG
            
            # 只有上期所才要考虑平今平昨
            if contract.exchange != EXCHANGE_SHFE:
                req.offset = OFFSET_CLOSE
            else:
                # 获取持仓缓存数据
                posBuffer = self.posBufferDict.get(vtSymbol, None)
                # 如果获取持仓缓存失败，则默认平昨
                if not posBuffer:
                    req.offset = OFFSET_CLOSE
                # 否则如果有空头今仓，则使用平今
                elif posBuffer.shortToday:
                    req.offset= OFFSET_CLOSETODAY
                # 其他情况使用平昨
                else:
                    req.offset = OFFSET_CLOSE

        vtOrderID = self.mainEngine.sendOrder(req, contract.gatewayName)    # 发单

        # print vtOrderID
        self.orderStrategyDict[vtOrderID] = strategy        # 保存vtOrderID和策略的映射关系
        # self.writeCtaLog(u'策略%s发送委托，%s，%s，%s@%s' %(strategy.name, vtSymbol, req.direction, volume, price))
        # logger.info(u'策略%s发送委托，%s，%s，%s@%s' %(strategy.name, vtSymbol, req.direction, volume, price))
        
        return vtOrderID

    # ----------------------------------------------------------------------
    def findOrderId(self, targetPrice, orderType):
        """find Order ID"""
        orderIdlst = self.mainEngine.findOrderID(targetPrice, orderType)
        return orderIdlst

    # ----------------------------------------------------------------------
    def updateOrderStrategyDict(self, strategy):
        """Execute at the beginning to update last-time open(untraded) orders """
        initAllWorkingOrders = self.mainEngine.getAllWorkingOrders()
        for ord in initAllWorkingOrders:
            if not ord.vtOrderID in self.orderStrategyDict:
                self.orderStrategyDict[ord.vtOrderID] = strategy  # 保存vtOrderID和策略的映射关系

    #----------------------------------------------------------------------
    def findVtSymbolWorkingOrders(self, vtSymbol):
        """get all working orders in the corresponding vtSymbol of strategy"""
        allWorkingOrders = self.mainEngine.getAllWorkingOrders()
        # print 'findVtSymbolWorkingOrders %s' % allWorkingOrders
        vtSymbolWorkingOrders = []
        for ord in allWorkingOrders:
            if ord.vtOrderID in self.orderStrategyDict:
                if ord.vtSymbol == vtSymbol:
                    vtSymbolWorkingOrders.append(ord)

        return len(vtSymbolWorkingOrders)

    #----------------------------------------------------------------------
    def findWorkingOrders(self):
        """Check how many orders are in workingOrders"""
        return len(self.mainEngine.getAllWorkingOrders())

    #----------------------------------------------------------------------
    def getAllWorkingOrders(self):
        return self.mainEngine.getAllWorkingOrders()

    #----------------------------------------------------------------------
    def cancelOrder(self, vtOrderID):
        """撤单"""
        # self.mainEngine.tickHaltSwitch(['OKCOIN', 'ZHCOIN']) # halt receiving the tick info from OKCOIN and ZHCOIN
        # print 'cancelOrder: %s' % vtOrderID
        # 查询报单对象
        orderIdlst = []
        gatewayName = ''
        for id in vtOrderID:
            order = self.mainEngine.getOrder(id)
            # 如果查询成功
            if order:
                # 检查是否报单还有效，只有有效时才发出撤单指令
                orderFinished = (order.status == STATUS_ALLTRADED or order.status == STATUS_CANCELLED)
                if not orderFinished:
                    orderIdlst.append(order.orderID)
                    gatewayName = order.gatewayName

        if orderIdlst:
            self.mainEngine.cancelOrder(orderIdlst, gatewayName)
            # sleep(120) # wait 2 minutes
            # self.mainEngine.tickHaltSwitch(['OKCOIN', 'ZHCOIN'])  # proceed receiving the tick info from OKCOIN and ZHCOIN

    # ----------------------------------------------------------------------
    def cancelAll(self, vtSymbol):
        """Cancel All orders"""
        contract = self.mainEngine.getContract(vtSymbol)
        self.mainEngine.cancelAll(contract.gatewayName)
        self.writeCtaLog(u'%s发送取消委托' % vtSymbol)

    #----------------------------------------------------------------------
    def getContract(self, vtSymbol):
        return self.mainEngine.getContract(vtSymbol)

    #----------------------------------------------------------------------
    def sendStopOrder(self, vtSymbol, orderType, price, volume, strategy):
        """发停止单（本地实现）"""
        self.stopOrderCount += 1
        stopOrderID = STOPORDERPREFIX + str(self.stopOrderCount)
        
        so = StopOrder()
        so.vtSymbol = vtSymbol
        so.orderType = orderType
        so.price = price
        so.volume = volume
        so.strategy = strategy
        so.stopOrderID = stopOrderID
        so.status = STOPORDER_WAITING
        
        if orderType == CTAORDER_BUY:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_SELL:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_CLOSE
        elif orderType == CTAORDER_SHORT:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_COVER:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_CLOSE           
        
        # 保存stopOrder对象到字典中
        self.stopOrderDict[stopOrderID] = so
        self.workingStopOrderDict[stopOrderID] = so
        
        return stopOrderID
    
    #----------------------------------------------------------------------
    def cancelStopOrder(self, stopOrderID):
        """撤销停止单"""
        # 检查停止单是否存在
        if stopOrderID in self.workingStopOrderDict:
            so = self.workingStopOrderDict[stopOrderID]
            so.status = STOPORDER_CANCELLED
            del self.workingStopOrderDict[stopOrderID]

    #----------------------------------------------------------------------
    def processStopOrder(self, tick):
        """收到行情后处理本地停止单（检查是否要立即发出）"""
        vtSymbol = tick.vtSymbol
        
        # 首先检查是否有策略交易该合约
        if vtSymbol in self.tickStrategyDict:
            # 遍历等待中的停止单，检查是否会被触发
            for so in self.workingStopOrderDict.values():
                if so.vtSymbol == vtSymbol:
                    longTriggered = so.direction==DIRECTION_LONG and tick.lastPrice>=so.price        # 多头停止单被触发
                    shortTriggered = so.direction==DIRECTION_SHORT and tick.lastPrice<=so.price     # 空头停止单被触发
                    
                    if longTriggered or shortTriggered:
                        # 买入和卖出分别以涨停跌停价发单（模拟市价单）
                        if so.direction==DIRECTION_LONG:
                            price = tick.upperLimit
                        else:
                            price = tick.lowerLimit
                        
                        so.status = STOPORDER_TRIGGERED
                        self.sendOrder(so.vtSymbol, so.orderType, price, so.volume, so.strategy)
                        del self.workingStopOrderDict[so.stopOrderID]

    #----------------------------------------------------------------------
    def processTickEvent(self, event):
        tick = event.dict_['data']
        if tick.vtSymbol in self.tickStrategyDict:
            mmTick = MMTickData()
            d = mmTick.__dict__
            for key in d.keys():
                if key != 'datetime':
                    d[key] = tick.__getattribute__(key)
            if tick.exchange in ["ZHCOIN"]:
                mmTick.datetime = datetime.strptime(' '.join([tick.date, tick.time]), '%Y%m%d %H:%M:%S')
            else:
                mmTick.datetime = datetime.strptime(' '.join([tick.date, tick.time]), '%Y%m%d %H:%M:%S.%f')


            l = self.tickStrategyDict[tick.vtSymbol]
            for strategy in l:
                if strategy.inited:
                    self.callStrategyFunc(strategy, strategy.onTick, mmTick)
    
    #----------------------------------------------------------------------
    def processOrderEvent(self, event):
        """处理委托推送"""
        order = event.dict_['data']
        for k in self.strategyDict:
            strategy = self.strategyDict[k]
            self.callStrategyFunc(strategy, strategy.onOrder, order)
    
    #----------------------------------------------------------------------
    def processTradeEvent(self, event):
        """处理成交推送"""
        trade = event.dict_['data']
        # print 'processTrade: %s' % trade.__dict__
        # if trade.vtSymbol in self.tickStrategyDict:
        #     # 逐个推送到策略实例中
        #     l = self.tickStrategyDict[trade.vtSymbol]
        #     for strategy in l:
        #         self.callStrategyFunc(strategy, strategy.onTick, trade)

        # 过滤已经收到过的成交回报
        if trade.vtTradeID in self.tradeSet:
            return
        self.tradeSet.add(trade.vtTradeID)

        if trade.vtOrderID in self.orderStrategyDict:
            strategy = self.orderStrategyDict[trade.vtOrderID]
            sg = '.'.join([trade.symbol, trade.gatewayName])

            if sg == strategy.tarVtSymbol:
                # 计算策略持仓
                if trade.direction == DIRECTION_LONG:
                    strategy.pos[0] += trade.volume
                else:
                    strategy.pos[0] -= trade.volume
            elif sg == strategy.refVtSymbol:
                # 计算策略持仓
                if trade.direction == DIRECTION_LONG:
                    strategy.pos[1] += trade.volume
                else:
                    strategy.pos[1] -= trade.volume

            self.callStrategyFunc(strategy, strategy.onTrade, trade)

        # 更新持仓缓存数据
        if trade.vtSymbol in self.tickStrategyDict:
            posBuffer = self.posBufferDict.get(trade.vtSymbol, None)
            if not posBuffer:
                posBuffer = PositionBuffer()
                posBuffer.vtSymbol = trade.vtSymbol
                self.posBufferDict[trade.vtSymbol] = posBuffer
            posBuffer.updateTradeData(trade)
            
    #----------------------------------------------------------------------
    def processPositionEvent(self, event):
        """处理持仓推送"""
        pos = event.dict_['data']
        
        # 更新持仓缓存数据
        if pos.vtSymbol in self.tickStrategyDict:
            posBuffer = self.posBufferDict.get(pos.vtSymbol, None)
            if not posBuffer:
                posBuffer = PositionBuffer()
                posBuffer.vtSymbol = pos.vtSymbol
                self.posBufferDict[pos.vtSymbol] = posBuffer
            posBuffer.updatePositionData(pos)
    
    #----------------------------------------------------------------------
    def registerEvent(self):
        """注册事件监听"""
        self.eventEngine.register(EVENT_TICK, self.processTickEvent)
        self.eventEngine.register(EVENT_ORDER, self.processOrderEvent)
        # self.eventEngine.register(EVENT_TRADE, self.processTradeEvent)
        # self.eventEngine.register(EVENT_POSITION, self.processPositionEvent)

    #----------------------------------------------------------------------
    def writeCtaLog(self, content):
        """快速发出CTA模块日志事件"""
        log = VtLogData()
        log.logContent = content
        event = Event(type_=EVENT_CTA_LOG)
        event.dict_['data'] = log
        self.eventEngine.put(event)   
    
    #----------------------------------------------------------------------
    def __loadStrategy(self, setting):
        """载入策略"""
        try:
            name = setting['name']
            className = setting['className']
        except Exception as e:
            self.writeCtaLog(u'载入策略出错：%s' %e)
            return
        
        # 获取策略类
        strategyClass = STRATEGY_CLASS.get(className, None)
        if not strategyClass:
            self.writeCtaLog(u'找不到策略类：%s' %className)
            return
        
        # 防止策略重名
        if name in self.strategyDict:
            self.writeCtaLog(u'策略实例重名：%s' %name)
        else:
            # 创建策略实例
            strategy = strategyClass(self, setting)  
            self.strategyDict[name] = strategy
            # 保存Tick映射关系
            # It is possible to have 1 vtSymbol to multiple strategies
            # Plus, it is also possible to have multiple vtSymbols to 1 strategy
            for symbol in strategy.vtSymbol:
                if symbol in self.tickStrategyDict:
                    l = self.tickStrategyDict[symbol]
                else:
                    l = []
                    self.tickStrategyDict[symbol] = l
                l.append(strategy)

            # 订阅合约
            # contract = self.mainEngine.getContract(strategy.vtSymbol)
            # if contract:
            #     req = VtSubscribeReq()
            #     req.symbol = contract.symbol
            #     req.exchange = contract.exchange
            #
            #     # 对于IB接口订阅行情时所需的货币和产品类型，从策略属性中获取
            #     req.currency = strategy.currency
            #     req.productClass = strategy.productClass
            #
            #     self.mainEngine.subscribe(req, contract.gatewayName)
            # else:
            #     self.writeCtaLog(u'%s的交易合约%s无法找到' %(name, strategy.vtSymbol))

    #----------------------------------------------------------------------
    def initStrategy(self, name):
        """初始化策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            
            if not strategy.inited:
                strategy.inited = True
                self.callStrategyFunc(strategy, strategy.onInit)
            else:
                self.writeCtaLog(u'请勿重复初始化策略实例：%s' %name)
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)        

    #---------------------------------------------------------------------
    def startStrategy(self, name):
        """启动策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            
            if strategy.inited and not strategy.trading:
                strategy.trading = True
                self.callStrategyFunc(strategy, strategy.onStart)
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)
    
    #----------------------------------------------------------------------
    def stopStrategy(self, name):
        """停止策略"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            
            if strategy.trading:
                strategy.trading = False
                self.callStrategyFunc(strategy, strategy.onStop)
                
                # 对该策略发出的所有限价单进行撤单
                for vtOrderID, s in self.orderStrategyDict.items():
                    if s is strategy:
                        self.cancelOrder(vtOrderID)
                
                # 对该策略发出的所有本地停止单撤单
                for stopOrderID, so in self.workingStopOrderDict.items():
                    if so.strategy is strategy:
                        self.cancelStopOrder(stopOrderID)   
        else:
            self.writeCtaLog(u'策略实例不存在：%s' %name)        
    
    #----------------------------------------------------------------------
    def saveSetting(self):
        """保存策略配置"""
        with open(self.settingFileName, 'w') as f:
            l = []
            
            for strategy in self.strategyDict.values():
                setting = {}
                for param in strategy.paramList:
                    setting[param] = strategy.__getattribute__(param)
                l.append(setting)
            
            jsonL = json.dumps(l, indent=4)
            f.write(jsonL)
    
    #----------------------------------------------------------------------
    def loadSetting(self):
        """读取策略配置"""
        with open(self.settingFileName) as f:
            l = json.load(f)
            
            for setting in l:
                self.__loadStrategy(setting)
    
    #----------------------------------------------------------------------
    def getStrategyVar(self, name):
        """获取策略当前的变量字典"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            varDict = OrderedDict()
            
            for key in strategy.varList:
                varDict[key] = strategy.__getattribute__(key)
            
            return varDict
        else:
            self.writeCtaLog(u'策略实例不存在：' + name)    
            return None
    
    #----------------------------------------------------------------------
    def getStrategyParam(self, name):
        """获取策略的参数字典"""
        if name in self.strategyDict:
            strategy = self.strategyDict[name]
            paramDict = OrderedDict()
            
            for key in strategy.paramList:  
                paramDict[key] = strategy.__getattribute__(key)
            
            return paramDict
        else:
            self.writeCtaLog(u'策略实例不存在：' + name)    
            return None   
        
    #----------------------------------------------------------------------
    def putStrategyEvent(self, name):
        """触发策略状态变化事件（通常用于通知GUI更新）"""
        event = Event(EVENT_CTA_STRATEGY+name)
        self.eventEngine.put(event)
        
    #----------------------------------------------------------------------
    def callStrategyFunc(self, strategy, func, params=None):
        """调用策略的函数，若触发异常则捕捉"""
        try:
            if params:
                func(params)
            else:
                func()
        except Exception:
            # 停止策略，修改状态为未初始化
            strategy.trading = False
            strategy.inited = False
            
            # 发出日志
            print traceback.format_exc()
            content = '\n'.join([u'策略%s触发异常已停止' %strategy.name,
                                traceback.format_exc()])
            self.writeCtaLog(content)


########################################################################
class PositionBuffer(object):
    """持仓缓存信息（本地维护的持仓数据）"""

    #----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.vtSymbol = EMPTY_STRING
        
        # 多头
        self.longPosition = EMPTY_INT
        self.longToday = EMPTY_INT
        self.longYd = EMPTY_INT
        
        # 空头
        self.shortPosition = EMPTY_INT
        self.shortToday = EMPTY_INT
        self.shortYd = EMPTY_INT
        
    #----------------------------------------------------------------------
    def updatePositionData(self, pos):
        """更新持仓数据"""
        if pos.direction == DIRECTION_LONG:
            self.longPosition = pos.position
            self.longYd = pos.ydPosition
            self.longToday = self.longPosition - self.longYd
        else:
            self.shortPosition = pos.position
            self.shortYd = pos.ydPosition
            self.shortToday = self.shortPosition - self.shortYd
    
    #----------------------------------------------------------------------
    def updateTradeData(self, trade):
        """更新成交数据"""
        if trade.direction == DIRECTION_LONG:
            # 多方开仓，则对应多头的持仓和今仓增加
            if trade.offset == OFFSET_OPEN:
                self.longPosition += trade.volume
                self.longToday += trade.volume
            # 多方平今，对应空头的持仓和今仓减少
            elif trade.offset == OFFSET_CLOSETODAY:
                self.shortPosition -= trade.volume
                self.shortToday -= trade.volume
            # 多方平昨，对应空头的持仓和昨仓减少
            else:
                self.shortPosition -= trade.volume
                self.shortYd -= trade.volume
        else:
            # 空头和多头相同
            if trade.offset == OFFSET_OPEN:
                self.shortPosition += trade.volume
                self.shortToday += trade.volume
            elif trade.offset == OFFSET_CLOSETODAY:
                self.longPosition -= trade.volume
                self.longToday -= trade.volume
            else:
                self.longPosition -= trade.volume
                self.longYd -= trade.volume
        
        
    
    


