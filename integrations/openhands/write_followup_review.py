"""Compile completed development iterations with honest quality denominators."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
rounds=[('v15','independent-two-projects-1x3-v15','pruner_v7'),('v16','independent-two-projects-1x3-v16','pruner_v8'),('v17','independent-two-projects-1x3-v17','pruner_v8'),('v18','independent-two-projects-1x3-v18','pruner_v9')]
if (ROOT/'runs/stage5-openhands/independent-two-projects-3x3-v19/audit.json').exists():
    rounds.append(('v19','independent-two-projects-3x3-v19','pruner_v9'))
if (ROOT/'runs/stage5-openhands/independent-two-projects-3x3-v20/audit.json').exists():
    rounds.append(('v20','independent-two-projects-3x3-v20','pruner_v9'))
if (ROOT/'runs/stage5-openhands/independent-two-projects-3x3-v21/audit.json').exists():
    rounds.append(('v21','independent-two-projects-3x3-v21','pruner_v9'))
lines=['# 稀疏方法、证据容量与近期编辑窗口复验','','这是同一批已接触的 Click / packaging 自编功能任务的开发试验。保留全部失败和中间版本，不称作新盲测。v14 完整三次对照见 V6_REPAIR_REVIEW.md。','','## 改动与验证','','v7 在稀疏 Python 类片段无法整体 AST 解析时按函数签名和缩进识别已观察方法，多行签名一起保留；未观察缺口不伪造。v8 将函数得分按观察代码长度平方根调整，优先保存多个小型相关 API，普通模块片段排后。实现为 prototype_v7.py / prototype_v8.py / prototype_v9.py，没有新增摘要模型。模型实验结束后复制到 context_pruner/adapters/openhands_v9.py，默认预算与实测配置一致，全部函数 AST 相同，并通过零 API 等价验证；实时实验仍以冻结 prototype_v9.py 为源码证据。这些 OpenHands 实验适配器使用本地证据选择，不声称覆盖核心中间件全部生命周期与自动恢复。','','v15/v16 保持原触发 12,000、目标 10,000、硬界 16,000；v17 保持 v8 算法，触发提高为 24,000、目标 20,000、硬界 32,000，原生摘要触发同步提高，其他预算不变。v18 使用同一较大预算，改为尽量保留最近四个完整 SDK 工具批次，在硬界不足时按安全边界缩小窗口；窗口不保证总能保留四批。因此跨轮差异同时含模型轨迹波动及参数变更，只解释各轮内配对。v19 将 v9 的同一配置扩大到每任务三次、共18样本，但14个样本受到连接错误干扰，不作为有效确认。v20 在接口恢复后使用同一配置与独立新目录完整重跑，只增加连接错误立即停止，不添加模型重试。这些仍是已接触任务的开发重复验证。','','累计 21 项本地 SDK 机制测试逐批通过；最后一项验证可导入模块默认配置与冻结原型的压缩结果一致。v7 在五条已完成 v14 轨迹上回放 84 个接受点；v8 在全部六条上回放 84 个接受点；较大预算回放 35 个接受点；v9 窗口回放 51 个接受点。这些本地检查没有 API 请求或硬预算违规；这些是机械检查，不能替代真实任务验收。','','## 完整结果','','| 轮次 | 方法 | 正常完成 | 最终代码通过 | 实际输入（含摘要） | 请求总数 |','','|---|---|---:|---:|---:|---:|']
# Remove the blank within the Markdown table.
lines=[x for i,x in enumerate(lines) if not (x=='' and i>0 and lines[i-1].startswith('| 轮次'))]
latest=None
for name,directory,plugin in rounds:
    out=ROOT/'runs/stage5-openhands'/directory
    a=json.loads((out/'audit.json').read_text(encoding='utf-8'))
    assert a['source_hashes_valid'] and not a['credential_matches']
    diag=json.loads((out/'trace-diagnostics.json').read_text(encoding='utf-8'))
    cross=json.loads((out/'completion-state-crosscheck.json').read_text(encoding='utf-8'))
    assert cross['mismatches']==0
    for arm,v in a['arms'].items():
        lines.append(f"| {name} | {arm} | {v['successes']}/{v['samples']} | {v['artifact_successes']}/{v['samples']} | {v['total_input_tokens']:,} | {v['model_calls']} |")
    latest=(name,a,plugin)
lines+=['','## 配对输入减少率','','v19 的全配对数字仅描述连接中断后的残留计数，不用于评估效果；失败请求缺失 usage 的消耗未知。v20 在第五样本遇代理 TLS 握手中断后停止（5/18），也不作完整比较。v21 只为实验进程设置 DeepSeek 直连，保留其他路由与 TLS 验证，用于同配置完整重跑。以有效完整复验判断结果。','','| 轮次 | 方法 | 全配对均值 | 双方通过且 API 正常均值 |','|---|---|---:|---:|']
def fmt(m):return ('不可用' if m['mean'] is None else f"{m['mean']:.2%}")+f" (n={m['n']})"
for name,directory,plugin in rounds:
    a=json.loads((ROOT/'runs/stage5-openhands'/directory/'audit.json').read_text(encoding='utf-8'))
    for arm,p in a['paired'].items():
        lines.append(f"| {name} | {arm} | {fmt(p['metrics']['all'])} | {fmt(p['metrics']['both_success_api_ok'])} |")
name,a,plugin=latest;base=a['arms']['none'];v=a['arms'][plugin];m=a['paired'][plugin]['metrics']['both_success_api_ok']
quality=v['successes']>=base['successes'] and v['artifact_successes']>=base['artifact_successes']
if not a['complete']:
    verdict=f"{name} 因接口连接故障停止，完整18样本复验尚未完成，不能据部分结果判断效果。已完成v18试跑插件2/2正常通过，但配对平均输入增加11.92%，仍未证实稳定节省。"
elif quality and m['mean'] is not None and m['mean']>=.3:
    verdict=f"{name} 在这两个开发任务上保留通过率，双方通过配对平均减少 {m['mean']:.2%} 输入（n={m['n']}）。这是有限开发证据，不能宣称稳定、普遍有效或独立盲测达到 30%。"
elif quality and m['mean'] is not None and m['mean']>0:
    verdict=f"{name} 在本轮开发样本上保留通过率且有输入减少，双方通过均值 {m['mean']:.2%}，未达到 30% 目标。"
else:
    verdict=f"{name} 仍未确认质量保持且节省输入。不能把失败样本较低输入或单一成功案例作为整体有效证明。"
lines+=['','## 结论与后续','',''+verdict,'','短任务可能不触发压缩，应核查逐样本压缩事件后再归因。两项目及多次调参导致的选择偏差不能通过重复旧任务消除；下一阶段应冻结版本与预算，采用未接触的新任务，并报告全部失败。选定上游模块通过不代表完整上游测试通过。金额映射缺失，美元成本未知；失败请求未返回 usage 的消耗未知。','','所有模型请求已停止。v20 在5/18样本处因连接故障停止，其余轮次按已标注的完成情况报告。源哈希、修改边界、工具结构、归档引用、凭据扫描及 SDK 完成状态由独立审计检查。每轮 manifest、ledger、原事件、工作目录与外部 pytest 输出均保留在对应 runs/stage5-openhands 目录。']
target=ROOT/'integrations/openhands/FOLLOWUP_REPAIR_REVIEW.md'
target.write_text('\n'.join(lines)+'\n',encoding='utf-8')
for path,heading in [(ROOT/'README.md','## 21. 稀疏方法与记忆预算后续复验'),(ROOT/'runs/HANDOFF_NEXT_CHAT.md','## 13. v7/v8/v9 后续开发试验')]:
    old=path.read_text(encoding='utf-8');assert heading not in old
    path.write_text(old.rstrip()+'\n\n'+heading+'\n\n'+verdict+'\n\n详见 integrations/openhands/FOLLOWUP_REPAIR_REVIEW.md；全部中间失败保留。模型请求已停止。\n',encoding='utf-8')
print(verdict)
