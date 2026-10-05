# 01｜先让一条客诉跑起来

[课程入口](README.md) · 下一章：[值与类型](02-values.md)

## 业务问题与目标

用户说“我的订单未享受优惠”，程序该查哪个订单？先启动固定离线请求，找到输入、补槽、事实回复和终态。前置只需函数、类、import和字典；先认出await的位置，稍后再解释调度。

## 先建立四个概念

根uv workspace像Maven聚合项目：app提供用例，services提供独立MCP服务，tools提供明确执行的实验和验收。根uv.lock约束同一套依赖；包边界不等于微服务，也不意味着每次学习启动全部服务。

`python -m 包.模块`在当前环境查找并执行模块入口，不是执行当前目录下同名文件，安装wheel后也能运行。import会执行模块顶层语句，所以创建数据库连接不能放在普通import路径。`if __name__ == "__main__"`保护CLI入口，库消费者导入时不会启动服务。

生产入口是`deephelp_app.__main__`→`bootstrap.commands.mvp_cli.main`；serve打开运行依赖，ask提交消息。本章使用tools课程入口，装配ReplayAssembly并复用生产Conversation，不加载.env.local。Spring容器通常帮你创建单例，这里assembly显式创建对象，用上下文管理器打开依赖；类定义本身不是运行中的对象。

## 从仓库根运行

Windows使用现有Python3.14.7环境。以下全部离线；request用包内合成订单和正式SDK stdio工具，远程模型0次。准备环境与课程运行是两件事。

```powershell
py -3.14 -m uv run --locked python --version
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course --help
py -3.14 -m uv run --locked python -m deephelp_tools.learning.course request
```

预期Python为3.14.7；输出evidence_kind=synthetic_mechanism_replay、remote_model_calls=0。ID每次不同，核对关系与内容：

1. missing返回CLARIFY，缺order_id，tool_calls为0。
2. answered沿原问题补充000031，回复含99.90 CNY实付和10.00 CNY优惠，有facts/evidence。
3. replay重投同一消息，run_id与answered相同，本次四种计数全0。
4. unknown是无关物流诉求，HANDOFF、工具0次。

counts.model_calls是经模型端口的动作计数，本实验由RecordedModel返回固定结构，不能当供应商账单。课程CLI封住Python TCP/DNS及libpq；Windows先初始化事件循环内部socketpair，再封业务网络。受控stdio子进程只加载demo资料；这不是任意第三方子进程的网络沙箱。

## 一步一步找到源码

打开[request_demo](../../modules/deephelp-tools/src/deephelp_tools/learning/course/request.py)：repo/judge/trace是实验适配器；assembly.open拥有stdio进程；conversation.run进入[生产编排](../../modules/deephelp-app/src/deephelp_app/application/conversation/orchestrator.py)。沿[源码导航](source-navigation.md)读preparation→state→intent→sop→terminal。

先在request_demo找到四次run的输入输出，再在Conversation找到“未领取新执行就返回原receipt”的分支，最后在[reply.fact_reply](../../modules/deephelp-app/src/deephelp_app/application/reply.py)找到Money怎样变成正文。实验保存在内存账本，正式运行保存在PG，持久性证据不同。

## 常见误区与反例

- 从IDE直接运行内部文件可能破坏包导入；用根环境和-m，类似通过正确classpath启动Java main。
- HTTP200、JSON或ANSWERED不证明事实正确，继续核对金额、evidence和工具记录。
- 有Key、在云端或import httpx都不意味着调用模型，入口和端口装配才决定行为。

## 练习与答案

预测把第三次调用的message_id换成新ID是否仍零调用重投。

答案：新ID代表新消息，会重新经过归属/领取和可能的工具路径。相同文本不是幂等键。实验自查：指出创建Conversation、等待它、原事实和本次计数的位置；项目不据此宣称用户已经学会。
