# Week 3–12 定向阅读导引

这是对各周 `RESOURCES.md` 的深化：每项资料只服务一个练习或实验。阅读结束必须交出问题答案和对应产物；看完网页但没有代码、测试或笔记，只算 E0/E1，不算掌握。

## 固定阅读法

每条资料最多用 20–45 分钟。先写预测，再只读指定主题；关闭网页后回答三个问题，最后完成“连接产物”。版本会变动的 SGLang 页面必须同时记录访问日期和 Week 3 固定 SHA；网页不能替代源码证据。

## Week 3：Python 工程边界

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [Python modules](https://docs.python.org/3/tutorial/modules.html) | 模块执行、`__name__`、包 | import 时发生什么？为什么顶层 I/O 危险？`main()` 的边界是什么？ | E2 导入副作用测试 |
| [mypy getting started](https://mypy.readthedocs.io/en/stable/getting_started.html) | annotations、incremental adoption、strict | 类型检查不能证明什么？`Literal` 为什么比 str 窄？为何先 scoped？ | TaskType 故意错误输出 |
| [Ruff configuration](https://docs.astral.sh/ruff/configuration/) | `pyproject.toml`、check/format | lint/format 各发现什么？为何不以 `noqa` 结案？怎样让命令失败？ | quality script |
| [pytest monkeypatch](https://docs.pytest.org/en/stable/how-to/monkeypatch.html) | fixture 与副作用隔离 | fake 与 monkeypatch 何时选？为何不能 patch 一切？如何还原？ | import/配置测试 |

## Week 4：HTTP 与测试边界

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/) | TestClient、pytest | 为什么不用开 socket？怎样注入 app/service？测试什么而不测试什么？ | contract tests |
| [FastAPI status codes](https://fastapi.tiangolo.com/tutorial/response-status-code/) | status code 基础 | 422/503/500 的责任方是谁？调用者各做什么？为何不 200+error？ | status decision 表 |
| [Python logging](https://docs.python.org/3/howto/logging.html) | logger、level、format | log 与 response 的受众？字段化为何重要？哪些绝不能写？ | JSONL schema |
| [IANA status registry](https://www.iana.org/assignments/http-status-codes/http-status-codes.xhtml) | 4xx/5xx | 429、401 本周为何不实现？status 是否说明根因？cacheable 吗？ | 非目标说明 |

## Week 5：async 与 streaming

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [asyncio tasks](https://docs.python.org/3/library/asyncio-task.html) | await/task/cancel/timeout | cancellation 在何处注入？为何要 re-raise？timeout 的归属？ | cancel test |
| [Python async generators](https://docs.python.org/3/reference/expressions.html#asynchronous-generator-iterator-methods) | `async for`、cleanup | generator 何时 close？finally 何时运行？谁拥有 task？ | stream source |
| [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events) | event/data/framing | 一条 event 的边界？terminal 如何定义？浏览器 reconnect 本周支持吗？ | `encode_sse` tests |

## Week 6：并发、测量与 workload

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [asyncio synchronization](https://docs.python.org/3/library/asyncio-sync.html) | Semaphore/Queue/Event | 限制的是什么？队列满怎么办？测试为何用 Event？ | admission tests |
| [statistics quantiles](https://docs.python.org/3/library/statistics.html) | quantile 术语/限制 | 选哪种分位定义？样本少时能说明什么？为何保存 raw？ | P95 function |
| [SGLang serving benchmark](https://docs.sglang.ai/developer_guide/bench_serving) | workload/metrics/outputs | concurrency 与 request rate 区别？TTFT/ITL 是什么？fake 延迟不能证明什么？ | metric mapping |

## Week 7：观测与可恢复失败

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [OpenTelemetry Python manual](https://opentelemetry.io/docs/languages/python/instrumentation/) | trace/span/context | trace 与 log 区别？parent 如何传播？采样限制是什么？ | trace schema |
| [Python exceptions](https://docs.python.org/3/tutorial/errors.html) | raise/try/finally | 哪些 exception 可恢复？为何 finally 必须执行？为何不暴露 traceback？ | error taxonomy |
| [HTTP retry guidance](https://www.rfc-editor.org/rfc/rfc9110) | idempotency/retry 相关段落 | 什么请求可重试？deadline 在哪里？副作用如何阻止重放？ | retry allowlist |

## Week 8：复现与证据

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [Python venv](https://docs.python.org/3/library/venv.html) | create/activate/rebuild | 环境里有什么？为何 lock/version 要记录？干净 venv 证明什么？ | rebuild log |
| [pytest good practices](https://docs.pytest.org/en/stable/explanation/goodpractices.html) | test layout/import | discovery 规则是什么？如何避免隐式路径？什么是 flaky？ | stability runner |
| [SGLang contribution guide](https://docs.sglang.ai/developer_guide/contribution_guide.html) | tests/PR | issue 与复现区别？小测试为何优先？哪些证据可让 review 有效？ | issue card |

## Week 9：Tensor 实验

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [PyTorch tensor attributes](https://docs.pytorch.org/docs/stable/tensor_attributes) | dtype/device/layout | dtype/device 分别改变什么？默认提升可靠吗？CPU/GPU何时混用失败？ | capability table |
| [PyTorch numerical accuracy](https://docs.pytorch.org/docs/stable/notes/numerical_accuracy.html) | floating point limits | FP32为何是 oracle？allclose 如何选？为何不同后端不同？ | error report |
| [PyTorch profiler](https://docs.pytorch.org/docs/stable/profiler.html) | profile/export | trace显示什么？不显示什么？如何避免把 warmup 算入？ | trace + explanation |

## Week 10：Attention

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [PyTorch SDPA](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html) | shapes/mask/is_causal | Q/K/V shape？mask 方向？非方形 causal 是否可直接外推？ | SDPA oracle test |
| [MultiheadAttention](https://docs.pytorch.org/docs/stable/generated/torch.nn.MultiheadAttention.html) | mask semantics | 与 SDPA mask 有何不同？head 如何分割？何时 C%H 必须整除？ | multihead tests |
| [Attention Is All You Need](https://arxiv.org/abs/1706.03762) | scaled dot-product/multihead | 为什么除 sqrt(D)？因果 mask 保障什么？O(T²) 来自哪里？ | handcalc + complexity |

## Week 11：Decoder 与可重复运行

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [nn.Module](https://docs.pytorch.org/docs/stable/generated/torch.nn.Module.html) | forward/eval/state | eval 改变什么？parameters/state_dict区别？为何 module boundary 有用？ | deterministic forward |
| [LayerNorm](https://docs.pytorch.org/docs/stable/generated/torch.nn.LayerNorm.html) | normalized shape | 它在哪些维度归一化？为什么 shape test 必要？训练/推理差别？ | block contract |
| [PyTorch reproducibility](https://docs.pytorch.org/docs/stable/notes/randomness.html) | seeds/limits | seed 保证什么？哪些 CUDA 算法仍不同？报告怎样写边界？ | replay harness |

## Week 12：Cache 与 serving 映射

| 资源 | 只读范围 | 三个问题 | 连接产物 |
| --- | --- | --- | --- |
| [PyTorch SDPA](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html) | Q/K/V 长度 | decode Q=1/KV>T 时何种 mask？为何 square默认不安全？cache 拼哪一维？ | cache tests |
| [vLLM PagedAttention paper](https://arxiv.org/abs/2309.06180) | abstract/introduction | local cache 与 page allocation 区别？碎片来自哪里？本周未测什么？ | local-vs-paged table |
| [SGLang session cache](https://docs.sglang.ai/advanced_features/session_radix_cache.html) | session/prefix lifecycle | session ownership？close/eviction？多租户新增何种正确性问题？ | cache gap analysis |

## 最终检查

每周 Resources、Detailed Path、SGLang Lab 三者应形成闭环：**一条资料 → 一个可回答问题 → 一个代码/测试/实验产物 → 一条可复现证据**。若闭环断裂，先删减主题而不是添加更多资料。
