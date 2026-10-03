# 执行期解释口径勘误

付费运行启动后复核 `build_history()`：`repeat=0` 为 8 条合成历史单元，`repeat=1` 为 9 条。因此两个 repeat 是**受控历史长度变体**，不是相同输入的随机重复。每个 task×repeat 内三臂同初始历史，仍可作同批配对；最终结果按 r00/r01 分层，不把两层的均值差解释成随机方差。

这一勘误不修改已冻结协议、运行器、任务文件、模型或预算，也不增加样本。旧 v2cal 的 r00 只作独立批次旁证。

付费后审计还发现请求槽与 HTTP 响应计数的区别：运行器在辅助摘要调用前先 `RequestBudget.consume()`；只有成功返回的摘要才累计 `SummaryCallLedger.calls` 和 token usage。69 个已占用预算槽中，54 个对应 Agent 尝试，15 个对应摘要槽预留；其中 11 个摘要取得 usage，另外 4 个没有 usage。现存 `results.jsonl` 不记录这 4 个调用的异常或传输状态，不能断言它们都到达 DeepSeek HTTP 端点，也不能断言供应商收费为零。结果中的 token 总量仅是**已记录用量**，不等同于完整账单。冻结协议中的“真实请求”字样在这里应按“预算槽”理解。

另有两个已核实的运行器行为：`max_summary_calls=6` 检查的是 `SummaryCallLedger.calls`（成功取得响应的次数），失败只增加 `failures`，所以该参数并非摘要**尝试**硬上限；全局 96 槽仍为尝试前的预算护栏。运行结束时日志出现 `RuntimeWarning: coroutine 'AsyncAPIClient.close' was never awaited`，因为主函数同步调用 `summary_transport.close()`。这发生在批次末尾，不能解释中途 4 个无 usage 的槽。`AsyncOpenAI` 被全批共享，但每个原生样本各自新建并关闭 `LoopRunner`；`native_summary_common.py` 源码注释已提示跨事件循环复用连接可能出错。这是待验证的故障假设，现有持久化结果没有摘要异常详情，不能把 4 槽归因于此。
