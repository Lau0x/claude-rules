# Claude / Anthropic 分流规则

从 [Claude Code 域名分流规则大全](https://ip.net.coffee/claude/site.html) 同步的 Shadowrocket / Surge 规则集，以及一份 Clash classical payload。

页面本身**没有**提供可直接订阅的 raw 规则地址。本仓库只放规则，不放完整客户端配置，也不放节点、密码或订阅链接。

本次对照抓取时间：2026-11-23 09:49:41 CST（Asia/Singapore, UTC+8）。  
页面 `Last-Modified`：2026-11-13 15:26:54 CST（HTTP 头为 `Fri, 13 Nov 2026 07:26:54 GMT`）。  
sitemap 中该页的 `<lastmod>` 也是 `2026-11-13`。正文里没有单独的版本号。

## 文件

| 文件 | 用途 |
| --- | --- |
| `rules/claude.list` | 主规则（对应页面「完整分流配置」的 classical 块，并补上兜底 `DOMAIN-KEYWORD,sentry`） |
| `rules/claude-kyc.list` | KYC 域名（页面表格有，复制块经常没有） |
| `rules/claude-ntp.list` | 可选 NTP 兜底。页面写的是 `geosite:category-ntp`，Shadowrocket 不支持 geosite，这是一份保守替代，不是完整 geosite 导出 |
| `rules/claude.yaml` | Clash / Mihomo `payload` 格式（主规则 + KYC） |
| `update.py` | 抓取页面、解析、生成规则；有变化时打印 diff。**不会自动 commit / push** |
| `snapshots/source-meta.json` | 最近一次抓取的 `Last-Modified`、HTML 哈希、解析后规则哈希 |

## Shadowrocket 用法

把下面两行放到配置 `[Rule]` 段**最前面**（优先于通用 Google / Apple / GEOIP 规则）：

```
RULE-SET,https://cdn.jsdelivr.net/gh/Lau0x/claude-rules@main/rules/claude.list,AI
RULE-SET,https://cdn.jsdelivr.net/gh/Lau0x/claude-rules@main/rules/claude-kyc.list,AI
```

策略名 `AI` 必须和你配置里的策略组一致。

国内直连 `raw.githubusercontent.com` 经常失败，所以默认写 jsDelivr。也可以改成：

```
RULE-SET,https://raw.githubusercontent.com/Lau0x/claude-rules/main/rules/claude.list,AI
```

或把 `rules/*.list` 挂到你自己的域名后再引用，国内更稳。

可选 NTP（默认不要急着开）：

```
RULE-SET,https://cdn.jsdelivr.net/gh/Lau0x/claude-rules@main/rules/claude-ntp.list,AI
```

这条必须放在 `DOMAIN-SUFFIX,apple.com,DIRECT` 之前，否则 `time.apple.com` 会走直连。另外：NTP 返回的是 UTC，不是时区；更有效的是把设备时区改成和代理出口一致，并保持 UDP 代理可用。

### 客户端如何在线更新

Shadowrocket 的 `RULE-SET` 行通常**不需要**写 `update-interval`（那是 Surge 常见写法，部分配置也能兼容，但不是官方文档的重点）。

在 App 里：

1. 打开 **配置文件 > 使用配置 / 编译配置**，会重新拉取当前配置引用的远程规则集。
2. 若要自动更新：在 Shadowrocket 设置里打开配置自动更新，间隔可选 1–7 天；同时在 iOS **系统设置 > 通用 > 后台 App 刷新** 里允许 Shadowrocket。
3. 本地配置如果没有「配置订阅 URL」，自动更新仍会刷新已引用的远程规则集。

## 更新脚本

```bash
python3 update.py          # 抓取并写文件；规则有变化时 exit 1，并打印 diff
python3 update.py --dry-run
python3 update.py --html /path/to/site.html
```

解析优先用页面里以 `DOMAIN-SUFFIX,anthropic.com` 开头的 classical `<pre>` 块，KYC 从表格补，`keyword:sentry` 从兜底块补。HTML 可能因 CDN/统计代码而哈希变化，脚本另外计算 `parsed_rules_sha256`，避免把页面噪音当成规则更新。

建议检测频率：**每天 1 次**足够。该页 sitemap 写的是 monthly，最近一次实际更新是 2026-11-13；HTTP 缓存 `max-age=900`，不必按小时打。

## 要不要直接订阅别人的仓库？

作者没有官方 raw 地址。网上能找到第三方列表，但不建议直接当本页的替代：

- `xiaolai/anthropic-claude-surge-rules-set` 的 `dist/anthropic.list` 标注生成于 2026-04-14，缺 MCP / 认证 / 监控 / KYC 等多条本页已有规则，且含 `storage.googleapis.com`（范围过大，会把大量无关 Google 存储流量打到同一出口）。
- `VPSDance/ai-proxy-rules` 引用了本页且内容更全，但也含 `storage.googleapis.com` 以及不少本页没有的额外域名。

如果以后作者自己提供了稳定的 `.list` / raw 链接，应优先改用官方源，不必继续维护这份镜像。

## 仓库

规则托管在 https://github.com/Lau0x/claude-rules 。本仓库只放规则和同步脚本，不放完整客户端配置，也不放节点或订阅信息。
