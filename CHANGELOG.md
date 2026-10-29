# CHANGELOG

## 0.9.10 — 2026-10-29

**清理旧版本残留。**

- `app.py` 顶部还写着「AstrBot 翻唱插件（matsuko_cover）」，改成现在的插件名，
  上游名字只作为兼容说明保留；
- README 里「插件更新只覆盖 `data\plugins\astrbot_plugin_matsuko_cover`」是旧目录名，
  改成 `astrbot_plugin_chorus_cover`；
- 版本号 0.9.9 → 0.9.10。


## 0.9.9 — 2026-10-29

**混音默认加人声链（用户 A/B 通过后转正）。**

按「先对比、后改动」的规矩，先在桌面做了 A/B：同歌同段同模型，只差混音这一步。
用户听感确认「优化后的人声明显更好」，于是把这套设成默认。

- 人声：85Hz 高通去掉低频闷声；2.5:1 轻压缩把强弱拉稳（阈值 -18dB，增益衰减很小）；
- 伴奏：侧链压缩让位（1.8:1），人声出现时伴奏退一点，人声更贴脸；
- 整条链跟原有的混音在同一次 ffmpeg 渲染里完成，四十五秒片段耗时从 33 秒到 33 秒，
  基本可忽略；峰值仍归一化到 -1.5 dBFS；
- 开关保留：`CHORUS_VOCAL_CHAIN=0` 关掉人声链，`CHORUS_VOCAL_DUCK=0` 只关伴奏让位；
- 混音链与响度模式也进了缓存 key，改开关不会拿到旧成品；
- 版本号 0.9.8 → 0.9.9。


## 0.9.8 — 2026-10-29

**修 8G 卡上「提主旋律」OOM 导致的整首失败，并修好失败后的兜底进程。**

现象：日志报 `分离子进程失败（karaoke）：No module named 'sep_guard'`，用户在群里只看到失败。

两层原因：
1. 分离输入原本是「目标 45 秒 + 15 秒余量」= 60 秒，女声/和声多的歌在 8G 卡上
   被 OOM（当时只剩 2.9G 空闲，分离峰值要 3G+）。余量默认从 15 秒降到 6 秒，
   分离跨度从 60 秒降到 45 秒，峰值跟着降；
2. 真 OOM 之后走兜底进程 `sep_worker.py`，它 `chdir` 到 RVC 目录后 `import sep_guard`
   找不到（RVC 便携版 python 的 `sys.path[0]` 是 runtime 目录，不是脚本目录），
   于是把「显存不足」掩盖成「模块找不到」。现在脚本启动先把自己的目录塞进 `sys.path`。

另外：
- 提主旋律失败时自动退回「去伴奏」再试一次，不再直接判失败；
- 给 worker 进程加 `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`，减少显存碎片；
- 实测（GPT-SoVITS 与 RVC WebUI 都开着、空闲 2.9G）60 秒→45 秒跨度可稳定跑完，耗时 32 秒。


## 0.9.7 — 2026-10-29

**混音不再用 loudnorm 动态压缩，改成只做峰值归一。**

用户反馈「音质上下空间差、像被压过」。原因：混音尾部挂的是单遍
`loudnorm=I=-14:TP=-1.5:LRA=11`，单遍模式为了打到目标响度会动态压缩，动态范围被压扁。

- 改为两步：先混出临时文件，用 ffmpeg `volumedetect` 量峰值，再整体加一个固定增益到
  -1.5 dBFS。固定增益只改整体音量，不改变动态范围；
- 峰值测量失败时不做归一，保持原样，不退回动态压缩；
- 日志会打一行 `mix peak=… gain=…`，便于核对；
- 版本号 0.9.6 → 0.9.7。


## 0.9.6 — 2026-10-29

**确认默认走单段式：直接对混音跑「提主旋律」。**

- 试了两段式（先 BS-Roformer 分人声/伴奏，再从人声里拆主唱），同一首《雨爱》A/B 听不出差别，
  时间反而从 29 秒涨到 43 秒，所以默认保持单段。
- 两段式代码保留为可选实验：`CHORUS_TWO_STAGE=1` 打开，默认关。
- 版本号 0.9.5 → 0.9.6。


## 0.9.3 — 2026-10-29

