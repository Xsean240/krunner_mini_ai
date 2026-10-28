# krunner_mini_ai —— KRunner 免费 AI 对话插件

在 KRunner（Alt+Space）里直接使用大模型进行带上下文的对话，支持联网搜索与图片理解；
也可唤出一个与 KRunner 共享上下文的独立对话小窗口。

- **纯用户态实现**：不写系统目录、不用 root、不向 krunner 进程注入任何代码
  （采用 KDE 官方 DBus Runner 机制，与 KWin 窗口搜索、Baloo 文件搜索同一套体系）
- **默认渠道免费**：智谱 BigModel（`glm-4-flash` 长期免费 / `glm-4v-flash` 免费视觉模型），
  任意 OpenAI 兼容渠道均可一键切换
- **隐私友好**：API key 仅存于本机 0600 权限配置；上下文仅存内存；无遥测

架构与设计决策详见 [DESIGN.md](DESIGN.md)。

## 功能

| 输入（KRunner） | 行为 |
|---|---|
| `ai <问题>` | 带上下文提问（默认记住最近 8 轮，仅存内存） |
| `ai 搜 <问题>` | 联网搜索后回答并列出可点击来源；`ai 搜 新闻 …` 新闻专题、`ai 搜 深 …` 深度检索 |
| `ai 模型 [筛选]` | **查看/主动切换当前模型**（自动 · 智谱 → 备用 → 魔搭；切换后梯子仍作兜底） |
| `ai 窗口` | **打开/置前独立对话小窗口**（与 KRunner 共享上下文） |
| `ai 图 /路径/图片.png <问题>` | 图片理解（免费视觉模型） |
| `ai reset` | 清空上下文 |
| `ai ping` | 健康自检 |
| 回车 | 复制回答全文并弹通知；动作菜单可"查看全文" |

**对话窗口**（GTK4，无需额外安装依赖）：完整滚动对话记录、📎 选择图片、
📋 粘贴剪贴板截图、顶栏**搜索模式下拉框**（仅对话/联网/联网·新闻/联网·深度）、
联网回答附带来源链接列表；窗口持续存在，重复唤出只置前不重开。

内置工程细节：输入防抖（打字过程不浪费配额）、主模型繁忙自动切备用模型、
响应超时自动转后台（通知送达并自动复制，答案不丢失）、搜索结果提示注入防护。

## 初始配置（一次性）

1. **模型 key**（免费，无需信用卡）：注册 [智谱 BigModel](https://bigmodel.cn)，
   控制台 →「**API Keys**」创建 key。默认模型 `glm-4-flash`（长期免费），
  `glm-4.7-flash` 已配置为备用（主模型繁忙时自动切换），视觉模型 `glm-4v-flash` 同样免费
2. **搜索 key**（可选，免费）：注册 [Tavily](https://app.tavily.com)（每月约 1000 次免费）
3. 把 key 填入 `~/.config/krunner-ai/config.toml`（安装后生成，权限 0600）
4. `systemctl --user restart krunner-ai`
5. KRunner 输入 `ai ping` 确认在线，`ai 你好` 开始对话

## 安装 / 卸载 / 更新

```bash
./install.sh      # 安装到 ~/.local，注册 systemd 用户服务并重启 KRunner
./uninstall.sh    # 停服务并删除全部文件（--purge 连配置和 key 一起删）
./install.sh      # 改完代码重新执行即为更新
```

依赖：KDE Plasma 6（KF6 ≥ 6.30）+ 系统自带 `python3-dbus` / `python3-requests` /
`python3-gobject` + GTK4（KDE 发行版一般都有；无则 `dnf install python3-gobject gtk4`）。
已在 Fedora 44 + Plasma 6.7 上完整验证。

离线自检（不消耗 API 额度）：`~/.local/bin/krunner-ai-daemon selftest`

## 安全设计

- 所有文件仅位于 `~/.local`、`~/.config`；`uninstall.sh` 一次删净
- 守护服务经 systemd 沙箱加固：系统目录只读、仅可写指定缓存目录、限制套接字类型
- 仅出站 HTTPS 访问配置中的 API 域名；不监听任何端口；DBus 仅会话总线
- 网页搜索结果只作为明确标注的数据注入提示词；`Run()` 只执行本地固定动作，
  不解析、不执行模型输出中的任何指令
- API key 不进命令行参数、不写日志；日志只记录时延/状态/长度

## 配置（~/.config/krunner-ai/config.toml）

```toml
[llm]
base_url  = "https://open.bigmodel.cn/api/paas/v4"
api_key   = ""
model     = "glm-4-flash"        # 长期免费
fallback_model = "glm-4.7-flash" # 同渠道备用（繁忙时自动切换）
vision_model   = "glm-4v-flash"  # 免费视觉模型
timeout   = 12   # 流式读超时(秒)：只约束首字与流停滞，正常生成超时不会误杀

# 副渠道（可选）：主渠道整体失败/超时时的最后一级
# 魔搭需先在 modelscope.cn 绑定阿里云账号，否则 key 返回 401
[llm.backup]
base_url = "https://api-inference.modelscope.cn/v1"
api_key  = ""
model    = "deepseek-ai/DeepSeek-V4.1-Flash"

[search]
provider = "tavily"   # 或 none
api_key  = ""

[chat]
max_turns   = 8       # 记住的对话轮数
auto_search = false   # true 时自动判断是否联网
```

请求采用**流式接收**（超时只约束首字与停滞，长回答不会被整体误杀），
并按**重试梯子**降级：主模型 → 备用模型 → 副渠道，全程自动。

任何 OpenAI 兼容渠道都可使用，改 `base_url`/`api_key`/`model` 三行即可。

## 故障排查

- **输入 ai 没有反应**：`systemctl --user status krunner-ai` 看服务；改过 desktop 文件后
  需 `systemctl --user restart plasma-krunner`
- **提示"限速或服务繁忙"**：免费模型容量波动，内置自动切换备用模型；仍失败可稍后再试
- **`ai 图` 提示图片不存在**：守护服务有 `PrivateTmp` 沙箱，看不到 shell 的 `/tmp`，
  图片请放家目录，或在窗口里直接粘贴
- **日志**：`journalctl --user -u krunner-ai -f`（窗口进程的报错在 `journalctl --user`）

## 开发与测试

```bash
python3 tests/test_offline.py    # 离线单元测试（不联网、不需要 key）
python3 tests/test_gui_send.py   # GUI 驱动测试（会短暂弹出窗口并真实发送一条）
```

代码：`src/krunner_ai/daemon.py`（守护服务）、`src/krunner_ai/chat_window.py`（GTK4 窗口）。

## 许可

MIT（见 [LICENSE](LICENSE)）

## 参考

- [KRunner DBus Runner 元数据格式（KDE 官方文档）](https://develop.kde.org/docs/plasma/krunner/metadata)
- [org.kde.krunner1 接口实例（plasma-browser-integration）](https://github.com/KDE/plasma-browser-integration/blob/master/dbus/org.kde.krunner1.xml)
- [智谱 BigModel 免费模型文档](https://docs.bigmodel.cn/cn/guide/models/free/glm-4-flash-250414) · [Tavily](https://tavily.com)
