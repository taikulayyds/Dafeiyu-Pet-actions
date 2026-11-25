# Dafeiyu-Pet-actions

Dafeiyu-Pet 专用**公开动作资源仓**。托管动作帧、清单、来源、许可及资源转换/同步脚本；**不含插件源码、DEB或凭据**。下载不需要 GitHub Token。

## 全量动作与自动更新

- 上游：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)。首轮清点为 **114 个 WebM 动作**，并非仅现有106个GIF目录。
- [总目录 catalog.json](catalog.json)记录每项名称、稳定ID、来源提交/哈希、帧数、时长、独立ZIP地址与校验和。新动作按 **12fps、静态WebP Q85、原640×360透明画布**转换，不缩放、不裁切、不AI放大、不统一强制120帧/10秒。
- 新转换按源视频实际时长采样；多数源为24fps/10.042秒，因此可能有121帧、最后一帧为短帧。原悠闲哼歌v1保留120帧/10000ms的兼容时序，不被批处理覆盖。
- GitHub Actions [自动同步工作流](https://github.com/taikulayyds/Dafeiyu-Pet-actions/actions/workflows/sync-upstream.yml)配置为**每6小时检查一次**，也可手动运行。定时仅在仓库变量`ACTION_SYNC_ENABLED=true`后执行；首次上线先运行完整`preview`（只转换验证，不发布Release、不提交帧），检查容量与耗时后再运行`publish`并启用定时。手动运行默认`preview`。GitHub定时可能延迟；公开仓长期无活动时平台可能自动停用定时，届时需重新启用。无需手机或Minis保持运行。
- **整批发布硬门禁**：即使直接选择`publish`或定时触发，也先在同一固定上游commit/工具配方下完成全部待变更动作的转换、独立内容校验及目标修订逐文件比对；全部通过后才允许首次Release上传或新增帧目录。后续动作在预检中失败时，本轮不会提前发布前几个动作。既有未变更条目仍须通过本地目录完整校验。
- 每轮先锁定上游commit；仅对新增或源内容/转换配方变化的动作重新转码。新动作自动取得稳定ASCII ID，既有114项ID已冻结。每个ZIP及帧修订使用内容哈希命名，**不覆盖旧Release或历史帧**；上游删除只标记`missing-upstream`。
- 许可、署名段落变化或新增许可文件时**失败关闭，等待人工复核**。透明度、全帧哈希、ZIP字节一致性、8MiB包上限等未通过时不更新目录，不自动降低画质。首次转换/上传失败可能留下已验证的不可变Release，重跑可复用，不能当作目录发布已完成。
- 失败详情见Actions日志；本仓不自动改插件、安装包或设备。

### 当前插件兼容范围（重要）

**Build85仅支持固定的「悠闲哼歌v1」导入**。全量动作仓就绪不等于插件已支持114个动作；其余条目`build85Compatible=false`，需要后续独立扩展多动作导入、存储/索引和播放器接入。不要把新包改名伪装为悠闲哼歌，也不要依靠导入新包覆盖插件现有动作。

### 格式与审计

新包包含`manifest.json`、`LICENSE.txt`和`frames/000.webp`起的完整静态帧，记录每帧毫秒时长、文件大小与SHA256。转换后由独立Pillow/libwebp逐帧真实解码，检查静态WebP、画布、透明通道及非全透明内容；另行ffprobe探测源时长，逐项比对12fps时序与完整受信许可正文，并非只相信自报哈希。当前源时长对应：10042ms→121帧、5125ms→62帧、4917ms→59帧；距12fps边界≤0.5ms的容器舍入不制造零时长尾帧，总毫秒时长不变。ZIP不含目录条目、Zip64、脚本或外部帧URL。所有源固定提交下载并验证Git blob；转换不会执行上游代码。既有测试包的逐文件指纹见[legacy-v1.json](legacy-v1.json)，许可变动策略见[sync-policy.json](sync-policy.json)。

## 首包：悠闲哼歌（youxian-hengga v1）

- [导入ZIP](https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/download/youxian-hengga-v1/youxian-hengga-webp-frames-v1.zip) · [Release](https://github.com/taikulayyds/Dafeiyu-Pet-actions/releases/tag/youxian-hengga-v1)
- [120张逐帧浏览](actions/youxian-hengga/frames/) · [manifest](actions/youxian-hengga/manifest.json) · [catalog](catalog.json)
- 大小：**2678259 bytes**
- SHA256：`1719e2260ab889844c9f6ac271c133b1d11ed91087002627960d74146e08d256`
- 120张静态有损WebP，quality=85；原始640×360透明画布，12fps采样，未AI放大/锐化、未缩放裁切。
- 播放时序逐项沿用原动作：80ms×80帧、90ms×40帧，总长10000ms；并非动画WebP测试文件的83/84ms。
- ZIP严格122个普通文件：manifest.json、LICENSE.txt、frames/000.webp…119.webp；无目录条目、Zip64、加密或数据描述符，仅普通Deflate，压缩及解压均小于8MiB。
- ZIP只放Release附件，不重复提交进Git；仓库逐张帧与ZIP帧完全相同。

## 署名与许可（不是素材MIT）

原动画作者/项目：[PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，固定提交 `fb38ce620576f5ed3735a99536d2b1a4a9eb313f`，源文件 `dsh-pet/assets/webm/悠闲哼歌.webm`。
源SHA256：`358905afc2c81a9ed478bcf121a8f2f19276766b1d382d0067456bd57d1d7dbb`。

形象为社区二创DeepSeek“大肥鱼”（鲸鱼娘），**非DeepSeek官方形象**；原型“溟月”来自B站[@上善无形](https://space.bilibili.com/4456176)，DeepSeek版女仆鲸鱼娘设计来自[@ZipZipPipe](https://space.bilibili.com/4168597)。[角色授权声明](https://www.bilibili.com/opus/1247156596114456581)。转帧打包发布：taikulayyds / Dafeiyu-Pet-actions。

素材及派生帧遵循 **CC BY-NC-SA 4.0：署名、非商业、相同方式共享**。请保留原作者、来源、修改说明与许可。上游MIT只用于代码，不意味着本素材可商用。

完整条款、上游原文及修改说明见[LICENSE.txt](LICENSE.txt)；[上游适用NOTICE](licenses/upstream-NOTICE.md)、[上游完整软件MIT](licenses/upstream-LICENSE-MIT.txt)、[上游插件README](licenses/upstream-plugin-README.md)均原文保留。依据上游及角色公开声明再分发，不构成额外商业授权、官方背书或对完整权利链的独立保证。
