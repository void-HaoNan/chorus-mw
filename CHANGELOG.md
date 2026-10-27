# CHANGELOG

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
