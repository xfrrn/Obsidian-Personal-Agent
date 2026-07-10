# Exceptions

Agent Core 语义化异常类型。

约定：

- Core 内部业务错误优先抛 `AgentCoreError` 子类。
- 需要兼容 Python 内置异常语义时，异常类同时继承对应内置异常，例如 `ValueError`、`KeyError`、`PermissionError`。
- API 层可调用 `to_dict()` 生成稳定错误结构。
