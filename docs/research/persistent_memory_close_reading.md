# persistent-memory 设计文档精读笔记（逐节）

> **精读对象**
> 1. [研究与推荐设计](./persistent_llm_memory_system_research.md)（562 行 / 17 节，2026-10-27）
> 2. [详细技术设计](./persistent_llm_memory_system_technical_design.md)（1368 行 / 30 节，2026-10-28）
>
> 本笔记是**逐节精读**而非摘要：每节固定回答三个问题——**讲了什么 / 要记住的细节 / 批注**。
> 第三部分把研究结论映射到技术设计章节与当前代码，第四部分是精读后的评价与待核对清单。
> 实现信息以仓库 [README](../../persistent-memory/README.md) 和源码模块名为准；未读代码实现细节处标注"待核对"。

## 阅读说明

- 两份文档是"为什么这么设计 → 具体怎么落地"的上下游：研究文档管**选型与约束**，技术设计管**接口、数据、流程、阶段**。
- 阅读顺序建议：研究 §1（结论）→ 技术设计 §1（决策）→ 研究 §2–§3（前提与竞品）→ 研究 §5–§13 → 技术设计 §4–§26 → 研究 §14 / 技术设计 §24（路线图）→ 技术设计 §29–§30（前置条件）。
- "§"指原文小节，标题后行号链接可跳回原文。

---

# 第一部分　研究文档逐节（17 节）

### §1 结论先行 [L8](./persistent_llm_memory_system_research.md#L8)
- **讲了什么**：直接给出一套推荐架构——事件溯源 + 五层派生记忆 + 混合检索 + 双路径写入 + 可撤销治理。
- **要记住的细节**：八条主张里最关键的是前三层分离（原始事件是证据，不是提示词；上下文 / 记忆 / 授权分离）、PostgreSQL 唯一事实源、情感只能作辅助信号、更正不是覆盖、遗忘不是立刻删、先建评估集再加智能。
- **批注**：这八条几乎每条都对应后文一个专章，是全文的骨架；如果只读一节，读这节，再读 §2 和 §17 的"证据边界"。

### §2 先纠正五个错误前提 [L23](./persistent_llm_memory_system_research.md#L23)
- **讲了什么**：拆掉"上下文不够长""情绪越强越该记""存得越多记得越好""摘要等于事实""新的总是真的"这五个常见设计前提。
- **要记住的细节**：§2.1 把问题拆成窗口容量 / 长上下文有效利用 / 跨会话状态三层，并指出 LongMemEval 评的是信息抽取、跨会话推理、时态推理、知识更新和**拒答**；§2.2 强调 Generative Agents 的 poignancy 分数只在模拟环境中有效；§2.4 要求派生记忆保存来源事件、生成模型/策略版本和置信度。
- **批注**：这是全文"反直觉价值"最高的一节——很多记忆产品失败恰好因为只解决了 §2.1 的容量层，却没解决"正确使用、辨别新旧"。

### §3 方案比较与共识 [L60](./persistent_llm_memory_system_research.md#L60)
- **讲了什么**：横向比较 11 个方案，逐个列出"可迁移价值 / 不可照搬部分 / 工程判断"。
- **要记住的细节**（可按此表复习）：

| 方案 | 精读结论 |
|---|---|
| Generative Agents | 多信号检索 + 反思引用证据可借鉴；0.995 衰减、150 阈值、等权重都来自模拟环境 |
| MemGPT / Letta | 分层上下文、受控换页可借鉴；完整 runtime 会改变 Agent 运行方式 |
| LangGraph Store / LangMem | 热路径工具写入 + 后台巩固的双路径最值得参考；是原语库，不自带完整治理 |
| Mem0 | ADD-only 抽取、语义+BM25+实体多信号；托管基准含专有优化，不能外推 OSS |
| Zep / Graphiti | 双时态失效边、episode/实体/事实三层图最完整；运维成本高，参考服务无生产级认证授权 |
| A-MEM | 原子 note + 动态链接值得借鉴；缺不可变来源时"演化旧记忆"会扩散错误 |
| HippoRAG | 图 + Personalized PageRank 面向多跳；偏外部文档整合，缺用户画像与删除治理 |
| MemoryBank | 衰减与回忆强化可用于排序实验；遗忘公式是探索性简化，不能当删除规则 |
| Emotional RAG | 证明情绪可参与检索匹配，但评估目标是人设一致性，不是长期保存决策 |
| 2026 多因素价值模型 | 情绪只是七因素之一、权重应从下游任务学习；仍是研究原型，不能替代权限硬门 |
| Reflexion | 只适合任务复盘与程序性经验；不是通用用户记忆 |

- **批注**：这节的方法论比结论更值钱——每个方案都明确区分"机制可迁移"与"数字不可照搬"，这是后续所有设计取舍的信用来源。§3.2 列出的"尚未解决问题"（什么值得记住无通用权重、情感分类跨语言不稳、自动反思会固化推断）应在实现时持续回看。

### §4 推荐系统边界 [L98](./persistent_llm_memory_system_research.md#L98)
- **讲了什么**：目标六条 + 非目标五条。
- **要记住的细节**：目标是"可量化准确率、成本、延迟、安全性和删除完整度"；非目标明确排除完整人类认知模拟、全量永久保存、模型自改权限、首版建通用知识图谱。
- **批注**：非目标写得比目标更严格，尤其"不把微调当可删除可审计的个人记忆"——这条在技术上排除了"用微调做记忆"的捷径。

