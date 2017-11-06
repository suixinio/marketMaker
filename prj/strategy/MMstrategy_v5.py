# encoding: UTF-8

"""
一个ATR-RSI指标结合的交易策略，适合用在股指的1分钟和5分钟线上。

注意事项：
1. 作者不对交易盈利做任何保证，策略代码仅供参考
2. 本策略需要用到talib，没有安装的用户请先参考www.vnpy.org上的教程安装
3. 将IF0000_1min.csv用ctaHistoryData.py导入MongoDB后，直接运行本文件即可回测策略

"""

from MMBase import *
from MMTemplate import MMTemplate
from collections import OrderedDict
import numpy as np
from vtGateway import *
from datetime import datetime
import random
from operator import itemgetter, attrgetter
import  logging
import copy
from vnlog import vnLog
from vtFunction import *

DIRECTION_LONG = u'多'
DIRECTION_SHORT = u'空'


########################################################################
class MarketBalance(MMTemplate):
    """BitCoin做市平衡市场交易策略"""
    className = 'MMStrategy'
    author = u'CYX'

    # 策略参数
    initDays = 10  # 初始化数据所用的天数

    # 策略变量
    bar = None  # K线对象
    barMinute = EMPTY_STRING  # K线当前的分钟

    bufferSize = 100  # 需要缓存的数据的大小
    bufferCount = 0  # 目前已经缓存了的数据的计数
    highArray = np.zeros(bufferSize)  # K线最高价的数组
    lowArray = np.zeros(bufferSize)  # K线最低价的数组
    closeArray = np.zeros(bufferSize)  # K线收盘价的数组

    orderList = []  # 保存委托代码的列表
    EntryOrder = []

    # 参数列表，保存了参数的名称
    paramList = ['name',
                 'className',
                 'author',
                 'vtSymbol']

    # 变量列表，保存了变量的名称
    varList = ['inited',
               'trading',
               'pos']

    # orderbook_prc = {}      # 记录上一个tick的订单薄价格
    # orderbook_vol = {}      # 记录上一个tick的订单薄交易量
    # tick_old = VtTickData()             # 记录上一个tick
    orderbook1 = []
    orderbook2 = []
    tunepct = 0.5
    tickcount = 0
    tickcount1 = 0
    # print fields

    orderbook_buy = {}
    orderbook_sell = {}

    # ----------------------------------------------------------------------
    def __init__(self, mmEngine, setting):
        """Constructor"""
        # the mmEngine here could be either MMEngine for real-trading, or backtestingEngine for backtesting.
        super(MarketBalance, self).__init__(mmEngine, setting)

        # 注意策略类中的可变对象属性（通常是list和dict等），在策略初始化时需要重新创建，
        # 否则会出现多个策略实例之间数据共享的情况，有可能导致潜在的策略逻辑错误风险，
        # 策略类中的这些可变对象属性可以选择不写，全都放在__init__下面，写主要是为了阅读
        # 策略时方便（更多是个编程习惯的选择）
        # order_id -> order
        self.idOrderDict = {}
        # price -> list(order_id)
        self.priceOrderIdsDict = {}

        self.orderUpdate = False
        self.tickUpdate = False
        self.OKtickUpdate = False

        self.logger = vnLog('marketMaker.log')
    # ----------------------------------------------------------------------
    def onInit(self):
        """初始化策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略初始化' % self.name)

        # # 载入历史数据，并采用回放计算的方式初始化策略数值
        # initData = self.loadBar(self.initDays)
        # for bar in initData:
        #     self.onBar(bar)

        self.putEvent()

    # ----------------------------------------------------------------------
    def onStart(self):
        """启动策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略启动' % self.name)
        self.putEvent()

    # ----------------------------------------------------------------------
    def onStop(self):
        """停止策略（必须由用户继承实现）"""
        self.writeCtaLog(u'%s策略停止' % self.name)
        self.putEvent()


    # 做市策略：根据okcoin的orderbook，灵活调整zhcoin的orderbook。要求价格一致，挂单量成有一定相关性
    def onTick(self, tick):
        if 'OKCOIN' in tick.vtSymbol:
            def get_orderbook1():
                bids1 = copy.deepcopy(tick.bids)
                asks1 = copy.deepcopy(tick.asks)
                bids1vol = [0]*len(bids1)
                asks1vol = [0]*len(bids1)
                for i in range(len(bids1)):
                    bids1[i][0] = priceUniform(bids1[i][0])      # 委托价取两位小数
                    bids1vol[i] = (float(bids1[i][1]))  # 单独记录委托量以便后续计算总量和比例
                    asks1[i][0] = priceUniform(asks1[i][0])
                    asks1vol[i] = (float(asks1[i][1]))

                bids1volpct = [x / sum(bids1vol) for x in bids1vol]  # 委买单量占总委买单量比例
                asks1volpct = [x / sum(asks1vol) for x in asks1vol]  # 委卖单量占总委卖单量比例
                if not bids1vol:
                    return
                PCT = sum(asks1vol) / sum(bids1vol)  # 总委卖单量与总委买单量比值
                BuyTotalVol = 5  # ZHCOIN总委买单量（根据人民币账户资金换算成比特币数目确定）
                SellTotalVol = int(round(BuyTotalVol * PCT * 10)) / 10.0  # 根据市场买卖方量比等比例确定，保留小数点一位
                for i in range(len(bids1)):
                    bids1[i][1] = priceUniform(bids1volpct[i] * BuyTotalVol)  # 某一价位的委托量占总委托量比例保持一致
                    asks1[i][1] = - priceUniform(asks1volpct[i] * SellTotalVol)
                tmp1 = bids1
                tmp1[len(bids1):] = asks1
                self.orderbook1 = tmp1  # 目标orderbook,委托量为负表示卖出
            get_orderbook1()
            self.OKtickUpdate = True

        elif 'ZHCOIN' in tick.vtSymbol:
            def get_orderbook2():
                bids2 = copy.deepcopy(tick.bids)
                asks2 = copy.deepcopy(tick.asks)
                for i in range(len(bids2)):
                    bids2[i][0] = priceUniform(bids2[i][0])
                    bids2[i][1] = volumeUniform(bids2[i][1])
                for i in range(len(asks2)):
                    asks2[i][0] = priceUniform(asks2[i][0])
                    asks2[i][1] = - volumeUniform(asks2[i][1])
                tmp2 = bids2
                tmp2[len(bids2):] = asks2
                self.orderbook2 = tmp2
            get_orderbook2()
            self.tickUpdate = True

        # 每隔n个tick进行操作
        self.tickcount += 1
        if not self.OKtickUpdate or not self.tickUpdate or not self.orderUpdate or self.tickcount <= 2:
            return
        else:
            self.tickcount = 0

        print ('OKCOIN')
        print (self.orderbook1)
        print ('ZHCOIN')
        print (self.orderbook2)
        def trade_in_lastPrice():
            n = random.randint(1, 10)
            pri = priceUniform(tick.lastPrice)
            v = 0.0001
            if n % 2 == 0:
                self.buy('BTC_CNY.ZHCOIN', pri, v)
                self.sell('BTC_CNY.ZHCOIN', pri - 0.1, v)
            else:
                self.sell('BTC_CNY.ZHCOIN', pri, v)
                self.buy('BTC_CNY.ZHCOIN', pri + 0.1, v)

            # buyVol = 0.
            # sellVol = 0.
            # for pri_vol in self.orderbook2:
            #     if pri_vol[1] > 0:
            #         buyVol += pri_vol[1]
            #     else:
            #         sellVol += pri_vol[1]
            # print 'buyVol:',buyVol, 'sellVol:',sellVol
            # if buyVol > 2:
            #     self.sell('BTC_CNY.ZHCOIN', pri-1000, 2)
            # elif sellVol > 2:
            #     self.buy('BTC_CNY.ZHCOIN', pri + 1000, 2)

        trade_in_lastPrice()

        self.orderUpdate = False


    # ----------------------------------------------------------------------
    def onBar(self, bar):
        pass

    # ----------------------------------------------------------------------
    def onOrder(self, order):
        if order.status in [STATUS_PARTTRADED, STATUS_PENDING]:
            if not self.idOrderDict.has_key(order.vtOrderID):
                self.idOrderDict[order.vtOrderID] = order
                if order.status == STATUS_PENDING:
                    print '排队单子', order.vtOrderID
                else:
                    print '部分成交单子', order.vtOrderID
        else:
            if order.vtOrderID in self.idOrderDict:
                del self.idOrderDict[order.vtOrderID]
                if order.status == STATUS_ALLTRADED:
                    print '全部成交', order.vtOrderID
                else:
                    print '全部撤单', order.vtOrderID

        price = priceUniform(order.price)

        # 可撤单 且不在list中 ，则插入   # 成交了 或 撤销了，则删除
        if order.status in [STATUS_PARTTRADED, STATUS_PENDING]:
            if not self.priceOrderIdsDict.has_key(price):
                self.priceOrderIdsDict[price] = []
                print 'append ', price
            orderList = self.priceOrderIdsDict[price]
            if orderList.count(order.vtOrderID) == 0:
                orderList.append(order.vtOrderID)
        # elif order.status in [STATUS_ALLTRADED, STATUS_CANCELLED]:
        else:
            if self.priceOrderIdsDict.has_key(price):
                orderList = self.priceOrderIdsDict[price]
                if orderList.count(order.vtOrderID) > 0:
                    orderList.remove(order.vtOrderID)
                if len(orderList) == 0:
                    del self.priceOrderIdsDict[price]
                    print 'del ', price

        self.orderUpdate = True


    # ----------------------------------------------------------------------
    def onTrade(self, trade):
        pass


