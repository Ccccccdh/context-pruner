# V9 Django代码证据读取设计

日期2026-10-15。状态：按用户“先完成1、2步”完成书面设计；待后续审阅和实现。当前无V9产品代码、测试执行或API请求。设计范围是一个真实Python代码修复任务族的读取/生命周期/评分合同，不是通用代码代理平台。

## 目的与选择

验证V8按记录精简返回的思路能否迁移到真实代码修复；保留源版本、必要上下文、全文与行范围回退，独立评估正确补丁及端到端费用。目标仍为质量和成本，恢复为次要指标。

方案比较：①固定行窗口简单，但可能切断装饰器/方法结构；②AST定位函数/方法并返回原始代码块，加显式上下文/回退（选择），确定性、可审计、不额外调用模型；③模型摘要灵活，但新增成本和事实误差，不进入本候选。AST片段不被宣称语义必然完备。

## 现有代码依据与不能照搬之处

- .tooling/agents_repair_runner.py的read_range已经返回行范围，且采用自定义JSON消息循环；不能当作新全文对照或直接声称是新的原生SDK接入。
- .tooling/agents_w2_real_quality_endpoint.py的evaluate_candidate默认跑FAIL_TO_PASS及target_module回归；不是完整PASS_TO_PASS。run_regression=False时可能接受目标通过而未跑回归的结果，新真实验收不能使用该捷径。
- 旧read_range使用字符串startswith检查路径，V9使用解析后的路径包含关系，拒绝同名前缀旁路和越界链接。
- V8完整registry resolver校验text与整源hash，不能用它把代码片段当成全文。V9新证据类型/作用域回执单独设计，不修改冻结V8。

## 公平对照

| 组 | 历史处理 | symbol请求的返回范围 |
|---|---|---|
| C0 | 不裁剪 | 全文件，受共同大小限制 |
| C1 | 固定生命周期裁剪 | 同C0 |
| C2 | 与C1相同裁剪 | 请求函数/方法及明确标记的支持代码块 |

C1/C0研究本协议下裁剪，C2/C1研究返回粒度。三组使用同一任务、初始请求、模型参数、工具schema、工具描述、来源清单、工作区起点、费用和请求上限；组名/缓存origin不进入提示。归档后端替换不加入第四组，read_code始终读取当前源，归档仅服务历史裁剪和审计。

三组都有file/range/symbol读取与相同发现工具。range请求在三组均返回明确请求的行范围，file请求均按全文合同；只有symbol请求的返回范围为实验变量。模型可完全使用range/file，未使用symbol或未触发裁剪的样本仍保留。所谓“全文组”只是symbol请求的全文回传模式，不声称它每次都实际读了全文。

不为C1/C2隐藏资料，不为C0额外增加探路回合；超大全文明确报错并提供各组同样可用的范围读取入口，不能静默截断还标为完整。任务是否进入开发集按预先固定的环境/质量门决定，不能按哪一组更省钱或是否触发裁剪选任务。

## 模型可见工具

拟定统一接口（下面是合同，不是已存在函数）：

- list_source_files(prefix, cursor, limit)：只枚举允许的相对源码路径，分页默认32、最多64条，不返回隐藏材料。
- describe_module(path, cursor, limit)：当前source_version、函数/方法限定名、装饰器/定义行范围、导入位置及分页轮廓，默认32、最多64条，不含函数正文或参考修复线索。
- read_code(path, mode, symbol, first_line, last_line, expected_source_version)：mode为file/range/symbol；不适用字段传null；symbol必须为可唯一定位的限定名，range必须为1起始闭区间，不静默裁剪越界。
- close_research(summary)：可选，标记模型实际声明的研究结束，summary作为模型数据保留。
- submit_patch(diff)：一次终局提交unified diff；可在未close_research时提交，不能强迫先关研究或人为重读制造触发。

模型从一开始获得公开problem_statement，没有registry的延迟work_order。没有参考补丁、隐藏测试、期望符号或答案选择器。发现工具和选择器只能用当前允许读取的源码；不得从reference.patch/host-tests.patch/FAIL_TO_PASS推断“该看哪里”。

初始只允许任务模型工作区内django/下的普通Python源码。拒绝绝对路径、盘符/UNC、父目录越界、.git、凭据文件、隐藏/评测测试及指向工作区外的符号链接/junction。路径解析后用is_relative_to验证；所有工具均适用同一允许集合。

