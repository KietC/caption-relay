# README Design Research

English first · [中文版本](#中文版本)

Research date: **2026-10-05**. This date identifies a documentation study, not a deployment or production event. Star counts are a changing snapshot from GitHub's public REST API; they are a selection aid, not a quality guarantee.

## Official repositories reviewed

| Repository | Stars at review | Public API | README design observed |
|---|---:|---|---|
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 151,158 | [Repository metadata](https://api.github.com/repos/Genymobile/scrcpy) | Purpose, capabilities, prerequisites, platform setup, practical examples, detailed documentation, FAQ, license. |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | 102,828 | [Repository metadata](https://api.github.com/repos/fastapi/fastapi) | Short introduction, official documentation, requirements, installation, a runnable example, expected response, license. |
| [othneildrew/Best-README-Template](https://github.com/othneildrew/Best-README-Template) | 16,388 | [Repository metadata](https://api.github.com/repos/othneildrew/Best-README-Template) | Navigation, project overview, getting started, usage, roadmap, contribution, license and acknowledgments. |

The observations come from the projects' own [scrcpy README](https://github.com/Genymobile/scrcpy/blob/master/README.md), [FastAPI README](https://github.com/fastapi/fastapi/blob/master/README.md), and [Best-README-Template README](https://github.com/othneildrew/Best-README-Template/blob/main/README.md). This repository's wording and diagrams are written independently. No upstream README text, logos or screenshots are bundled.

## Decisions for this project

These are our editorial decisions rather than claims made by the reference projects:

1. **State the actual function immediately.** The bridge transfers captions that already exist on a supported phone. Identify the upstream recognition and translation requirement. Do not describe a development assistant as the speech engine.
2. **Separate the two data paths.** Explain volatile latest preview and durable history, including what each acknowledgment proves.
3. **Give one beginner path.** Present prerequisites, download/build choice, desktop start, private pairing, phone setup, endpoint confirmation, a synthetic sentence and visible success checks in dependency order. Put alternatives after the default path.
4. **Make each setup step checkable.** State where to run a command, what it creates, expected output, and what to check before proceeding. Never publish an actual key or endpoint as an example.
5. **Provide a compact root README.** Link to detailed setup, troubleshooting, architecture, build, privacy and contribution documents. Long operational explanations belong in the linked guides.
6. **Label verification honestly.** Distinguish unit tests, loopback integration, package checks and physical phone acceptance. A local test is not evidence of current remote-phone connectivity or audio-to-screen latency.
7. **Publish only useful badges.** A license badge may point to the real license. A CI badge should point to an existing workflow and must not be presented as passing until the workflow has run. Do not add decorative coverage or download numbers.
8. **Make the whole guide bilingual.** Complete English comes first, then complete Chinese in the same document. Both versions must describe the same defaults, commands, limitations and recovery steps.
9. **Use synthetic examples.** Avoid company products, customer wording, actual environments, real transcripts, personal devices, local usernames and operational receipts.
10. **Keep the license and attribution clear.** Use the repository's declared license for original source. Preserve relevant third-party notices; dependency downloads remain subject to their own licenses.

## Root README outline

```text
Project title and one-sentence purpose
Language navigation and real license/CI links
Scope and tested compatibility
Architecture and data-path diagram
Ordered quick start with expected results
Detailed setup and troubleshooting links
Build and test commands
Privacy and storage boundaries
Known limitations
Repository map
Contribution and security reporting
License and third-party notices
Complete Chinese version in the same order
```

## Markdown acceptance checklist

- Use one H1 and a consistent heading hierarchy.
- Leave a blank line before every list, table and fenced block.
- Label fences with the correct language (`powershell`, `python`, `java`, `json`, `mermaid`).
- Use repository-relative paths for internal links. Do not link to a maintainer's local disk.
- Keep commands copyable: no shell prompts, hidden substitutions, secrets or unrelated setup actions.
- Match filenames, flags, ports and UI labels to the current code.
- Test internal file links and English/Chinese section anchors.
- Show optional actions and irreversible recovery operations explicitly.
- Use tables for comparable symptoms, causes and checks; avoid long nested lists.
- Check the GitHub rendering and mobile reading order after upload.

---

## 中文版本

### README 格式研究

研究日期：**2026-10-05**。此日期表示文档研究日期，不是部署时间或生产事件。星标数通过 GitHub 公共 REST API 实时读取，是会变化的快照，只用于选取参考项目，不代表质量保证。

### 本次查看的官方仓库

| 仓库 | 查看时星标数 | 公共 API | 观察到的 README 结构 |
|---|---:|---|---|
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 151,158 | [仓库元数据](https://api.github.com/repos/Genymobile/scrcpy) | 用途、功能、前置条件、各平台安装、实用示例、详细文档、常见问题、许可证。 |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | 102,828 | [仓库元数据](https://api.github.com/repos/fastapi/fastapi) | 简介、官方文档、环境要求、安装、可运行示例、预期响应、许可证。 |
| [othneildrew/Best-README-Template](https://github.com/othneildrew/Best-README-Template) | 16,388 | [仓库元数据](https://api.github.com/repos/othneildrew/Best-README-Template) | 导航、项目介绍、入门、使用、路线图、贡献方式、许可证及致谢。 |

这些观察来自项目自己的 [scrcpy README](https://github.com/Genymobile/scrcpy/blob/master/README.md)、[FastAPI README](https://github.com/fastapi/fastapi/blob/master/README.md) 和 [Best-README-Template README](https://github.com/othneildrew/Best-README-Template/blob/main/README.md)。本项目文字和图示独立撰写，不打包上游 README 原文、标志或截图。

### 本项目采用的写法

以下是本项目的文档编排决定，不是引用项目给出的结论：

1. **第一段说明真实功能。** 本桥接程序传输受支持手机上已经显示的字幕，说明上游识别与翻译的必要条件，不把开发助手描述成语音识别引擎。
2. **分清两条数据路径。** 说明易失的最新预览与可靠历史，以及各自的确认消息能证明什么。
3. **给新手一条默认路线。** 按依赖顺序写环境要求、下载或构建、电脑启动、私密配对、手机设置、地址确认、合成测试句和可见验收。替代路线放在默认路线之后。
4. **每一步都可以检查。** 写明命令在哪个目录运行、生成什么、正常输出是什么、进入下一步前检查什么。示例不能包含实际密钥或实际地址。
5. **根 README 保持可读。** 链接详细配置、排错、架构、构建、隐私与贡献文档，较长的运行说明放在对应指南。
6. **如实标注验证范围。** 分清单元测试、本机回环集成、打包检查和真实手机验收。本机测试不能证明远端手机此刻连通，也不能证明音频到屏幕的延迟。
7. **只使用有用的徽章。** 许可证徽章必须指向真实许可证；CI 徽章必须指向真实工作流，在工作流首次运行前不能宣传已经通过；不放装饰性的覆盖率和下载量。
8. **完整双语。** 同一文档中先完整英文，再完整中文。两版的默认值、命令、限制和恢复方法保持一致。
9. **使用合成示例。** 不使用公司产品、客户用语、真实环境、真实字幕、个人设备、本机用户名或生产回执。
10. **写清许可证与归属。** 原创源码使用仓库明确声明的许可证；保留必要第三方说明，另外下载的依赖仍遵循各自许可证。

### 根 README 推荐顺序

```text
项目标题与一句话用途
语言导航与真实许可证、CI 链接
功能范围和测试兼容性
架构与数据路径图
按顺序执行的快速开始及预期结果
详细配置和排错链接
构建、测试命令
隐私和存储边界
已知限制
仓库目录
贡献方法和安全问题反馈
许可证与第三方说明
按相同顺序写完整中文版本
```

### Markdown 验收清单

- 一个一级标题，标题层级一致。
- 列表、表格和代码块前留空行。
- 代码块标注正确语言，例如 `powershell`、`python`、`java`、`json`、`mermaid`。
- 仓库内链接使用相对路径，不链接维护者电脑的本地磁盘。
- 命令可以直接复制，不包含提示符、隐藏命令替换、密钥或无关配置操作。
- 文件名、参数、端口和界面文字与当前源码一致。
- 检查文件链接以及中英文标题锚点。
- 明确标出可选操作和不可逆的恢复动作。
- 可比较的症状、原因和检查使用表格，减少长嵌套列表。
- 上传后检查 GitHub 渲染效果和手机阅读顺序。
