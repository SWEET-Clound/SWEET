# 第 1 周环境证据

日期：2026-10-01

## 已知初始状态

- Git：2.55.0.windows.5
- GPU：NVIDIA GeForce RTX 5070 Ti，16303 MiB
- Windows NVIDIA 驱动：591.86
- Windows Python：3.14.7 已安装；当前终端 PATH 中没有 `python`/`py`
- WSL：Ubuntu / WSL2 已安装并验证

## Windows

```text
winver/build：Windows 11（具体 build 待用户补充）
PowerShell：可用
Python 版本：3.14.7
Python 路径：C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe
项目解释器：D:\Code\SWEET\.venv\Scripts\python.exe
pip 版本：26.2.1
Git 版本：2.55.0.windows.5
```

## 虚拟环境

创建命令：

```powershell
C:\Users\Admin\AppData\Local\Python\pythoncore-3.14-64\python.exe -m venv D:\Code\SWEET\.venv
```

验证输出：

```text
Python 3.14.7
sys.executable = D:\Code\SWEET\.venv\Scripts\python.exe
sys.prefix != sys.base_prefix = True
pip 26.2.1
```

重建步骤：

1. 确认 Windows Python 3.14 基础解释器仍存在；
2. 在仓库根目录使用基础解释器执行 `-m venv .venv`；
3. 始终使用 `.\.venv\Scripts\python.exe` 安装依赖和运行命令。

## WSL2

安装命令：

```powershell
wsl --install -d Ubuntu-24.04（用户已完成；实际发行版显示名称为 Ubuntu）
```

`wsl -l -v`：

```text
NAME      STATE    VERSION
Ubuntu    Stopped  2
```

`wsl nvidia-smi`：

```text
NVIDIA GeForce RTX 5070 Ti, 16303 MiB, 591.86
```

## Git 忽略审计

| 路径/模式 | 是否忽略 | 证据 |
| --- | --- | --- |
| `.venv/` | 已忽略 | `.gitignore:1` |
| `.env` | 已忽略 | `.gitignore:2` |
| `__pycache__/` | 已配置 | `.gitignore:3` |
| `.pytest_cache/` | 已忽略并验证 | `.gitignore:5` |

## 阻塞与解决

| 阻塞 | 完整错误 | 查阅资料 | 下一步 | 状态 |
| --- | --- | --- | --- | --- |
| 当前终端找不到 `python`/`py` | PATH 未包含用户 Python；PyCharm 仍可使用 `.venv` | Python Windows 文档 | 本周统一使用 `.venv\Scripts\python.exe`，PATH 是否配置暂不阻塞 | 已绕开 |
| `.venv` 没有 pytest | 初始为 `No module named pytest` | pytest 入门 | 已通过 `requirements-dev.txt` 安装 9.1.1 | 已解决 |
| WSL 没有 pip | `/usr/bin/python3: No module named pip` | Ubuntu/Python 包管理 | Week 1 Windows 测试不需要；后续单独安装 | 延后 |

## 最终复现结论

- [x] 新终端可以用明确路径运行项目 Python；
- [x] 虚拟环境确实隔离；
- [x] WSL2 状态明确；
- [x] WSL 可以看到 GPU；
- [x] 未安装 WSL Linux 显示驱动；
- [x] pytest 可运行，现有练习结果为 `1 passed in 0.03s`；
- [ ] 完整 Agent 环境可以从文档重建（应用依赖尚未整理）。