### §5 五层记忆模型 [L117](./persistent_llm_memory_system_research.md#L117)
- **讲了什么**：L0 原始事件 / L1 工作记忆 / L2 情景 / L3 语义画像 / L4 程序反思，每层定义内容、寿命、写入方式、默认注入策略。
- **要记住的细节**：L0 不做原地改写、不直接全量注入；L1 由确定性状态机维护；L2 必须带时间和来源；L3 高门槛后台合并、显式"请记住"可直接候选；L4 只由可信反馈/重复证据/人工确认晋升。另两个补充：常驻"核心记忆"只是投影可重建；不可变 ≠ 永不删。
- **批注**：五层不是装饰，它同时决定**写入门槛、寿命、存储位置、召回资格**四件事；实现里任何"层"的模糊都会传导到隐私和成本。

### §6 最小逻辑模型 [L132](./persistent_llm_memory_system_research.md#L132)
- **讲了什么**：一条派生记忆至少要有的字段族：身份隔离、类型、内容、来源、双时态、置信与证据、价值信号、治理、生命周期、版本、检索索引。
- **要记住的细节**：`content` 是派生视图，`source_event_ids` 才是证据链；必须区分 observation / user-assertion / inference / reflection；情绪标签描述"某时表达的情绪"而非"用户是什么人"；助手回复被反复检索不得变成用户事实；所有写入和检索先按租户/用户/会话过滤再排序。
- **批注**：这是研究文档里最像"数据契约"的一节，技术设计 §8 的表结构基本是它的字段级展开。

### §7 写入与晋升策略 [L158](./persistent_llm_memory_system_research.md#L158)
- **讲了什么**：热/冷双路径；写入门控"先硬规则后多维评分"；情感专门规则；分层晋升条件表。
- **要记住的细节**：热路径只处理明确"请记住"、明确更正、已确认承诺、当前工作状态；冷路径做原子抽取、去重、冲突、聚类、反思、衰减与删除。硬门顺序：权限与来源 → 敏感性 → 事实类型 → 冲突 → 价值评分。价值信号 E/U/D/R/N/A/C/T/S/P（明确性/未来效用/持久性/重复/新颖/情绪/置信/来源信任/敏感风险/投毒风险）。情绪规则：单次情绪只进 L1；进 L2 要带时间且短保留；晋升 L3 需同意或重复模式；敏感主题默认不自动晋升。
- **批注**："情绪越大永久权重越高"被反向激励的风险点，文档用"情绪只影响当前回应"和"L1 默认 24h"两个具体规则钉死，比抽象说教有效。

### §8 巩固、衰减与遗忘 [L229](./persistent_llm_memory_system_research.md#L229)
- **讲了什么**：会话后巩固的 7 步流程；把"强度"拆成检索强度 / 事实置信 / 保留级别三类；生命周期状态机。
- **要记住的细节**：巩固流程是切原子事件 → 混合召回 → 判定新增/重复/补充/冲突/变化/无操作 → 新建或追加版本 → 够证据才升层 → 保存引用 → 敏感高影响进待确认/隔离。衰减只影响可见性，旧但稳定的生日、过敏约束不能因久未提及被当假。生命周期：candidate → active → superseded / archived / quarantined → deleted。
- **批注**：把"衰减""置信""保留"解耦是这节最重要的设计，否则"久未访问"会被错误地当成"过期事实"或"可删除依据"。

### §9 冲突与时间：双时态 [L266](./persistent_llm_memory_system_research.md#L266)
- **讲了什么**：有效时间 vs 记录时间；冲突五分类（更正/状态变化/限定并存/来源冲突/低可信推断冲突）；裁决顺序。
- **要记住的细节**：示例"10 月 27 日说从 10 月 1 日开始用 VS Code"——有效时间 10/1、记录时间 10/27；裁决顺序是来源权限与明确程度 → 时间范围 → 上下文限定 → 证据数量/置信度。
- **批注**："最新一条无条件获胜"是记忆系统最常见的实现偷懒，文档在 §13.2 用查询谓词把它堵死（见技术设计部分）。

### §10 检索与上下文组装 [L285](./persistent_llm_memory_system_research.md#L285)
- **讲了什么**：十步召回流程；混合召回（向量/全文/精确字段/时间/最近情景）；RRF 融合；重排；证据打包；回答后记录。
- **要记住的细节**：权限、来源信任、隔离、敏感性是**前置门**，不是能被相似度抵消的负权重；查询要按目的区分（当前状态/历史/偏好/关系/多跳/程序经验）；证据包要带原子记忆、时间、置信和引用 id。
- **批注**：这节把"检索"定义成"受控上下文组装"而不是"相似度搜索"，是后续 §14 技术流程的直接来源。

### §11 存储与服务拓扑 [L304](./persistent_llm_memory_system_research.md#L304)
- **讲了什么**：首版 PostgreSQL + pgvector + 全文检索；什么条件下才升级图后端；不建议的首版做法。
- **要记住的细节**：升级条件五条——多跳是核心流量、PG 关系表 recall@k 不达标、图收益覆盖建图成本、实体解析与时态删除可控、完成隔离与注入防护。特别点名 Graphiti 参考 MCP/REST 服务没有认证授权和注入防护，不能直接暴露公网。
- **批注**：这节给"什么时候才允许增加基础设施"写了可证伪的门槛，比"以后可能需要"理性得多。

