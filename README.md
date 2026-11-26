# Dafeiyu-Pet-actions

Dafeiyu-Pet 专用**公开动作资源仓**。托管动作帧、清单、来源、许可及资源转换/同步脚本；**不含插件源码、DEB或凭据**。下载不需要 GitHub Token。

## 全量动作与自动更新

- 上游：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。首轮清点为 **114 个 WebM 动作**，并非仅现有106个GIF目录。
- [总目录 catalog.json](catalog.json)记录每项名称、稳定ID、来源提交/哈希、帧数、时长、独立ZIP地址与校验和。新动作按 **12fps、静态WebP Q85、原640×360透明画布**转换，不缩放、不裁切、不AI放大、不统一强制120帧/10秒。
- 新转换按源视频实际时长采样；多数源为24fps/10.042秒，因此可能有121帧、最后一帧为短帧。悠闲哼歌已从同一上游原视频重新转换为121帧/10042ms，配方与其余113动作完全一致，不再保留旧版特殊时序。
- GitHub Actions [自动同步工作流](https://github.com/taikulayyds/Dafeiyu-Pet-actions/actions/workflows/sync-upstream.yml)配置为**每6小时检查一次**，也可手动运行。定时仅在仓库变量`ACTION_SYNC_ENABLED=true`后执行；首次上线先运行完整`preview`（只转换验证，不发布Release、不提交帧），检查容量与耗时后再运行`publish`并启用定时。手动运行默认`preview`。GitHub定时可能延迟；公开仓长期无活动时平台可能自动停用定时，届时需重新启用。无需手机或Minis保持运行。
- **整批发布硬门禁**：即使直接选择`publish`或定时触发，也先在同一固定上游commit/工具配方下完成全部待变更动作的转换、独立内容校验及目标修订逐文件比对；全部通过后才允许首次Release上传或新增帧目录。后续动作在预检中失败时，本轮不会提前发布前几个动作。既有未变更条目仍须通过本地目录完整校验。
- 每轮先锁定上游commit；仅对新增或源内容/转换配方变化的动作重新转码。新动作自动取得稳定ASCII ID，既有114项ID已冻结。每个ZIP及帧修订使用内容哈希命名，**不覆盖旧Release或历史帧**；上游删除只标记`missing-upstream`。
- 许可、署名段落变化或新增许可文件时**失败关闭，等待人工复核**。透明度、全帧哈希、ZIP字节一致性、8MiB包上限等未通过时不更新目录，不自动降低画质。首次转换/上传失败可能留下已验证的不可变Release，重跑可复用，不能当作目录发布已完成。
- 失败详情见Actions日志；本仓不自动改插件、安装包或设备。

### 当前插件兼容范围（重要）

所有114动作均为统一新版逐帧格式，需要支持通用多动作导入的插件。旧 Build85 固定单动作导入器不兼容；不要把新包改名伪装为旧版。`build85Compatible=false` 不等于新插件不支持。此次只更新动作资源仓，不修改插件或DEB。

### 格式与审计

新包包含`manifest.json`、`LICENSE.txt`和`frames/000.webp`起的完整静态帧，记录每帧毫秒时长、文件大小与SHA256。转换后由独立Pillow/libwebp逐帧真实解码，检查静态WebP、画布、透明通道及非全透明内容；另行ffprobe探测源时长，逐项比对12fps时序与完整受信许可正文，并非只相信自报哈希。当前源时长对应：10042ms→121帧、5125ms→62帧、4917ms→59帧；距12fps边界≤0.5ms的容器舍入不制造零时长尾帧，总毫秒时长不变。ZIP不含目录条目、Zip64、脚本或外部帧URL。所有源固定提交下载并验证Git blob；转换不会执行上游代码。已退役旧包的历史指纹与验证替代记录见[legacy-v1.json](legacy-v1.json)（仅审计记录，不含旧资源下载），许可变动策略见[sync-policy.json](sync-policy.json)。

## 悠闲哼歌：统一标准版

- 配方：`webp-q85-alpha-12fps-v2`；640×360透明，Q85、12fps，121帧，总长10042ms，末帧42ms。保留上游完整时长，无缩放裁剪。
- [新版ZIP](https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/download/youxian-hengga-12fps-8bc1e60ae6e42912/youxian-hengga-12fps-8bc1e60ae6e42912.zip) · [新版Release](https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/tag/youxian-hengga-12fps-8bc1e60ae6e42912)
- [manifest](actions/youxian-hengga/revisions/8bc1e60ae6e42912/manifest.json) · [逐帧目录](actions/youxian-hengga/revisions/8bc1e60ae6e42912/frames/)
- ZIP：2653717 bytes；SHA256 `8bc1e60ae6e42912d5e0ea9b9fd4a6babc50f8aad068bcdbe9cd8d34f9c5b412`。
- 已对原视频执行两次独立转换，完整ZIP字节相同；目录仍114动作，其他113动作的条目及资源不变。
- 用户于2026-11-26授权在新版验证上线后删除旧v1 Release/ZIP与当前源码旧123资源。旧固定下载链接失效；Git历史与指纹审计保留，不重写历史。后续同步拒绝重新激活旧版。

## 署名与许可（不是素材MIT）

原动画作者/项目：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，固定提交 `fb38ce620576f5ed3735a99536d2b1a4a9eb313f`，源文件 `dsh-pet/assets/webm/悠闲哼歌.webm`。
源SHA256：`358905afc2c81a9ed478bcf121a8f2f19276766b1d382d0067456bd57d1d7dbb`。

形象为社区二创DeepSeek“大肥鱼”（鲸鱼娘），**非DeepSeek官方形象**；原型“溟月”来自B站[@上善无形](https://space.bilibili.com/4456176)，DeepSeek版女仆鲸鱼娘设计来自[@ZipZipPipe](https://space.bilibili.com/4168597)。[角色授权声明](https://www.bilibili.com/opus/1247156596114456581)。转帧打包发布：taikulayyds / Dafeiyu-Pet-actions。

素材及派生帧遵循 **CC BY-NC-SA 4.0：署名、非商业、相同方式共享**。请保留原作者、来源、修改说明与许可。上游MIT只用于代码，不意味着本素材可商用。

完整条款、上游原文及修改说明见[LICENSE.txt](LICENSE.txt)；[上游适用NOTICE](licenses/upstream-NOTICE.md)、[上游完整软件MIT](licenses/upstream-LICENSE-MIT.txt)、[上游插件README](licenses/upstream-plugin-README.md)均原文保留。依据上游及角色公开声明再分发，不构成额外商业授权、官方背书或对完整权利链的独立保证。
