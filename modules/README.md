# 后续模块放在这里

目前不预设任何业务模块。每个直接子目录应是一个 Python 子项目，拥有自己的
`pyproject.toml`；根目录通过 `modules/*` 将它们统一纳入 uv workspace。

创建和声明模块依赖的方法见 [根目录说明](../README.md)。
不要在这里单独执行 `git init`，整个 DeepHelp 只使用根目录的 Git 仓库。