**按音域自动移调（默认关闭）。**

- 新增 `pitch.py`：用自相关估源人声的中位基频，再算「挪到目标区间需要几个半音」，
  只依赖 numpy + soundfile，几十毫秒；带八度误判修正（男声 160Hz 被自相关测成 320Hz 的情况）。
- `pipeline.run()` 在 `key_shift=0` 且 `CHORUS_AUTO_PITCH=1` 时自动移调，人声和伴奏一起挪；
  `/convert` 的缓存键加入了 `auto_pitch` 和 `target_f0`，避免拿到旧的 0 调产物。
- 默认**关闭**（`CHORUS_AUTO_PITCH=0`）：实测同一首男声歌，A/B 听感上「不移调」比自动移调更贴模型，
  所以只把这个能力留在仓库里，需要时开环境变量。
- 可调：`CHORUS_TARGET_F0`（默认 240）、`CHORUS_MAX_AUTO_SHIFT`（默认 12）。
- 另：`harvest` 在本整合包里不支持（`infer/vc/pipeline.py` 只认 pm/rmvpe/fcpe），
  补装 pyworld 也没用，别再往这个方向试。

## 0.9.2 — 2026-10-29

**副歌挑选不再只看「哪段最响」。** 实测《晴天》这种主歌也有力气的歌，旧算法会切在
24 秒左右的主歌上，翻唱听起来像没进副歌。新算法在原来的「能量 × 覆盖率」之外加两项：

- **重复度**：把包络压成 2 秒一块的轮廓，看候选窗口和全曲其它位置的相关性——副歌会整段
  回来，主歌重复得更零碎；权重 `0.7 + 0.5 × 相关系数`。
- **位置微调**：靠后的窗口给一点点优势（`0.95 + 0.1 × 相对位置`），避免总是撞前奏/第一段。

只是打分公式的变化，没有引入任何模型，耗时仍在毫秒级；边界软切（找换气点）逻辑不动。

## 0.9.1 — 2026-10-28

**文档：把推荐的启动顺序写清楚。** 只有文档和版本号变化，代码逻辑没动。

- README 新增「推荐的启动顺序」：QQ → SnowLuma → GPT-SoVITS → 本中间层 → 网易云取歌 →
  AstrBot，并逐条说明为什么这么排（SnowLuma 要挂已登录的 QQ；AstrBot 最后起才能上线即用；
  SnowLuma 连不上 6199 会 5 秒重试；本中间层会自己拉起 7897）。
- 写明「建议用 `D:\QQBOT\启动\start.ps1` 一键启动，不要手动一个个开」，并解释手动启动最常漏
  3003 / 3333，表现为「能聊天但点歌、唱歌失败」。
- 记录启动器卡在 `[4/6]` 的根因（管道句柄被子进程 node 继承，父脚本等不到 EOF），
  提醒以后不要再给「会拉常驻进程的子脚本」接管道。
- `config.VERSION` → 0.9.1（下次重启中间层时启动日志与 `/cache_info` 会显示新版本）。

## 0.9.0 — 2026-10-28

**音色模型目录变成一等公民。** 以前 `assets\weights` 是写死的，插件设置页也没地方告诉
用户「模型该放哪」，只能猜。

### 变更

- 新增环境变量 **`CHORUS_WEIGHTS_DIR`**：音色模型目录可换；不设就是
  `<RVC>\assets\weights`。`config.WEIGHTS_DIR_SOURCE` 记录来源，启动日志和 `/model_info` 都会带出来。
- `ensure_rvc()` 拉起 RVC 时把 `weight_root=<WEIGHTS_DIR>` 写进子进程环境：
  RVC 的 `get_vc()` 拼路径用的是 `f'{os.getenv("weight_root")}/{模型名}'`，
  不传这个变量，换目录只是「列表变了、加载还是老地方」。
- 新增接口 **`/model_info`**（入参 `models_dir` 可选）：返回
  `dir` / `backend_dir` / `source` / `exists` / `count` / `models` / `matches_backend` /
  `rvc_port` / `rvc_running` / `index_dirs`，目录不存在、没有 `.pth`、或与后端目录不一致时带 `warning`。
