# Documentation design and references / 文档设计与参考

## English

The documentation was structured after inspecting established, highly starred GitHub projects on 2026-10-05. Star totals fluctuate and are not a quality guarantee. This is a record of structural choices, not an imported README or a claim of affiliation.

| Primary source | Useful structure | Adaptation here |
| --- | --- | --- |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | Clear purpose, prerequisites, installation, usage, and separate detailed guides | README stays navigable; device requirements and detailed setup have their own guides |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | Short introduction followed by runnable installation/example and deeper documentation | Setup provides commands, expected results, and acceptance checkpoints |
| [othneildrew/Best-README-Template](https://github.com/othneildrew/Best-README-Template) | About, getting started, usage, contribution, license, acknowledgements | Purpose, repository map, limitations, contribution, and license are explicit |
| [GitHub: About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | Repository-local links and a discoverable main README | Internal links use relative paths so they work in a clone and on GitHub |

The README uses English first and a complete Chinese section second. Detailed guides follow the same order. Each installation phase names prerequisites, exact command location, expected output, and the next checkpoint. Known failures have an ordered decision path rather than “reinstall everything.” Fenced commands are PowerShell unless explicitly stated otherwise. Source snippets, license badges, performance claims, and third-party logos are not copied from the reference projects.

### Official tool references

- [Python for Windows](https://www.python.org/downloads/windows/): Python installation; use Python 3.11 x64 for the portable build baseline.
- [Eclipse Temurin JDK 21](https://adoptium.net/temurin/releases/?version=21): one maintained source for a complete JDK.
- [Android SDK Manager](https://developer.android.com/tools/sdkmanager): installing `platform-tools`, `platforms;android-36`, and `build-tools;36.0.0`.
- [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools): official ADB distribution.
- [Cloudflare cloudflared downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/): official tunnel client distribution.
- [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/): temporary development tunnel behavior and limitations.

The project-specific commands and behavior come from this repository's source, not from the README references. Version examples are build inputs, not a claim that every listed tool is the latest upstream release. When updating a dependency, rerun its associated tests and build checks.

---

## 简体中文

2026-10-05 阅读了已有大量星标的 GitHub 项目，参考它们的文档组织方式。星数会变化，也不是质量保证。本文件记录结构设计，不复制整篇 README，不表示与参考项目存在关联。

| 一手参考 | 有用的结构 | 本仓库采用方式 |
| --- | --- | --- |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 清晰说明用途、前置条件、安装、使用，并把详细流程拆到独立文档 | README 负责入口，设备要求和安装细节放到操作指南 |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | 短介绍后给可运行的安装/示例，再链接深入文档 | 每一步给命令、预期结果和验收点 |
| [othneildrew/Best-README-Template](https://github.com/othneildrew/Best-README-Template) | 用途、入门、使用、贡献、许可、致谢 | 明确用途、目录、限制、贡献和许可证 |
| [GitHub 官方 README 说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) | 主 README 易发现、内部链接适配仓库 | 内部采用相对路径，同时适配 GitHub 和本地克隆 |

README 先完整英文，再完整中文；详细指南顺序一致。安装阶段都标明条件、命令所在目录、预期输出及下一检查点。排障按症状分层定位，不采用“全部重装”。命令围栏默认为 PowerShell。未复制参考项目的源码片段、徽章、性能声明或标识。

### 工具官方来源

- [Python Windows 下载](https://www.python.org/downloads/windows/)：便携版构建基线使用 Python 3.11 x64。
- [Eclipse Temurin JDK 21](https://adoptium.net/temurin/releases/?version=21)：完整 JDK 的一种维护中发行来源。
- [Android SDK Manager](https://developer.android.com/tools/sdkmanager)：安装 `platform-tools`、`platforms;android-36`、`build-tools;36.0.0`。
- [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools)：ADB 官方来源。
- [Cloudflare cloudflared 下载](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)：官方隧道客户端。
- [Cloudflare Quick Tunnel 说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)：临时开发隧道的行为和限制。

本项目命令和行为依据仓库源码，README 参考只是格式来源。版本示例是构建输入，不表示一定为上游最新版；更新依赖后应重新运行对应测试和构建检查。
