# 全量动作转换器：12fps / WebP Q85 / 原时长

本目录是隔离交付区。不会修改主动作仓、运行上游脚本、访问凭据或发布网络资源。仅需把下面列出的源码复制至主仓；不要复制 evidence 或历史输出。114 个源的全量媒体转换没有在手机执行。

## 依赖与兼容入口

Python 3.10+；ffmpeg/ffprobe 必须包含 **libvpx-vp9 解码器**；cwebp/libwebp。转换脚本本身只使用 Python 标准库。独立集成验收另需 Pillow，使用主仓 `scripts/sync_validation.py` 的真实校验函数。

```sh
python3 scripts/convert_action.py \
  --source /path/input.webm --out-dir /path/output/action-id \
  --clip action-id --title '动作名' \
  --source-commit 40位完整小写提交SHA \
  --source-file 'dsh-pet/assets/webm/动作名.webm' \
  --license /path/repo/LICENSE.txt
```

这正是 `sync_upstream.py` 使用的参数契约。stdout 只输出 JSON；错误写 stderr 并返回 2；JSON 保留 `manifest`、`action_dir`、`zip.path/bytes/sha256`、`source_probe.duration_ms/alpha_min/alpha_max` 等主仓所需字段。可选参数 `--ffmpeg`、`--ffprobe`、`--cwebp`、`--zip-out`、`--force`、`--id-map` 保持可用。

默认 ZIP 为动作目录的同级 `<clip>-webp-frames-v1.zip`。主验证器要求同级 ZIP，因此对接同步时不要设置其他位置的 `--zip-out`。上游溯源路径应使用上述 `dsh-pet/assets/webm/<filename>.webm` 规范。输出默认是不可见暂存后替换；已存在的输出先移到 `.history/<clip>/<digest>/`，不会删除旧 v1。正式同步仍应写临时目录，再由主仓编排器新增不可变 revision，不要直接对旧 v1 调用转换器。

## 图像、透明与时序契约

- 只接收 VP9、640×360、正时长且不超过60秒的源；不缩放、不裁剪，不自动降画质。静态 WebP：`cwebp -q 85 -alpha_q 100 -m 4 -metadata none -exact`。
- 输入必须显式选择 `-c:v libvpx-vp9`；ffprobe 报 `yuv420p` 并不代表源没有 alpha。普通 VP9 解码器可忽略 WebM alpha，因此不能代替 libvpx。
- 解码为 RGBA PNG，逐帧扫描 alpha；完全不透明的动作、全透明不可见帧均拒绝。透明背景与半透明边缘应保留；“正确透明解码”不是接受完全不可见的成品。
- `tpad=stop_mode=clone:stop_duration=0.084,fps=12:start_time=0:round=near`，再显式限制输出帧数，避免 fps 尾端舍弃原时长。
- 原时长规范化为源 probe 的整数毫秒。取 `duration_ms/1000*12` 的向上整数；距整数 tick 不超过0.5ms时贴近该 tick。非末帧时长为 `round((i+1)*1000/12)-round(i*1000/12)`，末帧补齐精确原毫秒时长。与主仓最新验证器的 sampling_count/累计边界规则一致；末帧额外1ms仅在总时长仍匹配帧数时允许。

| 源数量 | 时长ms | 输出帧数/源 | 末帧ms |
|---:|---:|---:|---:|
|110|10042|121|42|
|3|5125|62|42|
|1|4917|59|84|

独立归一化全部114个源时总计 **13555 帧，1124912ms**。4917ms 是59/12秒的容器毫秒表示，不产生第60帧零尾。**线上保留既有v1，不重建其原120帧，因此本轮线上合计预计13554帧**；13555是全部源都按新规则转换的理论/单测总数，不是要求覆盖旧v1。

## 确定性、缓存与失败边界