- `list_models(directory=None)` 支持指定目录；`/show_model` 行为不变（仍读当前目录）。
- 启动日志多一行：`音色模型目录=…（来源：…），共 N 个 .pth；RVC 的 weight_root 会跟它保持一致`。
- `check_paths()` 的模型目录检查改为按实际目录报错（带上来源）。

### 实测

- 本机默认目录 8 个 `.pth`，`/model_info` 返回 `matches_backend=true`。
- 故意传 `assets\indices`：返回 `matches_backend=false` + 告警「这个目录不是 RVC 正在用的模型目录…」。
- 版本 0.9.0 启动正常，`/convert` 29 参数不变，插件 0.10.1 无需改动即可继续用。

## 0.8.1 — 2026-10-28

**显存保护 + 失败原因透传。** 0.8.0 及以前，一旦分离 OOM，插件只会报
「分离没有产出人声：…\sep_vocal」——用户看不出是显存不足，还白跑一次整首分离兜底。

### 变更

- 新增 `sep_guard.py`：RVC 的 `pymss_separate` 是生成器，**异常被它自己吞成文字、永不抛出**，
  所以改成两条判断：输出目录里有没有**新增音频文件**（确定的成败证据）+
  从输出文字里挑出 `OutOfMemoryError` / `没有找到可处理的音频文件` 那一行（给人看的理由）。
- `resident_worker._pymss` 与 `sep_worker.py` 都用这套判断：没有新产物就抛 `RuntimeError` /
  打印 `WORKER_FAILED` 并以非零退出，不再「什么都没做还回 ok=True」。
- `rvc._run_task` 新增 **OOM 自动重试一次**：认出错因是 OOM 后等
  `CHORUS_OOM_RETRY_WAIT_SECONDS`（默认 3 秒），换**全新进程**重跑——常驻进程 OOM 之后
  CUDA 上下文可能已经坏了，同一个进程再试也是白搭。普通失败仍按老规矩退回进程模式。
- 失败消息改成能行动的说法，例如：
  「显存不足（去伴奏（becruily deux） 这一步 OOM，现在空闲 1997MB）。PyMSS 分离峰值要 1.6GB 左右，
  8G 卡上被 OBS / 直播伴侣 / NVIDIA Broadcast / GPT-SoVITS 占住就会顶到 7.4G 以上。
  关掉这些程序，或者先停 GPT-SoVITS（占 2GB）再重试。原始错误：…」
  非 OOM 失败保留原文（截断到 600 字）。
- `pipeline.run` 在分离前**预检显存**：`rvc.ensure_vram()` 读 `nvidia-smi`，空闲低于
  `CHORUS_VRAM_MIN_FREE_MB`（默认 1800MB）就最多等 `CHORUS_VRAM_WAIT_SECONDS`（默认 10 秒），
  并把「显存只剩 XXXMB」通过 `progress` 报给上层；查不到显存（无独显 / 驱动异常）直接放行。
- 新增环境变量：`CHORUS_VRAM_MIN_FREE_MB`、`CHORUS_VRAM_WAIT_SECONDS`、
  `CHORUS_OOM_RETRY_WAIT_SECONDS`。
- 日志新增三类行：`vram free=…`、`worker=<resident|process> failed op=… reason=…`、
  `oom op=… free=…MB，Ns 后换新进程重试一次`。

### 实测（2026-10-28 01:57 那次真实失败）

- 真因确认：`tools/pymss_core/modules/bs_roformer/bands.py::_get_packed_layer_group_params`
  里 `torch.stack` 把所有频带权重再复制一份到 GPU 时 OOM；当时卡被占 5.8GB，分离峰值到 7.39/8.15GB。
- 同一段 60 秒音频换独立进程重跑：**13.7 秒成功**，证明模型和环境没坏，是显存瞬时不够。
- 新判定：把整份 `_work/resident.log` 喂给 `failure_reason()`，输出
  「显存不足（CUDA out of memory），分离子进程被系统拒掉了 + 原始那一行」。
- 坏输入（不存在的 wav）走 `_pymss`：现在抛 `RuntimeError: 找不到要分离的音频文件`，
  旧写法会返回 `ok=True`。
- 预检在当前 1999MB 空闲下判定 `ok（阈值 1800MB）`，不等待；把阈值临时提到 2500MB 时会等满
  并把「显存只剩 …」报上去。