read_code单次响应JSON的UTF-8上限16KiB，range最多200行，完整源码解析输入上限1MiB。离线SDK最终请求体共同上限48KiB。超限返回明确READ_TOO_LARGE及允许的显式范围/轮廓回退，不擅自删行。后续付费请求数/输出token/总预算须另行固定，这些离线大小限制不是付费授权。

## 代码单元与上下文合同

仅静态解析，不import或执行仓库代码。使用声明编码严格解码，保留原行尾，不用errors=replace掩盖解码错误。

C2的symbol只定位函数、async函数或方法；限定名区分嵌套作用域，重复定义/条件分支产生歧义时返回AMBIGUOUS_SYMBOL。目标块包括全部装饰器、完整签名、docstring及函数体，直接取原文，不重写AST或生成“省略代码”。

支持块规则在三个任务之前固定：

1. 同文件直接相关的导入语句：目标及其装饰器/默认值/注解中静态引用的顶层绑定；不自动抓取第三方模块源码。
2. 同文件直接引用的顶层常量赋值原语句。若赋值过大，明确返回上下文超限，不只取变量名冒充足够证据。
3. 方法附带所在类的装饰器、类声明/基类/关键字；静态可识别的self/cls直接类属性赋值作为单独支持块。不能把整个类体自动展开成数万token。
4. 同文件其他函数、继承方法或闭包依赖只返回绑定位置/未解决依赖标识，正文需模型显式再次读取；动态属性、getattr、闭包/nonlocal等标记dependency_coverage=direct_static_only及需要的父作用域位置，不声称已完成依赖闭包。

模块docstring、导入、类头、赋值与目标代码分块标明role，重叠行去重、按原文件顺序输出；模型请求另一个symbol时重新确定作用域。class名本身不是“返回整类”的快捷方式，需describe_module后选方法或显式range/file。

AST无法解析、编码错误、未知/歧义symbol、参数矛盾、越界range、文件/上下文过大均有明确错误码；无参考修复驱动的自动换范围。range/file是各组同样可用的回退，错误也计入工具请求和最终成本。

## 响应、版本与完整性

新类型code_evidence_v9，不复用registry整源evidence语义。统一模型响应包含path、source_version（完整文件原始字节SHA256）、snapshot_id、requested_selector、returned_scope、selection_id、source_encoding、blocks和dependency_coverage。namespace只在宿主回执/审计中，不能将含臂名的sample_id送给模型。

每个block包含role、start_line、end_line、原样解码text及text_sha256（该text的UTF-8 SHA256）；content_sha256对移除content_sha256字段后的完整模型响应做规范JSON编码再hash，避免自引用。规范编码为sort_keys=True、ensure_ascii=False、separators=(',', ':')、allow_nan=False、UTF-8。selection_id按同样编码hash(path、source_version、requested_selector)，不能用含臂名的私有namespace改变它。回执身份还必须绑定宿主namespace、returned_scope和content_sha256，不能仅靠selection_id把选段当全文。

完整文件hash、片段hash与最终HTTP wirehash分别记录，不能互相替代。line范围绑定该source_version，避免把旧行号用于新文件。C0/C1全文和C2选段用同一字段结构，returned_scope如实表示file/range/symbol_bundle。空文件file读取可返回blocks=[]并正确标记整源空文件hash；range无有效行须报越界，symbol须报未知符号，不伪造第0行。

工作区启动时冻结允许源码的path→原始字节SHA256映射与正确base_commit，snapshot_id由公开instance_id、base_commit及该规范映射确定，三组相同；私有运行/臂标识不参与模型可见snapshot_id。每次读取都与此初始映射核对，不能在首次读取时把外部改动默认为新基准。

expected_source_version可为null以取得该已核验版本；非null必须匹配任务快照版本。首轮补丁修复期间模型工作区只读，submit_patch是终局，补丁在独立评分副本应用。因此本候选不提供边改文件边继续读的工具，也不混入增量修复循环。

若读前/读后发现文件内容变更、创建/删除、路径目标变化或版本不匹配：立即失效该文件全部symbol索引、作用域回执和归档引用；返回SOURCE_CHANGED/STALE_VERSION并按完整性故障停止当前样本及后续付费队列，不用旧归档掩盖变化。本设计不自动接纳新版本。将来若增加在线编辑，必须另定义宿主提交事务和snapshot世代推进后再开放读取，不能静默放宽本合同。

## 原生SDK与历史裁剪

拟新增原生OpenAI Agents SDK runner，复用既有最终请求转换/账本审计方法。旧自定义JSON修复runner只作评分桥接与历史参考，不把字符串消息过滤冒充SDK工具输入过滤。

