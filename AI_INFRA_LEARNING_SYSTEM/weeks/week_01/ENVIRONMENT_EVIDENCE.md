# 第 1 周环境证据

日期：

## 已知初始状态

- Git：2.55.0.windows.5
- GPU：NVIDIA GeForce RTX 5070 Ti，16303 MiB
- Windows NVIDIA 驱动：591.86
- Windows Python：初检不可用
- WSL：初检未完成安装

## Windows

```text
winver/build：
PowerShell：
Python 版本：
Python 路径：
pip 版本：
Git 版本：
```

## 虚拟环境

创建命令：

```powershell

```

验证输出：

```text

```

重建步骤：

1.
2.
3.

## WSL2

安装命令：

```powershell

```

`wsl -l -v`：

```text

```

`wsl nvidia-smi`：

```text

```

## Git 忽略审计

| 路径/模式 | 是否忽略 | 证据 |
| --- | --- | --- |
| `.venv/` | | |
| `.env` | | |
| `__pycache__/` | | |
| `.pytest_cache/` | | |

## 阻塞与解决

| 阻塞 | 完整错误 | 查阅资料 | 下一步 | 状态 |
| --- | --- | --- | --- | --- |
| | | | | |

## 最终复现结论

- [ ] 新终端可以运行 Python；
- [ ] 虚拟环境确实隔离；
- [ ] WSL2 状态明确；
- [ ] WSL 可以看到 GPU；
- [ ] 未安装 WSL Linux 显示驱动；
- [ ] 环境可以从文档重建。