ZIP 成员顺序固定为 manifest、LICENSE、递增帧；所有成员使用1980-01-01 00:00:00、Unix普通文件0644、DEFLATE level9、空extra/comment。文件mtime、权限、临时目录与机器路径不写入包内。JSON无构建时间字段。双构建必须固定源、commit、标题、许可全文以及工具链；改变 LICENSE 是输入变化，理应改变ZIP SHA。

缓存指纹包含源SHA、转换器源码SHA、工具版本、许可SHA及profile；复用前还核对标题、commit、source_file、完整帧和ZIP。修复了旧实现忽略标题/提交/许可导致错误复用的问题。强制重建仍保留旧输出。ZIP备份或安装失败会恢复旧动作目录和ZIP；这不是跨文件系统、掉电或多进程并发事务保证。**同一输出不要并发运行**；输出与源/许可重叠、软链输出及软链父目录均拒绝。

每帧结构/尺寸/静态WebP、SHA、字节数、12fps时间边界和包成员闭合集均检查；每帧≤256KiB，ZIP和展开包均≤8MiB。转换器自检是结构检查，不声称能独立证明任意恶意WebP位流可解码；正式发布必须保留主仓 Pillow/libwebp 的独立逐帧解码与受信许可全文比较。所有媒体子进程使用最小环境，不继承代理、令牌、PYTHONPATH和LD_PRELOAD。

## 测试与iOS注意事项

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

快速测试无需联网、无需编解码二进制，覆盖114源时序、0.5ms边界、PNG五种过滤器、ZIP mtime/权限扰动、安全路径、CLI兼容、缓存失效及故障回滚。事务测试的WebP是显式标记的结构mock，不当作真实解码证据。

集成测试显式运行（不会随unittest自动运行）：

```sh
CONVERTER_FFMPEG=/path/ffmpeg-linux \
CONVERTER_FFPROBE=/path/ffprobe-linux \
CONVERTER_MAIN_REPO=/path/main-repo \
CONVERTER_AUDIT_DIR=/path/audit \
CONVERTER_SOURCE_DIR=/path/cached-webm \
PYTHONDONTWRITEBYTECODE=1 python3 tests/run_integration.py
```

集成测试生成0.5秒真实VP9-alpha源，检查每张输出WebP alpha与libvpx源解码逐像素一致，再以公开上游固定commit的4917ms及10042ms代表源分别强制双构建，并调用主仓真实验证器。源SHA256与Git blob SHA1交叉核对。结果写入 `evidence/integration-results.json`；原始日志 `evidence-integration.log`、`evidence-unit.log`。它冻结 `evidence/approved-license-snapshot.txt`，防止主仓并行修改许可造成伪非确定性；要更换可信许可输入，先明确删除该快照。既有ZIP与原始媒体不纳入源码交付。

iOS 的 `ffmpeg` 名称可能被桥接到不含libvpx的原生构建。请使用已有 `/tmp/dfy-ffmpeg`、`/tmp/dfy-ffprobe` 等不同名称的Linux副本，或上述显式参数；不应删除或覆盖系统工具。本机实测Linux ffmpeg/ffprobe 6.1.2、cwebp 1.4.0。iSH共享挂载的stat/fstat device id存在差异，私有暂存清理使用不跟随软链的路径遍历以规避Python fd-rmtree误判。

## 复制清单

1. `scripts/convert_action.py` → 主仓同路径（核心，主同步脚本无需修改参数）。
2. `tests/test_converter.py`、`tests/test_transactions.py` → 主仓同路径。
3. `tests/run_integration.py` → 主仓同路径（可选真实媒体验收工具，配置外部审计/源路径）。
4. 本README可作为主仓 `docs/converter.md`；不要覆盖现有主README。

不复制 `evidence/`、`.converter-state/`、`.history/`、日志、临时目录或 `__pycache__/`。工具版本变化可能改变WebP/DEFLATE结果；本验收证明固定工具链/固定输入重跑一致，不承诺不同libvpx/libwebp/zlib版本逐字节一致。完整114源的总包体积、耗时、全部源可解码性和最终发布仍需在合适的Linux CI执行离线dry-run后独立审核。