if __name__ == '__main__':
    # 提供直接双击回测的功能
    # 导入PyQt4的包是为了保证matplotlib使用PyQt4而不是PySide，防止初始化出错
    from ctaBacktesting_tick import *
    from PyQt4 import QtCore, QtGui

    # 创建回测引擎
    engine = BacktestingEngine()

    # 设置引擎的回测模式为K线
    engine.setBacktestingMode(engine.BAR_MODE)

    # 设置回测用的数据起始日期
    engine.setStartDate('20161101')
    # engine.setStartDate('20110101')

    # 设置使用的历史数据库
    engine.setDatabase(TICK_DB_NAME, 'EUR_USD.OANDA')
    # engine.setDatabase(MINUTE_DB_NAME, 'IF0000')

    # 设置产品相关参数
    # engine.setSlippage(0.02)     # 股指1跳
    engine.setSlippage(0.0001)  # 股指1跳
    engine.setRate(0.3 / 10000)  # 万0.3
    engine.setSize(300)  # 股指合约大小
    engine.setFreq(5)

    ## 在引擎中创建策略对象
    # setting = {'atrLength': 11}
    engine.initStrategy(MarketBalance)

    ## 开始跑回测
    engine.runBacktesting()

    ## 显示回测结果
    engine.showBacktestingResult()

    # 跑优化
    # setting = OptimizationSetting()                 # 新建一个优化任务设置对象
    # setting.setOptimizeTarget('capital')            # 设置优化排序的目标是策略净盈利
    # setting.addParameter('atrLength', 11, 20, 1)    # 增加第一个优化参数atrLength，起始11，结束12，步进1
    # setting.addParameter('atrMa', 20, 30, 5)        # 增加第二个优化参数atrMa，起始20，结束30，步进1

    # 性能测试环境：I7-3770，主频3.4G, 8核心，内存16G，Windows 7 专业版
    # 测试时还跑着一堆其他的程序，性能仅供参考
    import time

    start = time.time()

    # 运行单进程优化函数，自动输出结果，耗时：359秒
    # engine.runOptimization(AtrRsiStrategy, setting)

    # 多进程优化，耗时：89秒
    # engine.runParallelOptimization(AtrRsiStrategy, setting)

    # print u'耗时：%s' %(time.time()-start)