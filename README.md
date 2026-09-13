# DeepHelp

项目当前入口：[00_START_HERE](00_START_HERE.md)；规划策略：[按需拆分](planning/PLAN_MODULE_PROMPT.md)；实施方法：[单主会话串行工作流](docs/WORKFLOW.md)。以下为原workspace与基础设施说明，业务完成状态以docs/PROJECT_STATE.md为准。

一个 Git 仓库、一个 Python workspace，按需添加子模块。
目前只建立基础工程框架，不预设业务模块，也没有新增业务服务或中间件。

## 当前目录

```text
deephelp/
├── .git/                # 整个项目唯一的 Git 仓库，当前分支 main
├── .gitignore           # 忽略本地配置、虚拟环境、密钥和产物
├── .gitattributes       # 新文件使用 LF；infra 保持原始字节
├── .python-version     # 固定当前已验证的 Python 3.12.14
├── pyproject.toml       # 总工程配置，自动收纳 modules/*
├── uv.lock              # 精确依赖锁文件，要提交 Git
├── modules/             # 以后按需添加模块，目前只有说明文件
├── infra/               # 原有部署、隧道、配置与验收资料
├── README.md
└── .venv/               # uv 自动创建的本地环境，不提交 Git
```

以后打开 deephelp 根目录作为 IDE 工程。整个工程只在根目录管理 Git，
子模块不需要各自建仓库，也不需要 Git submodule。

## 用 Maven 来理解

| Maven / Java 中的概念 | 本工程中的对应方式 |
| --- | --- |
| Maven 命令行工具 | uv：管理 Python、虚拟环境、依赖和 workspace |
| 聚合 pom.xml / modules | 根 pyproject.toml 的 `[tool.uv.workspace]` |
| 子模块 pom.xml | 每个子目录自己的 pyproject.toml |
| 子模块间 dependency | dependencies + `{ workspace = true }` |
| Maven Central | PyPI，Python 第三方包仓库 |
| 确定实际使用的依赖版本 | 根 uv.lock，记录解析后的精确版本 |
| 构建 jar | 构建 wheel（.whl），Python 的可安装分发包 |

这是职责上的类比：Python 没有 Maven 那样统一的生命周期，根配置也不会像父 POM
一样自动继承全部元数据。子模块自己的名称、Python 要求和依赖要明确声明。
uv.lock 是解析结果，不是手写的 BOM。

Python 中“模块”常指一个 .py 文件，“包”通常指含 `__init__.py` 的目录。
你说的 Maven Module 在这里对应“有自己 pyproject.toml 的子项目”。
子项目内部可以继续分很多文件和包，不必把每个文件都拆成 workspace 成员。

根工程设置 `package = false`，只负责组织，不生成安装包。
infra/ 是运维目录，目前不属于 Python workspace，不需要硬加 pyproject.toml。

## 本机开始

```powershell
cd D:\IdeaProject\deephelp
uv sync --locked --all-packages
uv run python --version
```

本次安装了 uv 0.12.13。若终端找不到 uv，可把命令中的 `uv` 换成 `py -3.11 -m uv`：

```powershell
py -3.11 -m uv sync --locked --all-packages
```

这里 Python 3.11 只是启动已安装的 uv；实际项目由 uv 使用 Python 3.12。
uv 会按 .python-version 选择 Python，在根目录创建 .venv，不需要手工激活环境。
IDE 的解释器选择 `.venv\Scripts\python.exe`；Linux/WSL 对应 `.venv/bin/python`。
Windows 与 WSL 分别重建自己的环境，不共用同一个 .venv。

换电脑时按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/)
安装 uv 0.12 系列，再 clone 仓库、运行 sync，无需复制旧虚拟环境。

## 以后添加模块

以下 your-module 只是示例名称，替换成你实际需要的名字；当前没有创建它。
在根目录执行：

```powershell
uv init --lib --build-backend uv --python 3.12.14 --vcs none modules/your-module
uv sync --all-packages
```

`--lib` 创建可被其他模块导入的 Python 包；`--vcs none` 避免单独初始化 Git。
根配置的 `modules/*` 会自动收纳它。生成的基本结构类似：

```text
modules/your-module/
├── pyproject.toml
├── README.md
└── src/
    └── your_module/
        └── __init__.py
```

your-module 是依赖声明中的项目名；your_module 是代码中的导入名。
src 布局把源码放在明确的位置，避免依赖“恰好从当前目录启动”才能导入。

给该模块添加第三方依赖，例如：

```powershell
uv add --package your-module httpx
```

运行依赖写在使用它的子模块中。以后需要全工程开发工具，可在根目录运行
`uv add --dev 工具名`。依赖变更时同时提交 pyproject.toml 和 uv.lock。

## 模块间声明依赖