### §12 隐私、删除与安全 [L339](./persistent_llm_memory_system_research.md#L339)
- **讲了什么**：隐私默认值；七步删除协议；记忆投毒威胁模型与十项最低防线。
- **要记住的细节**：删除必须覆盖派生摘要、embedding、全文索引、关系边、缓存和备份到期，并用 canary 查询验证不可再召回；投毒的核心是"一次恶意输入变成跨会话控制通道"；防线从来源绑定、写入门、读取门、权限分离到白名单、再确认、审计、限流、隔离、攻击评估。
- **批注**：隐私默认值里"分类同意"（偏好/长期目标/敏感经历/第三方信息分别控制）是最容易被产品简化掉、后果又最重的一条。

### §13 评估体系 [L381](./persistent_llm_memory_system_research.md#L381)
- **讲了什么**：公共基准（LongMemEval 500 题五能力、LoCoMo、LongMemEval-V2、投毒集）；必须自建的中文金标场景；七阶段指标分层；六条对照基线；准入线。
- **要记住的细节**：必须分别报告"只检索对不对""拿到正确证据后回答对不对""端到端对不对"，否则检索失败、读取失败和幻觉会混成一个分数；每次加机制都要与最近 N 轮/摘要/仅向量/向量+全文+时间/完整方案比较。
- **批注**：§13.2 的十一条自建场景（含反讽、否定、第三人隐私、网页夹带指令、模型错误回写、删除后改写查询不可召回）实际上就是测试用例清单，技术设计 §26 直接继承了它。

### §14 分阶段落地路线 [L438](./persistent_llm_memory_system_research.md#L438)
- **讲了什么**：阶段 0 定义与基准、阶段 1 最小可信记忆、阶段 2 后台情景与时间、阶段 3 受控画像与程序记忆、阶段 4 按证据决定是否上图，每阶段带退出条件。
- **要记住的细节**：阶段 0 就要冻结记忆类型、来源等级、敏感分类、双时态与删除语义，并建 200–500 条中文金标+攻击集；阶段 1 只用显式记忆+混合检索，不启用情绪自动长期化、自动反思、图库；阶段 2 先 shadow 让人审查"本来会保存什么"。
- **批注**：路线图的价值在于"延迟昂贵且难治理的自动反思和图结构"，先建可纠正、可删除、可评估的底座——这与当前实现把 embedding 也延后的做法同源。

### §15 推荐决策 [L489](./persistent_llm_memory_system_research.md#L489)
- **讲了什么**：采用清单、暂不采用清单、必须由产品方确认的九个变量。
- **要记住的细节**：采用 PostgreSQL+pgvector+FTS、五层、双路径、双时态、情绪弱辅助、显式优先、硬门前置、用户可查看更正删除、先 shadow 再灰度、用 LongMemEval/LoCoMo+中文集+投毒集评估。产品变量包括 opt-in 方式、保留期、敏感类别、能否关情绪分析、延迟成本、备份删除窗口、家庭/团队身份边界、跨 Agent 共享、冲突何时问用户。
- **批注**："设计可以给默认值，但业务决策不能由模型替代"——这句话建议原样写进产品评审纪要。

### §16 来源索引 [L528](./persistent_llm_memory_system_research.md#L528)
- **讲了什么**：论文/仓库/安全基准分组索引，并声明所有链接于 2026-10-27 核对、落地前应固定 commit/tag。
- **要记住的细节**：来源覆盖基础架构（Generative Agents、MemGPT、MemoryBank、Emotional RAG、2026 多因素模型、Mem0、Zep、A-MEM、HippoRAG、Reflexion、LangMem）和评估安全（LongMemEval、LoCoMo、V2、Hidden in Memory、MemGate、OWASP、MPBench）。
- **批注**：无批注，按引用核对即可。

### §17 证据边界 [L556](./persistent_llm_memory_system_research.md#L556)
- **讲了什么**：自我限定——没跑任何仓库或重现实验；成熟度来自静态核对；各项目数字不可横向排名；2026 安全论文需独立复现；情绪驱动长期记忆缺真实用户证据，限制为弱信号是风险控制下的工程判断。
- **要记住的细节**："低后悔架构选择不应等攻击复现后才补"——来源绑定、最小权限、可撤销、隔离属于此类。
- **批注**：这一节让研究文档的可信度明显高于一般"综述+推荐"；精读时它也应该反向约束我们对文档其余部分的期待。

---

# 第二部分　详细技术设计逐节（30 节）

### §1 最终设计决策 [L11](./persistent_llm_memory_system_technical_design.md#L11)
- **讲了什么**：首版是一个独立的 `memory_system` 深模块；调用方只理解四个动作；PostgreSQL 唯一事实源；pgvector 语义 + 全文关键词；outbox 驱动后台；首版不上 Neo4j/Graphiti/Milvus/消息队列。
- **要记住的细节**："这不是把所有历史放进向量库——原始事件是证据，记忆是带版本和来源的派生状态，提示词上下文只是一次受控投影。"
- **批注**：这句话是整套设计的浓缩；读后续所有细节时都可以拿它检验自己有没有跑偏。

### §2 前提、默认值与非目标 [L33](./persistent_llm_memory_system_technical_design.md#L33)
- **讲了什么**：可信身份来自认证而非自然语言；模型输出必须 schema 校验；显式删除/纠正优先级最高；默认值表；非目标。
- **要记住的细节**：L0 30 天、L1 72 小时或任务结束、L2 180 天归档复核、L3 到更正/撤销、L4 每 90 天或模型升级后复核、高敏感默认不自动升 L3/L4、凭证永不进长期记忆；默认值是设计起点，须产品/法务/安全共同确认。
- **批注**："记忆不可授予工具权限，也不可降低系统安全策略"放在前提里而非安全章，说明它被当成系统不变量。