V9 scoped-code receipt适配器接受本工具实际执行的code_evidence_v9回执，校验文件版本、selector、block/content hash及真实call_id；不能把部分文件登记成全文缓存。可复用完整原子配对、hash和预算帮助函数，但不直接将新类型塞入V8全文resolver。

C1/C2共同采用研究阶段结束后归档、相同TTL2的既有生命周期语义；阶段只能由实际close_research调用产生。成功read_code回执按当前研究/实施阶段登记，全文与片段均作为明确作用域证据参与同一策略。C0只保留相同来源元数据而不裁剪。配对调用/输出为不可拆单元；指令、公开issue、模型summary和补丁不作为可删代码块。最终预算核验在SDK投影之后执行。

模型可不关闭研究或直接提交补丁；未形成裁剪机会是合法结果，不强迫额外close/read。无新自主恢复工具、无伪造missing信号；本候选归档不用于read_code后端。阶段、可见片段、归档准备和成功HTTP投递分开记录。

## 质量端点与样本划分

新评分桥接在独立且有自身.git的工作区验收：确认HEAD==base_commit、无继承改动；统一规范化测试label。模型工作区不包含新增host-tests.patch，提交补丁不得修改评测器、隐藏材料或测试入口。

每任务预先固定并真实运行：FAIL_TO_PASS目标、完整指定PASS_TO_PASS集合、额外回归模块（可去重执行但逐端点保留记录）。通过条件为补丁可应用且所有要求集合通过；不接受run_regression=False或缺失回归。基线必须目标失败，参考修复必须目标/回归通过；空/乱码/无修改/错误补丁必须被拒收，回归注入破坏须能被检测。环境/超时错误与模型质量失败分开。

复用旧端点前须补齐其实际缺失的完整PASS_TO_PASS验收合同，不能仅凭旧docstring认定已覆盖。此变更只在新桥接中实现，不改冻结旧端点。评分子进程使用受限环境，不继承提供商密钥；评测输出不反馈给已终局提交的模型，隐藏材料不能进入出站请求。

所有副本使用新任务/臂/重复/评测角色唯一标识；共享upstream只读。创建/清理仅在明确归属的新目录内，确认路径和.git归属，不对用户当前checkout或共享旧任务目录执行reset/clean。旧helper的隐式复用/删除行为不能直接调用到未核验目录。

现有18个本地实例仅为历史开发素材，7个同名upstream有.git；目录/解释器存在不等于准入通过。本次没有选择付费任务，也未确认新留出可用。开发可从历史素材做工程门；未来确认按instance_id+base_commit及历史参与记录去重，不能把这些已见材料直接称为新留出。

## 后续第3步的交付门（本次不执行）

- 测试装饰器、async、同名方法、嵌套作用域、闭包、类头/属性、导入/常量、语法/编码异常、超大类及无歧义原文位置。
- 测试源码修改/创建/删除、旧行号、path前缀旁路和跨namespace失效；失效后不得返回旧缓存。
- 证明同请求下file/range的共同合同、symbol组间唯一差别；SDK初始输入/schema相同，函数调用配对、完整预算与真实投递审计通过。
- 在独立评分副本验证基线/参考/空补丁/错误补丁/回归破坏的判别力、完整PASS_TO_PASS及环境隔离，报告每个候选的门失败原因。
- 固定脚本只测工程字节变化和证据充分性，不假装等于真实模型修复质量或真实token；自然不使用symbol/不裁剪的情况计入完整分母。
- 全部零provider通过后，再另写小规模开发付费协议。真实代码失败率和费用波动重新决定确认方法/样本量，不照搬registry48实例全通过界，也不隐藏旧修复负结果。

## 文件职责与自查

拟新增context_pruner/code_evidence_v9.py（选择/版本合同）、context_pruner/adapters/openai_agents_code_evidence_v9.py（scoped回执与生命周期）、experiments/runners/run_agents_code_evidence_v9.py（SDK/工具/账本）、experiments/audits/agents_code_quality_v9.py（严格端点评分桥接）及相应测试/审计。当前这些文件未创建。

已自查：对照分离、旧工具真实语义、整源/选段hash区分、原样代码与有限静态依赖、共同回退、可选阶段无强制重读、版本失效、完整P2P与回归、历史样本去重和预算边界一致。新宿主接口需实现后验证，未将设计描述当运行结果。

本设计为用户第2步的可审阅产物；本次无产品代码、环境安装、补丁应用、测试运行、真实API、实现计划或新付费claim。后续实现前审阅此合同，再制定执行计划。
