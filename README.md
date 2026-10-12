# AI Digest — 每日双语日报自动推送到飞书

每天北京时间 ~15:10（GitHub Actions 定时，可能有 15-60 分钟延迟）自动：
拉取 follow-builders 上游 feed → DeepSeek 改写成成品双语日报 → 存入 `digests/` 并推送飞书卡片。

**成品 .md 存档在 `digests/YYYY-MM-DD.md`，飞书里收到的是渲染后的卡片（内容一致）。**

## 初次配置步骤（只做一次）

### 1. 新建 GitHub 仓库
- 不要 fork 上游仓库。在你的账号下新建一个**私有**空仓库（如 `ai-digest`）
- 把本目录的全部内容推上去（`.github/`、`scripts/`、`prompts/`、`digests/`、README）

### 2. 添加两个 Secrets（Settings → Secrets and variables → Actions → New repository secret）

| Secret 名 | 值 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek 开放平台（platform.deepseek.com）→ API Keys → 创建，`sk-` 开头。先充值少量余额（每天一次调用，几厘钱） |
| `FEISHU_WEBHOOK` | 飞书群 → 设置 → 群机器人 → 添加机器人 → **Custom Bot** → 得到 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`。（签名安全设置选"自定义关键词"更简单：填 `AI Builders` 即可，无需加签密钥） |

### 3. 手动测试
仓库页 → Actions → daily-digest → Run workflow → 等待 ~2 分钟。
- 绿色 ✓：去飞书群看卡片，`digests/` 里多了当天的 .md
- 红色 ✗：点进失败 step 看日志（常见：secrets 拼写、DeepSeek 余额、webhook 关键词不匹配）

### 4. 之后无需任何操作
每天自动运行。因每天都会提交一次 `digests/` 和 `state.json`，永远不会触发"60 天不活跃自动停用"。

## 文件说明

```
.github/workflows/daily-digest.yml   定时任务定义（cron 07:10 UTC + 手动触发）
scripts/build_digest.py              主脚本：去重→组装素材→DeepSeek 改写→存档→推飞书
prompts/*.md                         4 个改写规则文件（来自 follow-builders skill）
digests/                             成品日报存档（每天提交一次）
state.json                           已处理过的播客 guid / 博客 url（去重用，脚本维护）
feeds/                               当日下载的上游 feed（不提交，仅运行时存在）
```

## 日期口径

与已有 retro 流水线一致：**D 日日报 ← feed D+1**。脚本每天 UTC 跑时取"昨天"作为日报日期。

## 已知限制

- GitHub 免费层定时任务可能延迟 15-60 分钟（送达时间 15:10-16:10 之间浮动）
- 飞书卡片对 markdown 支持是子集（标题/表格渲染有限），完整排版看 `digests/` 里的 .md 原件
- DeepSeek 输出上限 8192 token，内容特别重的日子（双播客）摘要会被适度压缩
- 长日报 >24K 字符时自动拆成多张卡片发送