### §3 统一术语 [L69](./persistent_llm_memory_system_technical_design.md#L69)
- **讲了什么**：12 个术语的精确定义，把容易混的概念分开：事件/候选/记忆/画像/反思、强度/置信/重要性、有效时间/记录时间、删除。
- **要记住的细节**：强度只影响检索可见性，不代表事实真假；置信不代表授权；重要性不代表敏感内容可以保存；删除 = 原文及一切派生副本进入不可召回级联状态。
- **批注**：术语章是本文档的"防歧义层"，实现评审时应直接引用这里的定义，而不是口头约定。

### §4 系统拓扑 [L85](./persistent_llm_memory_system_technical_design.md#L85)
- **讲了什么**：上层产品 → HTTP API → MemorySystem → PostgreSQL；outbox → worker；管理界面；指标与审计。
- **要记住的细节**：HTTP adapter 只管认证、DTO、错误映射；业务规则全部在 MemorySystem；模块不依赖具体 Agent/编排框架；在线与后台共享同一套策略与事务；权限过滤在数据库和应用两层；模型是提取和排序工具，不是事实源/授权源/事务协调器。
- **批注**："模型不是事务协调器"排除了让 LLM 决定写入顺序的常见诱惑。

### §5 深模块 interface [L112](./persistent_llm_memory_system_technical_design.md#L112)
- **讲了什么**：四个公开方法（record_event/build_context/execute_command/list_memories）及非类型契约；六种错误模式。
- **要记住的细节**：所有调用必须带可信 scope；record_event 靠 idempotency_key 幂等；build_context 只返回本 scope 可读内容且是 data-only（不能拼进 system instruction）；forget 先同步隔离再异步清理；不得静默声称"已记住/已删除"；数据库不可用时显式命令必须失败可见。错误包括 InvalidMemoryScope、MemoryUnavailable、MemoryPolicyRejected、MemoryConflictNeedsConfirmation、MemoryDeletionInProgress、MemoryBudgetExceeded。
- **批注**：六种错误模式里"显式命令不许假装成功"最容易被工程实现破坏（吞异常、先返回后写库），也是验收时必须测的。

### §6 建议目录和职责 [L155](./persistent_llm_memory_system_technical_design.md#L155)
- **讲了什么**：目标目录树（api/bootstrap/system/models/policy/retrieval/consolidation/deletion/postgres/llm/worker/settings/prompts）与"删除测试"论证深模块的必要性；反对按评分特征拆服务。
- **要记住的细节**：删除测试——删掉 memory_system 后事件、检索、冲突、删除、安全逻辑会散落回所有 Agent，所以值得独立；`retrieval.py` 等只是内部组织，不对外形成新 interface。
- **批注**：与当前仓库对照，实际实现没有 `deletion.py` 和 `prompts/` 目录（删除相关在 system/postgres/worker，抽取逻辑在 consolidation/llm，见第三部分）。这属于实现收敛而非原则违背，但文档值得回填。

### §7 领域类型代码框架 [L198](./persistent_llm_memory_system_technical_design.md#L198)
- **讲了什么**：公开 DTO、显式命令、内部候选三组类型。
- **要记住的细节**：公开 InteractionEvent **不接受** trust_level/sensitivity/importance——这些只能由可信通道和内部策略产生；MemoryLayer 枚举只有 L1–L4（L0 是事件不是记忆）；RecallRequest 带 purpose 与 token_budget（≤4096）；删除/忘记必须引用稳定 ID 或界面确认候选，自然语言"忘了它吧"不允许模型猜范围；候选只是提案，policy 可降级/缩短 TTL/要确认/隔离/拒绝，模型不能直接 INSERT。
- **批注**："能否由调用方抬高可信度"是接口设计的红线，实现里应重点核对（README 说明 purpose 与 memory_write 来自 token，符合该原则）。

### §8 PostgreSQL 逻辑 schema [L362](./persistent_llm_memory_system_technical_design.md#L362)
- **讲了什么**：8.1 事件表（密文、hash、幂等唯一、trust/sensitivity/consent、deleted_at）；8.2 派生记忆表（层、状态、双时态、expires、置信、importance、强度、affect、治理、supersedes_id、policy/model 版本、embedding 1536、tsvector、CHECK 约束）；8.3 证据与关系表（固定五种关系）；8.4 outbox/audit/deletion_job；8.5 索引与 RLS。
- **要记住的细节**：`statement` 必须原子且带限定（"用户在工作电脑上偏好深色主题"好；"喜欢深色而且最近压力大可能内向"差，因为混了偏好/状态/推断，无法独立更正删除）；高敏感内容用密文且不建全文/向量索引，只按稳定 ID 或受控键召回；embedding 模型升级要新列/新表双写回填，不在同索引混维度；审计表禁止存原文/embedding/完整提示词；RLS 是第二道防线，应用层仍要带 scope。
- **批注**：§8.2 的 CHECK 约束和 §8.3 的固定关系类型，是"防止模型动态创造结构导致查询与删除语义失控"的具体手段，实现时应视为硬要求。