## 0.8.0 — 2026-10-27

**参数打通**：0.7.0 之前 `/convert` 虽然收了 26 个参数，但 `pipeline` 里写死用
`config.DEFAULT_F0 / DEFAULT_INDEX_RATE / DEFAULT_RMS_MIX / DEFAULT_PROTECT`，
插件里改 `index_rate` **毫无效果**。0.8.0 起全部按请求解析并真正传下去。

### 变更

- `app.py` 的 `convert()` 把 `f0_method`（str）、`index_rate` / `rms_mix_rate` /
  `protect` / `inst_volume_db` / `vocal_volume_db`（float）、`filter_radius`（int）
  解析成最终值：缺省用 `config` 里的默认值，越界夹到范围内，非法值打一行日志后回落默认值。
- 一路传到 `pipeline.run` → `rvc.convert`（`f0_method` / `index_rate` / `rms_mix_rate` /
  `protect`）和混音 `mix`（`inst_volume_db` / `vocal_volume_db`）。
- **混音新增人声音量 `vocal_volume_db`（默认 0.0）**：人声轨乘 `10^(db/20)`，伴奏轨仍走
  `inst_volume_db`；两个都是 dB，负数是压小。
- **`filter_radius` 只收下并记日志，标注「暂未使用」**：RVC WebUI 这一版的
  `/infer_convert` 只有 9 个入参（speaker / input / pitch / f0 / index / index_rate /
  resample / rms / protect），根本没有 `filter_radius`，传不下去，所以它不会改变成品。
- `/convert` 新增 `rms_mix_rate`、`protect`、`vocal_volume_db` 三个参数（**26 → 29 个**），
  每个都带同名 `label`，`/info` 里能查到 `parameter_name`，插件才能用 kwargs 传。
- **缓存 key 加入** `f0_method` / `index_rate` / `rms_mix_rate` / `protect` /
  `inst_volume_db` / `vocal_volume_db`：改任何一项都会重新跑，不会再拿旧缓存糊弄。
- 启动日志多打一行「生效参数默认值」；每次请求打一行 `params ...`（`done` 行也重复最终值），
  对着日志就能核对实际用了什么。
- `config.py` 新增 `CHORUS_FILTER_RADIUS`、`CHORUS_INST_VOLUME_DB`、
  `CHORUS_VOCAL_VOLUME_DB` 三个环境变量。

### 实测（同一首 4:39 的 mp3，模型 `ainuo.pth`，chorus 模式，窗口 157.5–202.6 秒）

| 场景 | 成品 | 差异证据 |
| --- | --- | --- |
| `index_rate=0.3` | 45.0 s，RMS -16.27 dB，峰值 -1.78 dB | 与 0.9 比：波形相关 **0.9879**、200–8000 Hz 频谱平均差 **3.26 dB**，差分信号 -32.4 dBFS |
| `index_rate=0.9` | 45.0 s，RMS -16.26 dB，峰值 -1.72 dB | 同上 |
| 对照：0.9 再跑一次（换缓存 key 强制重跑） | 与上次**逐样本一致** | 波形相关 **1.0000**、频谱平均差 0.02 dB → RVC 推理是确定的，0.3/0.9 的差异确实来自参数 |
| `inst_volume_db=-6` | 峰值 -1.72 dB | <200 Hz 能量占比 **27.92%** |
| `inst_volume_db=-12` | 峰值 -1.51 dB | <200 Hz 能量占比 **18.03%**（相对降 35%），200–2000 Hz 占比 68.40% → 78.77%，波形相关 0.9455 |
| `f0_method=pm` | 45.0 s，RMS -16.27 dB | 与 `rmvpe` 波形相关 **0.3924**，两次日志分别 `f0=rmvpe` / `f0=pm`，都能跑通 |

> 本机 8 GB 显存同时被 OBS / NVIDIA Broadcast / 浏览器占着，连续跑几次后分离会报
> CUDA OOM（`分离没有产出人声`，`_work/resident.log` 里有 traceback）；关掉/重启 RVC WebUI
> 腾出显存后同一请求一次通过。这是环境资源问题，不是参数链路问题。

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
