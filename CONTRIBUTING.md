# Contributing / 贡献指南

## English

1. Fork and clone the repository. Work on `app/`, not `history/snapshots/`.
2. Follow [Setup](docs/SETUP.md) through Python dependencies and JVM checks. Use a disposable local environment and synthetic captions.
3. Keep changes focused. Preserve protocol domain separation, durable commit-before-ACK, sequence/idempotency rules, pending-queue identity guards and atomic history/cursor writes.
4. Add regression tests for changed behavior. Run `python tools/run_checks.py` and `python tools/test_android_core.py`. For Android changes, also compile against API 36, build with your own signer, and document real-device acceptance separately.
5. Use UTF-8 and LF. Write substantive comments in English first, Chinese second. Update both language sections when behavior changes.
6. Run `python tools/audit_public_source.py`. Commit only code, synthetic tests and documentation. Pairing codes, runtime databases, screenshots, logs, migration archives and signing files never belong in an issue or pull request.
7. Describe the problem, resulting behavior, test results and remaining limitations. Submit a pull request against `main`.

Do not claim all-device compatibility or millisecond end-to-end latency from unit tests. Provide a sanitized reproduction with public tool versions and a fabricated packet. Obtain permission before reading another person's captions.

## 简体中文

1. Fork 并克隆仓库。修改 `app/`，不要把历史快照当作当前代码。
2. 按 [配置手册](docs/SETUP.md) 安装 Python 依赖并完成 JVM 检查。使用一次性本地环境和合成字幕。
3. 保持修改范围明确。保留协议域隔离、持久提交后才 ACK、序列与幂等规则、待发送队列身份保护，以及历史和游标的原子写入。
4. 为行为变更补充回归测试。运行 `python tools/run_checks.py` 和 `python tools/test_android_core.py`。Android 修改还应针对 API 36 编译、使用自己的签名构建，并单独记录真机验收。
5. 使用 UTF-8 和 LF。主要注释英文在前、中文在后；行为变化时同步更新两个语言部分。
6. 运行 `python tools/audit_public_source.py`。仅提交源码、合成测试和文档。配对码、运行数据库、截图、日志、迁移包和签名文件不得进入 Issue 或 PR。
7. 说明问题、修改后的行为、验证结果和剩余限制，向 `main` 提交 PR。

不要从单元测试推导全机型兼容或毫秒级端到端延迟。复现材料应脱敏，写明公开工具版本并使用虚构数据包。读取他人字幕前应取得许可。