### §9 记忆层级与生命周期 [L524](./persistent_llm_memory_system_technical_design.md#L524)
- **讲了什么**：五层在实现中的落点（L0=event 表；L1=thread/checkpoint+短 TTL item；L2–L4=item 表）；状态机（Candidate/Active/Superseded/Archived/Quarantined/Deleted + deletion_pending）。
- **要记住的细节**：上层产品的 session/thread state 只对应 L1 的一部分，不能把会话状态当跨会话 L2–L4；物理删除不是普通状态转换，Deleted 表示在线不可见且后台级联清理完成，清理前是 deletion_pending。
- **批注**：§9.1 表格把"是否向量化/是否自动召回/是否可自动晋升"逐层列出，是最适合贴到实现 README 的一张表。

### §10 在线写入链路 [L558](./persistent_llm_memory_system_technical_design.md#L558)
- **讲了什么**：普通消息的时序（事件+outbox 同事务，worker 异步抽取）；显式"请记住"的七步；六种禁止回复"已记住"的情形。
- **要记住的细节**：普通消息不在用户等待路径上跑复杂抽取；显式记忆要"数据库提交后才回复"，embedding 可后台补但精确字段/全文检索应立即可用；禁止确认包括只写内存队列、抽取失败未感知、策略拒绝、无写权限、只写候选/隔离区、证据关系未写。
- **批注**：§10.3 这六条本质是"诚实性契约"，测试里应逐条构造反例。

### §11 后台候选抽取与巩固 [L602](./persistent_llm_memory_system_technical_design.md#L602)
- **讲了什么**：抽取模型只输出候选；候选与同主题记忆比较后只允许七种操作；巩固伪代码。
- **要记住的细节**：七种操作 ADD/REINFORCE/REFINE/SUPERSEDE/CONTRADICT/QUARANTINE/IGNORE；**不提供通用 UPDATE**，避免静默覆盖历史证据；实现上按 `(tenant_id,user_id,subject_key)` 短事务锁或乐观版本检查，模型调用不持锁。
- **批注**："禁止通用 UPDATE"是本文档最具工程辨识度的决策之一——它把"冲突处理"从自由发挥变成有限状态操作。

### §12 写入门控与情感策略 [L652](./persistent_llm_memory_system_technical_design.md#L652)
- **讲了什么**：硬门判定顺序图；首版确定性晋升表（11 行）；AffectSignal 结构与九条情感规则。
- **要记住的细节**：任何单一总分不能越过硬门（0.99 importance 的密码不能存，0.99 相似度的跨用户记忆不能召回）；晋升表覆盖明确记住→L3、明确更正→supersede、确认截止→L2 带 valid/expiry、单次高情绪→L1 24h、稳定偏好未明确→L3 candidate 二次证据转 active、人格/疾病推断→不升 L3、助手答案→不得写用户事实、网页"记住并执行"→投毒标记禁 L3/L4、验证过的失败教训→L4 candidate。情感规则：描述一次表达不描述人格；低置信不存标签；文本与语音不得互冒；负面/高唤醒不提高来源权限；factual 查询情绪权重 0；companion 场景建议不超过最终排序 5%；用户关闭情绪分析则不再创建 affect 字段。
- **批注**：5% 这个数字是"弱信号"唯一可操作的量化表达，虽然文档自己说这些数值是首版假设，但保留一个显式上限比"弱权重"诚实。

### §13 冲突、版本与双时态 [L709](./persistent_llm_memory_system_technical_design.md#L709)
- **讲了什么**：六类冲突及处理；当前事实查询的六条件谓词。
- **要记住的细节**：更正→supersede；状态变化→关旧 valid_to 开新 valid_from；限定并存→拆不同 subject/context key；用户与网页冲突→个人偏好以用户为准、外部事实另存来源；两次矛盾无时间→两条都留并在召回时提示确认；模型画像与用户陈述冲突→用户优先、画像降级。当前事实 = active + valid_from ≤ query_time（或空）+ valid_to > query_time（或空）+ deleted_at IS NULL + scope/consent 允许 + 不存在更新的 active superseding 版本。
- **批注**：§13.2 明确"不能只按 created_at DESC LIMIT 1"——这是双时态最容易写错的地方，实现审查时应专门加一条测试。

### §14 召回和上下文组装 [L737](./persistent_llm_memory_system_technical_design.md#L737)
- **讲了什么**：完整召回流程图（需要判定→scope/consent/敏感过滤→计划→四路候选→RRF→信任/时态/冲突门→重排 top12→token 打包）；排名顺序；初始 token 预算；data-only 渲染格式。
- **要记住的细节**：候选量是首版假设（向量 top40、全文 top40、精确/当前事实 top20、最近情景 top20）；预算表（核心画像 256–512、L1 256–768、L2/L3 512–1536、L4 256–768、元数据占 10%–20%）；默认总记忆上下文不超过窗口 20%–30%；超预算先删重复与低置信项，**不压缩来源、否定词、时间和限定条件**；渲染用 `<retrieved_memories authority="data-only">`，但标签不能替代真正的权限控制。
- **批注**：这是全文档信息密度最高的一节；"超预算时删什么"这条规则直接决定回答质量上限，实现时应把它写成显式排序策略而非随手截断。

### §15 上层产品接入协议 [L800](./persistent_llm_memory_system_technical_design.md#L800)
- **讲了什么**：一次 turn 的推荐调用顺序（记录用户事件→构建上下文→调模型→工具→记录工具→模型→记录助手事件）；上层只保留 TurnMemoryState；哪些对象不能作为会话状态持久化。
- **要记住的细节**：不能持久化 MemorySystem 实例、数据库连接、模型/embedding client、锁队列协程、权限对象、未加密长期记忆；每次请求用短期 token，记忆系统从 token 重建身份与用途。
- **批注**：这节实际在给 Agent 框架作者写"集成纪律"；违反其中任何一条（尤其持久化 client 与权限对象）都会把记忆的隔离性拖回框架层。

