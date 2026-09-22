"""端到端 demo：不依赖 LLM API，用规则版跑一个长程任务示例。"""

from context_pruner import ContextPruner, CompressionAction


def main() -> None:
    turns = [
        {"role": "system", "content": "你是一个科研助理，负责调研并设计一个上下文压缩方案，最终给出可落地的技术路线。"},
        {"role": "user", "content": "请先列出调研方向和可能的方法。"},
        {"role": "assistant", "content": "我打算调研四个方面：截断与滑动窗口、语义摘要、信息量筛选、结构化管理。"},
        {"role": "tool", "content": "search(query=prompt compression survey) -> 返回 20 篇论文，包含 LLMLingua、LongLLMLingua、H2O、StreamingLLM。"},
        {"role": "assistant", "content": "初步判断：截断容易丢关键信息；摘要质量依赖模型；信息量筛选更有潜力。"},
        {"role": "user", "content": "工具调用 1：请获取 LLMLingua 的论文摘要和核心方法。"},
        {"role": "tool", "content": "get_paper(title=LLMLingua) -> 基于困惑度对 prompt 进行 token 级压缩，压缩率可达 20 倍。"},
        {"role": "assistant", "content": "LLMLingua 是强基线，但它是静态压缩规则，不随任务阶段调整。"},
        {"role": "user", "content": "结论：我们采用信息熵+语义相关性评估，按任务阶段自适应调整压缩强度。请开始写方案。"},
        {"role": "assistant", "content": "方案结构：模块一负责上下文解析与分层，模块二负责重要性评估，模块三负责压缩调度。"},
        {"role": "user", "content": "请把方案拆成可实现的步骤，并说明每一步的输入输出。"},
        {"role": "tool", "content": "read(project_repo) -> 已存在 context_pruner 包，含 parser / evaluator / scheduler 骨架。"},
        {"role": "assistant", "content": "实现顺序：先解析分层，再评估重要性，最后调度压缩；每个模块都要有单元测试。"},
        {"role": "tool", "content": "run(pytest) -> 3 passed, 冒烟测试通过。"},
        {"role": "assistant", "content": "下一步接入真实 LLM：embedding 相关性打分 + 语义摘要压缩。"},
        {"role": "user", "content": "好，先完成规则版，再逐步替换为模型版。"},
    ]
    task_state = "设计面向长程 Agent 的自适应上下文压缩方案"

    pruner = ContextPruner()
    result = pruner.compress(turns, task_state=task_state)

    print(f"任务阶段: {result.phase.value}")
    print(f"Token: {result.tokens_before} -> {result.tokens_after}  "
          f"(压缩率 {result.compression_ratio:.1%})\n")
    print("各上下文块的处理：")
    for c in result.chunks:
        action = c.action.value
        imp = f"{c.importance:.2f}" if c.importance is not None else "-"
        preview = c.text[:34].replace("\n", " ")
        print(f"  [{c.ctype.value:<15}] imp={imp} act={action:<9} tok={c.token_count:<4} {preview}…")

    print("\n最终压缩后的 Prompt 内容：\n")
    for c in result.chunks:
        print(f"--- {c.ctype.value} ({c.action.value}) ---")
        print(c.text)
        print()


if __name__ == "__main__":
    main()
