# CHANGELOG

## 0.7.0 — 2026-10-27

提速 + 「唱多长」三种模式。

### 提速

- **先切再分离（默认）**：整首混音上用能量包络挑「目标时长 + 15 秒」的候选窗口，
  只切这一段去分离，再在分离出的人声里精挑最终段。三四分钟的歌不再整首过分离模型。
  实测 4:39 的歌 **72.2 秒 → 32.6 秒**（副歌 45 秒模式）。
- **常驻 worker**（`resident_worker.py`）：分离 / 去混响 / 变声共用一个常驻子进程，
  stdin/stdout 一行一个 JSON。省掉每步重新 import（每次 3~5 秒，三步 10~15 秒）。
  常驻进程没起来 / 崩溃 / 超时自动退回旧的 `sep_worker.py` / `rvc_worker.py`，
  日志记 `worker=fallback`；`CHORUS_RESIDENT=0` 可整开关掉。
- **并行**：分离完成后「去混响（人声）」与「伴奏切段 + 移调」用线程池同时跑。
- 新增 `pipeline.probe_duration()`（ffprobe 量时长）、`CHORUS_MARGIN_SECONDS`（默认 15）。

### 新增

- **`render_mode` 参数**：`chorus`（默认，45 秒副歌）/ `custom`（`chorus_seconds`，20~180）/
  `full`（整首翻唱，分离整首、变声整首，耗时可能是副歌模式的 5~10 倍、显存占用更高）。
  已加进 `app.py` 的 `convert()` 形参和 `_hidden_inputs()`（label 同名，排在 `inst_volume_db` 之后）；
  `/convert` 现在 26 个参数。
- `chorus.pick_window()` 支持自定义 `min_seconds` / `max_seconds`（以前写死 40~50 秒，
  自定义 30 秒或整首模式会被这组钳位改坏）。
- 缓存 key 加入 `mode` 与目标时长：同一首歌 chorus / custom / full 不再互相命中。

### 回退

- 新顺序任何一步报错（切候选、分离、精挑）自动退回「整首分离再切」，日志 `fallback=full_separation`。

### 实测（同一首 4:39 的 mp3，模型 Yuuka.pth，key_shift=0）

| 场景 | 耗时 | 成品 |
| --- | --- | --- |
| 改前（0.6.0，整首分离） | 72.2 s | 44.45 s wav，峰值 -1.5 dB，平均 -16.1 dB |
| `chorus`（45 s） | 32.6 s（HTTP 端到端 34.1 s） | 45.0 s wav，峰值 -1.5 dB，平均 -15.9 dB |
| `custom` 30 s | 27.1 s（HTTP 端到端 27.9 s） | 30.8 s wav，峰值 -1.5 dB，平均 -15.8 dB |

三段都过了 `py_compile`、ffprobe 时长与 `volumedetect` 峰值校验。
`full` 模式拿 100 秒素材跑通：**52.7 秒**产出 100.0 秒整首 wav（峰值 -1.5 dB），
长度涨到三四分钟时变声和分离都会继续变慢，所以插件侧默认仍是副歌模式。

## 0.6.0 — 2026-10-27

### 变更
- 分离链换成 PyMSS Studio 那套：becruily_deux 整首分离 → 挑 45 秒副歌 → anvuew 22.505 去混响 → RVC → 混音。
- 新标签「去伴奏（becruily deux）」「去混响（anvuew 22.505）」加进 SEP_TASKS / DEREVERB_TASKS / sep_worker TASKS / PYMSS_LABELS，config.py 两个默认值同步。
- 实测整链 74 秒（原链 159 秒），成品 44.45s、44.1k 立体声、mean -16.1dB。

### 说明
- 去混响模型在 RVC 里必须按 bs_roformer 加载（yaml 是 freqs_per_bands，mel_band 会报 unexpected key）。
- becruily_deux 的 stem 键是大写 Vocals/Instrumental，desired/secondary_stem 跟着大写，文件名后缀仍小写。
- 伴奏音量默认从 -2dB 调到 -6dB（人声更突出）。

## 0.5.0 — 2026-10-27

### 变更
- **不再把 RVC 路径写死在代码里**：`config.py` 的 `RVC_ROOT` 只认环境变量
  `CHORUS_RVC_ROOT`，没设就带一段说明直接退出。公开仓库里不再出现任何本机路径。
- 新增 `config.check_paths()`：启动前把 runtime / ffmpeg / 模型目录 / rubberband+SNDFILE
  一次检查完，问题列表一起报，不会带着半个环境跑到一半再炸。

### 新增
- `scripts/start.ps1`：启停 + 状态三条命令，自动设 `NO_PROXY`（不设会被系统代理坑）、
  等端口就绪、日志写到 `_work\mw.log` / `_work\mw.err`。
- 接进 AstrBot 的一键启动器：AstrBot 起来后检查 3333，没监听就拉起来，
  `-Status` / `-Stop` 也带上了这个服务。

## 0.4.0 — 2026-10-27

真跑通插件调用的一版。之前服务端跑 Gradio 3.14，插件（gradio_client 2.7.2）连不上，
所有「参数对上了就能用」的推断都不成立。

### 修复
- **服务端升到 Gradio 4.44.1（sse 协议）**：Gradio 3.x 的 ws 队列协议被新版
  gradio_client 直接拒绝（`Unknown protocol: ws`），只有 4.x 的 sse 能让插件按
  kwargs 传参。
- 25 个隐藏组件全部补 `label`，Gradio 4 的 `/info` 靠它生成 `parameter_name`。
- `gr.JSON` 会让 `/info` 抛 `argument of type 'bool' is not iterable`，
  给 `gradio_client.utils.json_schema_to_python_type` 加了兜底。
- 本机系统代理导致 Gradio 4 启动时误判 localhost 不可访问，把
  `gradio.networking.url_ok` 钉成 True。
- `/cache_info` 不再返回绝对路径：Gradio 4 会把存在的路径当文件去下载，客户端收 403。

### 新增
- `rvc_worker.py`：变声也改子进程（RVC 运行时 + gradio_client 0.2.9）。
  两套 gradio_client 只能用进程隔离，父进程专心做 sse 服务端。
- `pitch_shift()`：音高变换改用 rubberband，`-3 --pitch N`，时长和采样率都不变。
  45 秒音频升/降 2~12 个半音 4~8 秒完成；旧的 ffmpeg `asetrate` 方案同素材 600 秒超时。
- `tools/check_plugin_compat.py`：插件升级后一条命令比对参数表。
- `scripts/fetch_rubberband.ps1`：一键下载 rubberband.exe + sndfile.dll 到 `bin/`。
- `.gitignore`、`LICENSE`、`requirements.txt`、`requirements-worker.txt`。

### 变更
- `pipeline.mix()`：伴奏先移调到临时文件再 amix，`finally` 清临时文件。
- 启动方式：用系统 Python 3.12 跑 `app.py`（依赖在 `libs_web/`），
  RVC 相关重活仍走整合包自带的 `runtime\python.exe`。

## 0.3.0 — 2026-10-26

- 第一次跑通整条链路：整首分离 → 挑 45 秒副歌 → 软切换气点 → 去混响 → RVC → 混音。
- 分离/去混响改独立子进程 `sep_worker.py`（WebUI 的分离接口是流式的，客户端一断开就取消）。
- 副歌挑选：50ms RMS 包络 + 45 秒滑窗打分（能量 × 覆盖率），边界吸附到 ±4 秒内的换气点。
- 按 sha256 做结果缓存。