### §16 独立 HTTP interface [L865](./persistent_llm_memory_system_technical_design.md#L865)
- **讲了什么**：五个端点；JWT 与请求元数据；示例请求；状态码与幂等语义；create_app 代码骨架。
- **要记住的细节**：端点是 events / context:build / commands / items / deletions/{id}；HTTP adapter 必须从 token 建 scope，body 里 scope 只能做一致性检查；写请求必须带 Idempotency-Key，每个响应带 X-Memory-Diagnostics-Id；状态码 201/200/409/202/403/422/503 各有严格语义（同 key 不同 payload 是 409，越权 403 不透露目标存在，显式命令依赖挂掉必须 503 不得假装成功）；只有 bootstrap 能创建连接池/client/app。
- **批注**：§16.3 这张表几乎是可以直接转成契约测试的规格。

### §17 删除协议 [L944](./persistent_llm_memory_system_technical_design.md#L944)
- **讲了什么**：同步阶段五步、后台阶段流程图（派生闭包→再确认不可召回→删密文/记忆/embedding/FTS/关系/缓存→记录备份到期→canary）。
- **要记住的细节**：派生画像若同时由其他独立证据支持，不能整条删除，应重新生成不含待删证据的版本；canary 覆盖改写查询与精确 ID；仍可召回则告警失败。
- **批注**：这是"删除型记忆系统"最容易被低估的工程；canary + 备份到期时间 + 审计证明三件套缺一不可。

### §18 安全设计 [L970](./persistent_llm_memory_system_technical_design.md#L970)
- **讲了什么**：来源权限矩阵（六种来源 × L1–L4 是否可写）；十项关键控制。
- **要记住的细节**：网页/文档可进 L1、只能作外部资料进 L2、不得写 L3 用户画像、不得写 L4；助手输出不得成为用户事实；管理员可写只读组织规则；RLS 与应用 scope 双重隔离；embedding 也是敏感数据；高风险工具每次按当前授权确认；记忆文本不得进 system/developer 区；可疑写入 quarantine 不参与召回。
- **批注**：矩阵里"助手输出 L3=不得成为用户事实"是最反直觉的一格——很多产品会让助手总结自然沉淀成用户画像，这里明确禁止。

### §19 一致性、并发和重试 [L996](./persistent_llm_memory_system_technical_design.md#L996)
- **讲了什么**：必须原子提交的四组事务边界；幂等五类；并发控制规则。
- **要记住的细节**：event+outbox 原子；item+evidence+audit 原子；supersede 三件套原子；deletion_pending+job+audit 原子；模型调用在事务外；apply 前重读版本；同 subject_key 短 advisory lock 或乐观版本；worker 用 FOR UPDATE SKIP LOCKED；结构化输出失败有限重试后死信，不允许无限 LLM 重试。
- **批注**："模型调用不持锁"与"embedding 异步但 item 仍可用"两条，决定了系统 P95 和可用性下限。

### §20 降级与故障处理 [L1026](./persistent_llm_memory_system_technical_design.md#L1026)
- **讲了什么**：六类故障 × 普通对话/显式命令/后台任务三列的降级矩阵。
- **要记住的细节**：PG 挂→普通对话进无长期记忆模式并记指标、显式命令明确失败；embedding 挂→用全文/精确/时间；抽取模型挂→不创建候选，显式低敏感命令可走确定性写入；重排挂→RRF 基础排序；冲突解析挂→返回双方证据询问用户；删除 worker 挂→目标仍保持不可召回。
- **批注**：最后一句"涉及'你还记得什么/请记住/请忘掉'的请求必须披露真实状态"应写进上层产品的 UX 规范，而不是只留给后端。

### §21 可观测性 [L1039](./persistent_llm_memory_system_technical_design.md#L1039)
- **讲了什么**：12 个核心指标（写入/召回/候选决策/冲突/后台延迟/删除/canary/跨 scope 拦截/投毒标记/模型 token）；一次 build_context 的 trace 字段。
- **要记住的细节**：trace 记录 diagnostics id、scope hash、各通道候选数、过滤原因、最终 memory ids、token/耗时/降级、policy/model/index 版本；默认日志不放完整记忆与 prompt，排障走受权限控制、短期保留的安全通道。
- **批注**：指标名本身就能反推系统关键路径；memory_deletion_canary_failure_total 这种指标的存在说明删除是持续验证项而非一次性动作。

### §22 性能和成本预算 [L1073](./persistent_llm_memory_system_technical_design.md#L1073)
- **讲了什么**：SLO 起点表；成本公式；首版省钱六步。
- **要记住的细节**：普通事件写入 p95 ≤100ms、显式写入 ≤300ms、无重排召回 ≤200ms、带重排 ≤500ms、后台候选可见 95% ≤5min、删除在线隔离 ≤300ms、删除主存储完成 95% ≤24h、跨 scope 泄漏 0；省钱顺序：无价值 turn 不抽取 → 会话结束批量抽取 → 先规则后模型 → 候选多/查询复杂才重排 → 不为全部 L0 建 embedding → 定期归档低强度 L2。
- **批注**：成本公式把"抽取触发率"和"重排触发率"作为独立变量，提示产品应先调触发率而不是换模型。

