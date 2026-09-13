# uv workspace 成员

`deephelp-app/` 是 M01 创建的首个业务包，后续阶段在该包内增量实现。每个 workspace 成员拥有自己的 `pyproject.toml`；根目录通过 `modules/*` 纳入成员，统一使用根 `uv.lock`。

创建和声明模块依赖的方法见 [根目录说明](../README.md)。
不要在这里单独执行 `git init`，整个 DeepHelp 只使用根目录的 Git 仓库。