假设未来已有 module-a 和 module-b，A 需要使用 B：

```powershell
uv add --package module-a module-b
```

uv 会识别已经存在的 workspace 成员，在 A 的 pyproject.toml 中记录类似：

```toml
[project]
name = "module-a"
# version、requires-python 等其他字段保留
dependencies = ["module-b"]

[tool.uv.sources]
module-b = { workspace = true }
```

A 的代码就可以 `import module_b`。B 从本地 workspace 以 editable 方式安装，
修改其 Python 源码后重新运行即可使用，不需要先发布到 PyPI 或手工打包安装。
依赖或打包配置变化后仍需重新 sync。

```powershell
uv run --package module-a python -c "import module_b; print(module_b.__file__)"
```

不要用 sys.path.append 或 PYTHONPATH 拼接模块；保持单向依赖，避免循环。
共享 .venv 不会阻止代码误导入未声明的包，所以“用了哪个包，就明确声明它”仍是开发约定。

## 扩展原则

- 新增模块只需增加一个 modules/ 下的 Python 子项目，无需另建 Git 仓库。
- 各模块声明自己的依赖，整个 workspace 使用同一个锁文件和开发环境。
- 分成多个包并不要求部署多个服务；初期可以在同一个进程内运行。
- 如果未来某个模块必须使用冲突版本或不同 Python 环境，可在 workspace 中将它排除，
  单独管理 pyproject.toml、锁文件和环境，同时继续保留在同一个 Git 仓库。

模块组织不需要新增中间件。uv 是本地开发工具，现有 MySQL、Redis、Milvus 继续按原用途使用。
具体框架、驱动、队列在实现对应业务时再添加。

## 现有基础设施

infra/ 内原文件保持原始字节；验收报告里的旧路径作为历史记录保留。
服务器仍使用 `/srv/deephelp-infra`，本次没有操作服务器、数据库或正在运行的隧道。

旧目录 `D:\IdeaProject\deephelp-infra` 保留了旧虚拟环境和隧道状态，短期可继续使用：

```powershell
cd D:\IdeaProject\deephelp-infra
.\client\tunnel.ps1 -Action health
```

新 infra/ 已复制本机 .env 和 config/connections.env（均被 Git 忽略），没有复制虚拟环境
或运行中的隧道 PID 文件。用户目录中的原凭据文件继续保留在原位置。
切换隧道管理路径时，先在旧目录停止，再从新目录启动，避免重复占用相同端口。

旧验收工具使用独立的 infra/client/requirements.lock.txt，不混入新的业务 workspace。
需要在新路径使用完整旧验收工具时，可重建它自己的 Python 3.12 环境：

```powershell
cd D:\IdeaProject\deephelp
uv venv --python 3.12.14 infra/.venv312
uv pip install --python infra/.venv312/Scripts/python.exe -r infra/client/requirements.lock.txt
```

原说明见 [infra/README.md](infra/README.md)。在新路径使用时，将其中的本机路径
`D:\IdeaProject\deephelp-infra` 对应为 `D:\IdeaProject\deephelp\infra`。
Linux/WSL 健康检查可通过旧脚本支持的 DEEPHELP_PYTHON 指定解释器。

## 整体推到 GitHub

本工程使用 main 分支，保留原基础设施的两次提交历史；不需要操作 codex/p00-infra。
旧目录中的原分支保留。远端仓库为 [yuweilan7/deephelp](https://github.com/yuweilan7/deephelp)，
本地 origin 指向该仓库，main 跟踪 origin/main。
Git 是本地版本管理，GitHub 是远端仓库平台，使用 GitHub 仍需要本地 commit 和 push。

GitHub Desktop 可以选择 Add local repository，指向 deephelp 根目录，
检查修改、提交，再用 Push origin 推送。不要指向 infra/ 子目录。

日常使用命令行提交并推送：

```powershell
cd D:\IdeaProject\deephelp
git status
git add .
git diff --cached --stat
git commit -m "描述本次修改"
git push
```

换电脑时克隆仓库：

```powershell
git clone https://github.com/yuweilan7/deephelp.git
cd deephelp
uv sync --locked --all-packages
```

日常修改后运行 git add、git commit、git push，整个项目一次推送，无需逐模块操作。
源码、配置模板、文档、uv.lock 和已有运维报告会上传；虚拟环境、真实 .env、密钥、
本地数据与生成产物被忽略。如果远端已有提交，先整合两边历史，不要直接强制 push。

## 官方资料

- [uv workspace：共享锁文件、本地依赖及适用边界](https://docs.astral.sh/uv/concepts/projects/workspaces/)
- [pyproject.toml：Python 项目元数据标准](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
- [uv 项目结构和锁文件](https://docs.astral.sh/uv/concepts/projects/layout/)
- [GitHub：上传已有本地代码](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)