### §23 独立项目技术栈 [L1111](./persistent_llm_memory_system_technical_design.md#L1111)
- **讲了什么**：技术选型表、pyproject 骨架、进程部署拓扑、Settings 配置骨架。
- **要记住的细节**：Python 3.12 + FastAPI/Uvicorn + Pydantic v2 + SQLAlchemy 2 async/psycopg3 + Alembic + PG/pgvector/FTS + HTTPX + structlog + prometheus-client + KMS；不引 Redis/Kafka/Celery/Milvus/ES/Neo4j/K8s operator；最小生产 = 2 个无状态 API + 1–N worker + HA PostgreSQL + 模型网关 + KMS + 指标日志；无独立 scheduler，worker 用 advisory lock 选临时 leader；策略/敏感分类/晋升规则不能全做成随意环境变量，要版本化发布并记录 policy_version。
- **批注**："策略不进环境变量"这条在实践中经常被违反，结果是上线后没人知道某条记忆为什么被保存或删除。

### §24 分阶段实现计划 [L1212](./persistent_llm_memory_system_technical_design.md#L1212)
- **讲了什么**：Phase 0–5 及各自退出条件。
- **要记住的细节**：Phase 0 冻结契约/金标/基线，不接真实 Agent；Phase 1 显式记忆闭环（不自动抽取、不做向量检索）；Phase 2 混合召回 + 接入试点产品；Phase 3 后台抽取 + shadow mode + 人工审核；Phase 4 冲突/巩固/程序记忆；Phase 5 只有证据支持才做图检索、多语言 SDK、多 Agent 共享等。
- **批注**：Phase 0 的退出条件"不同评审者对同一条语句是否应保存/存哪层/何时失效有可接受一致性"极其关键——门槛不一致，模型学不会，评测也没法判对错。

### §25 验收矩阵 [L1273](./persistent_llm_memory_system_technical_design.md#L1273)
- **讲了什么**：功能 7 条、安全 6 条、质量 5 条。
- **要记住的细节**：功能要求"以前 X 现在 Y"在当前/历史查询返回不同版本、更正保留历史但默认只用当前、不知道时拒答、删除后精确/语义/改写/关系查询均不可恢复；安全要求跨租户查询 0、网页指令不能创建 L3/L4、记忆不能改工具权限、高风险动作仍需确认、embedding/日志/缓存/备份纳入删除检查、刷高情绪不能无限提升排名；质量要求分层报告、固定回答模型/judge/预算/数据版本、外部 benchmark 只作参考。
- **批注**：安全第 6 条（大量重复高情绪输入不能无限提升权限或排名）是防"记忆投毒 + 情绪操纵"组合攻击的关键验收点。

### §26 测试策略 [L1302](./persistent_llm_memory_system_technical_design.md#L1302)
- **讲了什么**：最小测试组合七层；反 mock 原则。
- **要记住的细节**：策略表驱动测试、PG 集成（事务/RLS/幂等/双时态/索引过滤）、删除集成（闭包/embedding/FTS 清理/canary）、模型输出契约（非法 JSON/越权字段/低置信/冲突）、Agent 端到端（记录→召回→更正→历史→删除）、投毒红队（用户/工具/网页/助手回写四渠道）、评估脚本（LongMemEval/LoCoMo 子集+内部中文金标）；主要测试面应与调用方相同：四个公开方法 + 真实临时 PG。
- **批注**："不对每个内部函数大量 mock"是对"测试看起来多但没测到契约"的纠偏；当前仓库 tests/ 目录结构与这七层大体对应。

### §27 关键风险和缓解 [L1316](./persistent_llm_memory_system_technical_design.md#L1316)
- **讲了什么**：十项风险及缓解：模型误提取、情感显著性偏差、自动反思自我强化、旧事实覆盖新事实、新事实无条件覆盖、向量近邻越权、删除不完整、后台成本失控、多库双写不一致、上层框架版本漂移。
- **要记住的细节**：每项都对应一个已在前文出现过的机制（证据链/candidate/shadow、情感弱权重+L1 TTL、L4 需结果证据、双时态 supersedes、来源权限、scope 预过滤+RLS、evidence 闭包+canary、批处理+触发率+有限重试、单 PG、稳定 HTTP interface）。
- **批注**：这张表适合当"实现评审检查单"用，逐条问"我们的缓解措施真的存在吗"。

### §28 明确不做的设计 [L1331](./persistent_llm_memory_system_technical_design.md#L1331)
- **讲了什么**：十条禁令：情感分数不直接决定永久保存、不建万能 importance 总分绕过硬门、摘要不覆盖原始事件、访问次数不改事实真假、不每轮同步抽取、不全量向量化、首版不上图库、不为测试方便建十几个 repository、记忆系统不持有/推断业务工具权限、本轮不创建运行代码/migration/test。
- **要记住的细节**：这节与 §12.1、§11.2 相互印证，是"防止设计在实现中被人性化退化"的负向契约。
- **批注**：当前仓库已经越过最后一条（实现已存在），其余九条仍是实现评审的对照项。

### §29 实现前必须确认的五个产品变量 [L1344](./persistent_llm_memory_system_technical_design.md#L1344)
- **讲了什么**：opt-in 方式、L0–L4 保留期与备份最晚删除、敏感类别范围、能否关闭情感分析与 affect 数据清理、成本/延迟/上下文预算。
- **要记住的细节**：原文结论——"如果这些值没有明确，系统可以开发，但无法诚实地声称隐私、成本和用户体验已经设计完成"。
- **批注**：这是从技术设计回到业务评审的关口，也是本笔记第四部分要列出的开放问题来源。

