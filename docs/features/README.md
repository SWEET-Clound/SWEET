# features 文档索引

这个目录收集的是偏功能说明、运行影响和部署说明的文档。各文件用途如下：

- [oa_prompt_nesting.md](/home/haoxindong/workspace/qll_chat/docs/features/oa_prompt_nesting.md): 说明 `OAAgent` 系统提示词的变量注入与嵌套替换关系。
- [redis_setup.md](/home/haoxindong/workspace/qll_chat/docs/features/redis_setup.md): 说明 Redis 在测试环境和正式环境下的部署命令与参数。
- [model_unavailable_impact.md](/home/haoxindong/workspace/qll_chat/docs/features/model_unavailable_impact.md): 说明各模型不可用时会影响哪些业务能力和链路表现。
- [general_embedding_models.md](/home/haoxindong/workspace/qll_chat/docs/features/general_embedding_models.md): 汇总不同厂商的 embedding 模型与接口示例，便于对比参考。
- [knowledge_parse_report.md](/home/haoxindong/workspace/qll_chat/docs/features/knowledge_parse_report.md): 说明知识解析后台结构化日志 `knowledge_parse_report` 的字段、类型和统计口径。
- [em_corpus_sync.md](/home/haoxindong/workspace/qll_chat/docs/features/em_corpus_sync.md): 说明 `scripts/em_task/` 下 `sync_em.py` 与 `deploy.sh` 的 EM 语料同步策略、定时任务和数据清理规则。
- [qll_chat_sandbox_design.md](qll_chat_sandbox_design.md): qll_chat 独立 Docker 沙箱、配置开关、实例池、生命周期与安全设计；实际部署步骤见 [Sandbox 部署手册](../../qll_chat/sandboxd/README.md)。
- [skill_authoring_web.md](skill_authoring_web.md): Skill 共用创建器、编写规范与打包模板。
- [client_oa_java_interface.md](client_oa_java_interface.md): ClientOAAgent 的 Java 请求、客户端工具中断、结果回填、续跑与 SSE 事件契约。
- [visual_mode_dynamic_model_switch.md](visual_mode_dynamic_model_switch.md): ClientOAAgent 通用视觉模式、workbot 多模态模型切换、断点续跑恢复与图片清理机制。
- [customer_qa_agent.md](customer_qa_agent.md): 客服知识问答 Agent 的路由、能力边界、检索工具、Prompt 与流式协议。
- [generic_configurable_agent.md](generic_configurable_agent.md): 通用可配置智能体的配置模型、运行时、Prompt、MCP 装配、路由与降级策略。
- [generic_agent_java_interface.md](generic_agent_java_interface.md): GenericAgent 的 Java 请求参数、响应事件和完整对接样例。
- [oa_agent_channel_support.md](oa_agent_channel_support.md): OAAgent 对 `supportChannel`、模块合并配置和功能开关的兼容规则。
- [third_party_knowledge_retrieval_api.md](third_party_knowledge_retrieval_api.md): 第三方 Agent 调用三个知识检索 HTTP 接口时的参数、权限过滤、响应、开关与部署说明。
- [llm_timeout_and_fallback.md](llm_timeout_and_fallback.md): 大模型请求的各层超时参数、触发后果、`read_timeout` 让路规则，以及兜底配置如何决定应用层超时是否生效。
- [malloc_arena_max.md](malloc_arena_max.md): `MALLOC_ARENA_MAX` 环境变量的作用与原理（glibc arena 机制）、本服务的收益与边界，以及内存排查入口。
- [cascaded_voice_agent_design.md](cascaded_voice_agent_design.md): 自研级联语音架构、VoiceAgent、团队职责与部署设计。
- [cascaded_voice_agent_project_plan.md](cascaded_voice_agent_project_plan.md): P0～P11 分阶段实施、验收证据和后续升级顺序。
- [cascaded_voice_agent_build_guide.md](cascaded_voice_agent_build_guide.md): Pipecat 源码映射、从零搭建、完整源码索引、协议和联调步骤。
