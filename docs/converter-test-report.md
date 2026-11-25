# 转换器验收报告

## 范围

仅修改 `/var/minis/shared/dafeiyu-actions-expansion-20261125/converter`。主动作仓、v1帧和审计材料只读；无联网下载、无发布、无凭据访问。实际源来自既有 `/tmp/dafeiyu-audit-videos`，与审计SHA256和Git blob SHA1双重比对。

## 已完成结果

- 最终单元测试：**43 tests, OK，8.313s**，无skip、无expectedFailure。覆盖110×10042ms、3×5125ms、1×4917ms及主验证器采样函数直接交叉核对。
- 114源时序期望：121×110 + 62×3 + 59 = **13555帧**；总时长 **1124912ms**；三类末帧分别42、42、84ms。4917ms没有第60帧零尾。
- 真实合成源：640×360、24fps VP9-alpha，500ms；转换后6帧/500ms。源libvpx解码alpha值为0、129、255（输入PNG的128经VP9像素格式链量化为129）；每张WebP的alpha与源解码逐像素完全相等。所有输出经过主仓真实Pillow/libwebp独立解码和完整校验。
- 真实完全不可见VP9-alpha源：被明确拒绝，不产生动作输出；不是把全透明坏帧作为“保留透明”通过。
- 上游固定提交：`fb38ce620576f5ed3735a99536d2b1a4a9eb313f`。
- 代表源“工作状态-垂头叹气冒汗”：源SHA256 `ab00c844e62221905574b4c070e0d7b6fa14fb2afa1064c27a160f10e25f7196`；两次独立强制转码均59帧/4917ms、末帧84ms、alpha范围0..255。两份ZIP都是1271662字节，SHA256均为 `95bd0aabbf327857fa6038815aadde4dec50081da33055fac962135ff06e9edf`，逐字节一致。逐帧mtime扰动后重打包仍同SHA。两次均通过主仓最新真实验证器。
- 普通10042ms代表源“三球抛接”：源SHA256 `a64c08a4b7e5c3549616faf9dcd418e17f156b4b72d906646e4d57067df70d23`；两次独立强制转码均121帧/10042ms、末帧42ms、alpha范围0..255。两份ZIP都是2408585字节，SHA256均为 `eee4f088d8d11e06b6ded7b618d994d189e7b3b41e9e0d223c4db8e47db87381`，逐字节一致。mtime扰动重打包同SHA；两次均通过主仓最新真实验证器。
- 完整真实集成验收 **status=passed，耗时371.84s**；没有遗留后台转换任务。主仓v1只读指纹检查 **123文件全部通过**。
- 13555帧是114源全部独立归一化的理论/单测总数。线上继续保留原v1的120帧而不重建其121帧新版本，因此本轮线上总帧预计 **13554**；不得据13555覆盖旧v1。

## 修复

缓存补齐转换器源码、许可全文和版本指纹，复用时核对title/commit/source_file/clip，修复同源不同标题/提交/许可被误用的问题。补齐ZIP/动作路径与源/许可重叠、软链祖先、每帧256KiB、严格时序和缺失ZIP检查。逐帧拒绝全透明不可见输出；VP9标签从stream.tags正确读取；仍强制libvpx-vp9解码。备份ZIP失败、安装ZIP失败都已故障注入验证会恢复旧目录和ZIP。新增iSH共享挂载兼容的私有暂存清理。媒体子进程不继承凭据/代理/loader环境。

## 可重现性边界与首次实验纠正

首次双构建时父流程并行修改了主仓LICENSE，导致ZIP不同；逐帧WebP和manifest完全一致，差异仅许可正文。已把合法许可输入冻结在converter/evidence内，再强制双转码验证。固定输入不是仅指视频，还包括标题、commit、许可及工具链。验证时显式传受信许可快照给主仓最新 `validated_output(expected_license_bytes=...)`。ffmpeg/ffprobe为Linux6.1.2，cwebp1.4.0；同名iOS桥接ffmpeg不含libvpx，测试使用已有 `/tmp/dfy-ffmpeg`、`/tmp/dfy-ffprobe`。

## 交付复制清单与限制

复制 `scripts/convert_action.py`、`tests/test_converter.py`、`tests/test_transactions.py`；可选复制 `tests/run_integration.py`。README可纳入主仓 `docs/converter.md`，不要覆盖现有主README。现有 `sync_upstream.py` 的CLI调用及JSON消费字段无需改名。不要复制evidence、缓存、history、日志或pycache。

主仓最新校验器的逐帧时序、独立WebP解码和许可全文门禁已用于真实验收；转换器内部WebP检查是结构自检，不能替代独立解码。没有运行114全量媒体转码，也没有证明全量耗时/总容量/全部源逐帧均合格；需Linux CI全量dry-run。不保证不同libvpx/libwebp/zlib版本产生相同字节，不支持同一输出并发构建；失败回滚不等于掉电原子或跨文件系统事务。