### §30 设计完成定义 [L1356](./persistent_llm_memory_system_technical_design.md#L1356)
- **讲了什么**：进入实现前应同时满足的七项条件（评审通过、四方法冻结、schema/删除闭包/RLS 获批、敏感分类保留期已定、中文金标与投毒集规格完成、PG/pgvector 运维成本被接受、获得创建测试文件授权后再 TDD）。
- **要记住的细节**：最后一句——"在此之前，继续增加框架代码只会把尚未决定的产品和隐私规则固化成技术债。"
- **批注**：对照仓库现状，实现已经开始且默认值已固化在 settings 中；这条"完成定义"实际已被跳过。风险不在代码本身，而在 §29 那五个变量还没有书面确认。

---

# 第三部分　研究 → 设计 → 实现 对照

| 研究文档主张 | 技术设计落点 | 当前实现（可核对） |
|---|---|---|
| 原始事件是证据，派生记忆可追溯（§1、§6） | §8.1 memory_event + §8.3 memory_evidence | `models.py` DTO + `postgres.py` 仓储；README 称事件与 outbox 同事务 |
| 五层记忆与各自门槛（§5、§7.4） | §9.1 映射 + §12.2 晋升表 | `models.py` MemoryLayer(L1–L4) + `policy.py` MemoryPolicy；L4 自动写入关闭 |
| 硬门先于分数（§7.2） | §12.1 判定顺序图 | `policy.py` 的 WriteDecision；README 列明拒绝凭证/敏感/注入 |
| 热/冷双路径（§7.1） | §10 在线链路 + §11 outbox 巩固 | `worker.py` + outbox；`consolidation.py` Heuristic/Hybrid/Consolidator |
| 双时态与版本（§9） | §13 冲突分类 + §8.2 valid_from/to、supersedes_id | schema 字段存在；具体查询谓词实现程度**待核对** |
| 混合检索 + RRF（§10） | §14 流程图与排序 | `retrieval.py` pack_context/render_data_only；v1 用 FTS+中文 trigram，未启用 embedding |
| data-only 上下文（§10、§12.3） | §14.4 渲染格式 | `retrieval.render_data_only`；README 称 `<memory-data trust="untrusted">` |
| 删除级联 + canary（§12.2） | §17 删除协议 | `worker.process_one_deletion` + deletion job；README 称 erasure tombstone + payload hash 防复活 |
| 记忆不得授权工具（§12.3） | §18 来源矩阵 + §5 契约 | `api.py` JWT purposes/memory_write 来自 token |
| 先评估再智能（§13、§14） | §25 验收 + §26 测试 | `tests/` 七类文件；中文金标/投毒集**未见**（README 提示需真实业务金标） |

**已发现的三处设计—实现漂移（供后续审查）**

1. §6 目录含 `deletion.py` 与 `prompts/`，当前仓库无同名文件/目录；删除逻辑分布在 `system.py`、`postgres.py`、`worker.py`，抽取提示词在 `consolidation.py`/`llm.py`。属于可接受收敛，但文档应回填。
2. §1 写"pgvector 负责语义候选召回"，而 v1 README 明确尚未启用 embedding，召回用 FTS + 中文 trigram + L3 画像常驻 + 场景化 L1。这是把 Phase 2 部分内容再次延后，方向与"数据证明后再启用"一致。
3. §30 要求先完成评审与金标再进实现，实际代码已经落地并固化了默认保留期/敏感策略；需要补的是 §29 五个变量的书面决策，而不是回退代码。

---

# 第四部分　精读后的评价与待核对清单

## 三个最值得保留的设计判断

1. **证据链优先于摘要**：所有派生物必须可回溯到事件；这让更正、删除、审计、投毒取证同时成立，一个决定解决四类问题。
2. **硬门先于分数**：权限/来源/敏感/冲突是不可被相似度或重要性抵消的前置条件，直接否定了"一个 importance 打分统管一切"的简化路线。
3. **先评估再智能**：图关系、自动反思、情绪驱动晋升全部推迟到有离线证据之后；路线图把"昂贵且难治理"的部分放在最后，这是最省钱的风险排序。

## 三个最需要警惕的地方

1. **金标缺失**：文档反复以中文金标与投毒集为退出条件，当前实现测试覆盖了工程契约，但"什么该记/该存哪层/何时失效"的一致性尚未验证；没有它，shadow 审核没有判据。
2. **启发式与模型双模式的一致性**：`heuristic` 默认可用、`shadow/hybrid/model` 可选，两条路径必须产出同构候选并经同一套 policy，否则"默认好用、开模型变差"或反之都会出现；这一点文档只在 §11–§12 隐含要求，值得加契约测试。
3. **文档漂移**：技术设计是"实现前"快照，schema 与目录已被实现部分改写；若继续以文档为唯一验收依据，评审会拿旧地图找新大陆。

## 待核对问题（留给"设计—实现偏差审查"）

1. 双时态是真实现（valid_to/valid_from + supersede 链）还是只落了字段？"当前事实查询"是否避开了 `created_at DESC`？
2. 删除 canary 具体覆盖哪些索引（FTS/trigram/画像投影），备份到期如何记录与验证？
3. L3 画像投影如何生成、何时重建，是否有独立于 item 表的事实源（文档要求"投影不是第六个事实源"）？
4. 情绪启发式（单次高唤醒→L1 24h）的阈值、跨语言稳定性，以及 affect 是否只作为 L1/排序信号？
5. outbox 的 shadow 模式审核入口（`statuses=quarantined`）是否形成闭环，模型候选是否会绕过 policy 直接 active？
