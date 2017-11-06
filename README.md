# marketMaker
接入okcoin的数字货币行情，并在众安交易所做市

> 作者：吴典（Andy.Woo） @上海 手机/微信:18621528717
>



# 项目简介

一.代码结构

1.api

通过websocket协议从okcoin接入okcoin柜台上数字货币的tick行情，通过rest协议从众安交易所读写数字货币的行情

2.dataRecorder

基于api接口，将okcoin获取的数字货币的tick转为自定义结构，并实时存入mongoDB数据库中

3.gateway网关

将外部获取的行情结构转为自定义结构

4.strategy

包含了自定义数据结构在系统内部的流转过程以及做市商策略

4.1.MMBase 定了tick和bar等数据结构

4.2.MMEngine 实现了CTA策略引擎，针对CTA类型的策略，抽象简化了部分底层接口的功能

4.3.MMTemplate 包含了CTA引擎中的策略开发用模板，开发策略时需要继承CtaTemplate类

4.4.MMStrategy 做市商策略 （内容详见doc/做市商策略.txt）

4.5.vtMMClient 主引擎 and 程序切入口


