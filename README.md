# chorus-mw

RVC 翻唱中间层，给 AstrBot 的翻唱插件当后端用：
丢一首歌进去，出来一段翻唱成品（默认 45 秒副歌，也可以自定长度或整首翻唱）。

当前版本：`0.9.0`（0.8.0 打通了推理与混音参数；0.8.1 加了显存保护；0.9.0 补上音色模型目录 `/model_info` 与 `CHORUS_WEIGHTS_DIR`）。

默认后端插件是自维护分支 `astrbot_plugin_chorus_cover`（也可接上游 `astrbot_plugin_matsuko_cover`，
接口签名一致）。

整条链路（0.7.0 起）：**下歌 → 整首混音上挑候选副歌 → 只切这一段 → 只分离这一段 → 人声里精挑 → 去混响 ∥ 伴奏切段移调 → RVC 变声 → 混音 → 出 wav**

0.7.0 之前是「整首分离 → 再切 45 秒」，三四分钟的歌光分离就要二三十秒；
现在默认只分离 60 秒左右，同一首 4:39 的歌实测 **72.2 秒 → 32.6 秒**。

当前默认模型：分离用 `becruily_deux`，去混响用 `anvuew sdr 22.505`（0.6.0 起），换模型见文末「分离模型」。

0.8.0 起，插件设置里的 `f0_method` / `index_rate` / `rms_mix_rate` / `protect` /
`inst_volume_db` / `vocal_volume_db` 会真的传进 RVC 和混音（不再写死默认值），
细节和实测见下文「音色与音量参数」。

0.8.1 起，分离前会看一眼空闲显存，真 OOM 了会换新进程重试一次，并且把
「显存不足」的原文透给插件，不再含糊地报「分离没有产出人声」。见「显存保护」。

## 目录

```
chorus-mw/
├─ app.py             接口层（Gradio 4，监听 127.0.0.1:3333）
├─ pipeline.py        主流程：下载、挑窗口、切割、并行调度、混音、缓存
├─ chorus.py          副歌挑选（能量包络滑窗 + 换气点吸附）
├─ rvc.py             分离/去混响/变声的调度（常驻 worker 优先，失败退回子进程）
├─ resident_worker.py 常驻工作进程（分离/去混响/变声，一行一个 JSON）
├─ sep_worker.py      旧的分离/去混响子进程（兜底，每次起一个）
├─ rvc_worker.py      旧的变声子进程（兜底，每次起一个）
├─ config.py          路径与参数（RVC 路径**不写死**，只认 CHORUS_RVC_ROOT）
├─ scripts/
│  ├─ start.ps1            启停 + 状态（会自动设 NO_PROXY、等端口就绪）
│  └─ fetch_rubberband.ps1 下载 rubberband.exe + sndfile.dll 到 bin/
├─ tools/
│  └─ check_plugin_compat.py  插件升级后比对参数表
├─ bin/            rubberband.exe + sndfile.dll（音高变换）
├─ libs/           gradio_client 0.2.9（只给 worker 用，对 Gradio 3.14 的 ws 协议）
├─ libs_web/       Gradio 4.44 + 依赖（app.py 用，sse 协议）
├─ _work/          临时产物（resident.log 也在里面）
├─ output/         成品
└─ cache/          按哈希命中缓存
```

## 为什么分成两套 Python 依赖

| 角色 | Gradio 版本 | 队列协议 | 需要的客户端 |
| --- | --- | --- | --- |
| RVC WebUI（7897） | 3.14 | **ws** | gradio_client 0.2.9 |
| 本中间层（3333） | 4.44 | **sse** | gradio_client ≥1（插件侧是 2.7.2） |

gradio_client 2.x 读到 `protocol: ws` 会直接抛 `Unknown protocol: ws`；0.2.9 又只认 ws。
所以：**跟 WebUI 说话的事全部丢进子进程**（`resident_worker.py` 常驻，
失败时退回 `sep_worker.py` / `rvc_worker.py`，都用 RVC 运行时 + `libs`），
父进程只做 sse 服务端（系统 Python 3.12 + `libs_web`）。两边井水不犯河水。

## 三种翻唱时长（0.7.0 新增，`render_mode`）

| 模式 | 说明 | 分离多长 | 实测（4:39 的《失眠飞行》） |
| --- | --- | --- | --- |
| `chorus`（默认） | 只翻唱副歌片段，默认 45 秒 | 目标 + 15 秒 = 60 秒 | **32.6 秒**（改前整首分离 72.2 秒） |
| `custom` | 自定义 20~180 秒，用 `chorus_seconds` | 时长 + 15 秒 | `chorus_seconds=30` → **27.1 秒** |
| `full` | 整首翻唱：分离整首、变声整首 | 整首 | **明显慢得多，可能是副歌模式的 5~10 倍，显存占用也更高** |

`full` 不是「把 45 秒换成 279 秒」这么简单：RVC 变声和 BS-Roformer 分离的耗时随音频长度增长，长音频还会把
显存吃到接近上限后降速。4:39 的歌整首跑通常要几分钟到十几分钟，插件默认 `inference_timeout=300` 秒
很可能不够，要整首翻唱就把它和任务超时一起调大。

`chorus` / `custom` 的省时原理：先在**整首混音**上用能量包络挑一个「目标时长 + 15 秒」的候选窗口
（最长不超过整首），只切这一段去做分离，再在分离出的人声里精挑最终那段、把边界吸附到换气点。
新顺序任何一步报错都会自动退回「整首分离再切」，日志里记 `fallback=full_separation`。

## 音色与音量参数（0.8.0 起真的生效）

0.7.0 及以前 `/convert` 虽然收了这些参数，但 `pipeline` 里写死用 `config.py` 的默认值，
插件里改 `index_rate` 毫无效果。0.8.0 起全部按请求解析、一路传到 RVC 推理和混音：

| 参数 | 默认 | 范围 | 作用 | 调大 / 调小 |
| --- | --- | --- | --- | --- |
| `f0_method` | `rmvpe` | `rmvpe` / `pm` / `crepe` / `harvest` | 音高提取算法 | `rmvpe` 最稳；`pm` 最快、质量一般；`crepe` 更吃显卡；`harvest` 兜底 |
| `index_rate` | `0.75` | 0~1 | 特征检索比率，越高越贴模型音色 | 调大更贴音色但易生硬；调小更自由、可能走音 |
| `rms_mix_rate` | `0.35` | 0~1 | 人声音量包络贴合原唱的程度 | 调大更自然、人声可能被压小；调小更平更冲、易发闷 |
| `protect` | `0.33` | 0~0.5 | 保护清音/气声，减少撕裂 | 调大更安全、更少电流味；调小更贴音色、易沙 |
| `inst_volume_db` | `-6.0` | -20~0 | 伴奏音量（dB） | 调大伴奏更响；调小几乎只剩人声 |
| `vocal_volume_db` | `0.0` | -12~+12 | 人声音量（dB，0 为原样） | 调大人声更靠前；调小人声往后退 |

- 每次请求都会打一行 `[chorus-mw] params f0=... index_rate=... ...`，`done` 那行也会重复最终值，对着日志就能核对。
- 启动时另打一行「生效参数默认值」，那是没被请求覆盖时用的值（可用环境变量改，见下表）。
- **`filter_radius` 只收下并记日志（标注「暂未使用」）**：RVC WebUI 这一版的 `/infer_convert` 只有 9 个入参，
  没有 filter_radius，传不下去，所以它不会改变成品——别把它当成能调的东西。
- 这些参数和两条轨道的音量都进了缓存 key：改了就会重新跑，不会拿上次的成品糊弄你。

## 常驻 worker 与并行（0.7.0）

- `app.py` 启动时在后台线程里拉起一个常驻进程 `resident_worker.py`（`_work/resident.log` 是它的输出），
  分离 / 去混响 / 变声都发到这个进程里串行做。以前每一步都起新进程、每次重新 import torch 和
  `tools.pymss_webui`（实测每次 3~5 秒，三步就是 10~15 秒），现在只付一次。
- 常驻进程没起来、中途崩了或超时（`_work\resident.log` 里有 traceback），自动退回旧的
  「每次起一个进程」，日志记 `worker=fallback op=...`；连续两次失败会停用 120 秒再试。
- 分离完成后，「去混响（只需人声片段）」和「伴奏切段 + 移调」用线程池并行跑，不再串行等待。
- 代价：常驻进程会一直占着一份分离/去混响模型的显存（实测常驻后 GPU 占用 3.8 GB 左右）。
  显存紧就设 `CHORUS_RESIDENT=0` 退回每次起进程。


## 显存保护（0.8.1）

8 GB 卡上这套东西很容易把显存顶满：常驻程序（GPT-SoVITS 2.0 GB、RVC WebUI 0.8 GB、
NVIDIA Broadcast + MediaSDK 0.6 GB、QQ/浏览器/ToDesk 等）平时就占着 5.8 GB，
而 PyMSS 分离峰值还要再吃 1.6 GB 左右，实测峰值 **7.39 / 8.15 GB**。差一点就会 OOM。

0.8.1 做了三件事：

| 机制 | 行为 | 相关配置 |
| --- | --- | --- |
| 分离前预检 | 查 `nvidia-smi` 的空闲显存，低于阈值就先等，并把「显存只剩 XXXMB」通过进度报给插件 | `CHORUS_VRAM_MIN_FREE_MB`（默认 1800）、`CHORUS_VRAM_WAIT_SECONDS`（默认 10） |
| OOM 自动重试 | 失败信息里认出 OOM 后，等几秒换**全新进程**重跑一次（常驻进程 OOM 后 CUDA 上下文可能已坏） | `CHORUS_OOM_RETRY_WAIT_SECONDS`（默认 3） |
| 失败原因透传 | 报「显存不足（…）现在空闲 XXXMB + 原始 OOM 原文」，而不是「分离没有产出人声」 | — |

日志里能对上三行：`vram free=...`、`worker=<resident|process> failed op=... reason=...`、
`oom op=... free=...MB，Ns 后换新进程重试一次`。

查不到显存（没独显、驱动异常、`nvidia-smi` 不在 PATH）时预检直接放行，不会挡住正常唱歌；
等 10 秒还是不够也照样往下跑——真正缺显存时由重试 + 明确报错兜底，而不是无限等。

**给用户的话**：OOM 时先关 OBS / 直播伴侣 / NVIDIA Broadcast，或者临时停掉 GPT-SoVITS
（它一个人占 2 GB），再重试；不用重启 AstrBot。


## 依赖

| 依赖 | 位置 | 说明 |
| --- | --- | --- |
| RVC 整合包 | `config.RVC_ROOT` | 提供 `runtime\python.exe`、`ffmpeg.exe`、分离模型 |
| RVC WebUI | `http://127.0.0.1:7897` | 必须先启动；`app.py` 会自己拉起并等端口 |
| rubberband 4.0 | `bin\rubberband.exe` | 音高变换，**必须和 `sndfile.dll` 放一起** |
| Gradio 4.44 | `libs_web\` | `pip install --target libs_web -r requirements.txt` |
| gradio_client 0.2.9 | `libs\` | `runtime\python.exe -m pip install --target libs --no-deps -r requirements-worker.txt` |
| 分离模型 | RVC 的 `assets\pymss_weights\` | ckpt + yaml 成对放，且要在 `tools\pymss_webui.py` 的预设清单里登记才可选 |

> `huggingface_hub` 必须降到 0.35.x：gradio 4.44 还在用 `HfFolder`，hub 2.x 把它删了会直接 ImportError。

## 启动

先设置 RVC 整合包路径（代码里不写死任何本机路径）：

```bat
set CHORUS_RVC_ROOT=D:\你的路径\RVC20260718Nvidia50x0
python -X utf8 app.py
```

或者直接用脚本，它会做体检、拉起服务、等端口就绪：

```bat
powershell -ExecutionPolicy Bypass -File scripts\start.ps1          :: 启动
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Status  :: 看状态
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop    :: 停掉
```

跑 `app.py` 的 Python 只要 3.12 + 能 import `libs_web` 里的东西即可；RVC 相关的重活都在
`config.RVC_PYTHON`（整合包自带运行时）里跑。环境变量一览：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHORUS_RVC_ROOT` | 无，**必填** | RVC 整合包根目录 |
| `CHORUS_WEIGHTS_DIR` | `<RVC_RVC_ROOT>\assets\weights` | 音色模型目录（0.9.0）。改这里要重启中间层；启动时会把它作为 `weight_root` 传给 RVC 子进程 |
| `CHORUS_PYTHON` | PATH 里的 python | 跑服务端的解释器（start.ps1 用） |
| `CHORUS_PORT` | 3333 | 监听端口 |
| `CHORUS_HOST` | 127.0.0.1 | 监听地址 |
| `CHORUS_RUBBERBAND` | `bin\rubberband.exe` | 想用系统装的 rubberband 就指过去 |
| `CHORUS_RVC_PORT` | 7897 | RVC WebUI 端口 |
| `CHORUS_SEP_MODEL` / `CHORUS_DEREVERB_MODEL` | 去伴奏（becruily deux） / 去混响（anvuew 22.505） | 分离模型标签，要和 RVC 预设清单里的 label 完全一致 |
| `CHORUS_F0` / `CHORUS_INDEX_RATE` / `CHORUS_RMS_MIX` / `CHORUS_PROTECT` | rmvpe / 0.75 / 0.35 / 0.33 | 推理参数默认值（请求没传或传了非法值时用） |
| `CHORUS_FILTER_RADIUS` | 3 | 只用于日志/缺省解析；RVC WebUI 本版没有这个入参，暂不生效 |
| `CHORUS_INST_VOLUME_DB` / `CHORUS_VOCAL_VOLUME_DB` | -6.0 / 0.0 | 混音默认电平（dB） |
| `CHORUS_SECONDS` | 45 | 默认翻唱秒数（调用方不传 `chorus_seconds` 时用） |
| `CHORUS_MARGIN_SECONDS` | 15 | 先切后分离的余量：候选窗口 = 目标时长 + 这么多秒 |
| `CHORUS_RESIDENT` | 1 | 设为 `0` 关掉常驻 worker，退回每次起进程（省显存、但慢 10~15 秒） |
| `CHORUS_VRAM_MIN_FREE_MB` | 1800 | 分离前预检的空闲显存下限（MB）；本机常驻程序占得多，定太高会每首歌白等 |
| `CHORUS_VRAM_WAIT_SECONDS` | 10 | 显存不够时最多等多少秒；等不到也继续跑 |
| `CHORUS_OOM_RETRY_WAIT_SECONDS` | 3 | 真 OOM 后等几秒再换新进程重试一次 |

启动前会跑 `config.check_paths()`，缺 runtime / ffmpeg / 模型目录 / rubberband+dl 会一次列全并退出，
不会带着半个环境跑到一半再炸。

## 和 AstrBot 一起开机

`D:\QQBOT\启动\start.ps1`（用户自己的启动器）里加了一段：AstrBot 起来后检查 3333，
没监听就用 `CHORUS_RVC_ROOT` + `NO_PROXY` 拉起本服务，并把 `-Status` / `-Stop` 也接上了。
启动器现在管四件事：QQ → AstrBot → 本中间层（3333）→ GPT-SoVITS API（9880），
SnowLuma 收尾；本仓库只负责中间层自己，启动器那边是用户侧配置。

## 接口

| api_name | 说明 |
| --- | --- |
| `/convert` | 主入口，29 个参数，按名字传 kwargs，返回 `(音频文件, "true"/"false" 是否命中缓存)` |
| `/show_model` | 列出音色模型目录里的 `.pth`（返回 list） |
| `/model_info` | **0.9.0 新增**：音色模型目录自检，返回目录、数量、模型清单与告警（见下） |
| `/show_msst_models` / `/select_msst_model` | 分离模型列表与切换 |
| `/cache_info` / `/clear_cache` | 缓存统计与清理 |

### 音色模型目录（0.9.0）

默认目录是 RVC 整合包的 `assets\weights`。想换地方就设环境变量 `CHORUS_WEIGHTS_DIR`，
启动时会把该路径作为 `weight_root` 传给 RVC 子进程——RVC 的 `get_vc()` 拼的是
`f'{os.getenv("weight_root")}/{模型名}'`，不传这个变量换目录是不生效的。

`/model_info` 入参只有 `models_dir`（可选，用来核对另一个目录），返回：

```json
{
  "dir": "D:\\...\\assets\\weights",
  "backend_dir": "D:\\...\\assets\\weights",
  "source": "默认（RVC 整合包的 assets\\weights）",
  "exists": true,
  "count": 8,
  "models": ["ainuo.pth", "..."],
  "matches_backend": true,
  "rvc_port": 7897,
  "rvc_running": true,
  "index_dirs": ["D:\\...\\assets\\indices"],
  "warning": "（只在目录不存在 / 没有 .pth / 和 backend_dir 不一致时出现）"
}
```

插件 0.11.0 的 `/刷新rvc模型` 就是拿这个接口回显「模型文件夹 + 数量」的。

`/convert` 参数名和原中间层完全一致（`song_name_src, key_shift, vocal_vol, inst_vol,
model_dropdown, reverb_intensity, delay_intensity, f0_method, index_rate, filter_radius,
uvr5_*, msst_*, vocal_postprocess, shift_accompaniment`），末尾追加本机参数：
`chorus_seconds`（默认 45）、`dereverb_model`（默认「去混响（anvuew 22.505）」）、
`inst_volume_db`（默认 -6.0，伴奏压得比人声低一些）、
**`render_mode`（0.7.0 新增，`chorus` / `full` / `custom`，默认 `chorus`）**，
以及 0.8.0 新增的 **`rms_mix_rate`（默认 0.35）、`protect`（默认 0.33）、
`vocal_volume_db`（默认 0.0，人声电平）**。
**每个组件都写了 label**，Gradio 4 的 `/info` 靠它生成 `parameter_name`，插件那边才能用 kwargs。

## 移调怎么做的

`key_shift = +N` 时，人声和伴奏必须一起升 N 个半音，否则两条音轨调性打架：

- 人声：交给 RVC 的 `vc_transform0`
- 伴奏：`rubberband.exe -3 --pitch N inst.wav out.wav`（音高变、时长和采样率不变）

`shift_accompaniment = false` 时只动人声，伴奏保持原调——只适合清唱或你自己另有安排。

## 插件升级后怎么自查

本项目的代码全在插件目录以外（插件更新只覆盖 `data\plugins\astrbot_plugin_matsuko_cover\`），
所以升级不会冲掉这里。唯一会断的是**接口签名**：插件每次升级都可能给 `/convert` 加参数，
多传一个我们不认识的参数，客户端会直接报 `Parameter xxx is not a valid key-word argument`。

升级后跑一条命令：

```bat
python tools\check_plugin_compat.py
```

输出里出现 `✘` 就照着补：`app.py` 的 `convert()` 加形参，`_hidden_inputs()` 加一个同名
`label` 的隐藏组件，重启中间层即可。

另外插件配置里这几个键是它自己也要用的，更新后顺手确认还在（缺了会被 AstrBot 丢掉）：
`rvc_base_url`、`default_api`、`default_model_index`、`rvc_models_keywords`。

## 常见问题

- **启动就报 `When localhost is not accessible...`**：本机开着系统代理（fake-ip），Gradio 4 探测 localhost 时走了代理。`app.py` 里已经把 `gradio.networking.url_ok` 钉成 True，正常不会再犯；自己另起 Gradio 4 服务时要么设 `NO_PROXY=127.0.0.1,localhost`，要么照抄这个补丁。
- **`Unknown protocol: ws`**：拿新版 gradio_client 直接连 RVC WebUI（3.14）就会这样。跟 WebUI 说话必须走 `sep_worker.py` / `rvc_worker.py`。
- **`/info` 500 / `argument of type 'bool' is not iterable`**：Gradio 4.44 处理 `gr.JSON` 的 schema 会崩，`app.py` 里给 `json_schema_to_python_type` 加了兜底。
- **混音卡住不动**：老版本用 ffmpeg 的 `asetrate` 做移调，采样率乘到十几万，重采样直接卡死。现在走 rubberband，45 秒 4~8 秒完成；还卡就查 `bin\` 里 exe 和 dll 是否齐全。
- **插件传 `render_mode` 报 `Parameter render_mode is not a valid key-word argument`**：中间层还是 0.6.0 之前的版本，`/info` 里没有这个参数。升到 0.7.0 并重启 3333；旧插件不传这个参数也能用，按 `chorus` 处理。
- **第一步就慢/每次 3~5 秒白等**：看 `_work\mw.log` 里是不是 `worker=process`；如果常驻 worker 没起来（`_work\resident.log` 有 traceback），会用旧路兜底，功能不受影响，只是慢。检查 `config.RVC_PYTHON` 能否跑 `resident_worker.py`。
- **变声报 `NoneType has no attribute pipeline`**：模型没加载。`rvc_worker.py` 每次都会先调 `/infer_change_voice` 预热，正常碰不到。
- **同一首歌重复跑**：命中 `cache/` 直接返回；改 `key_shift`、模型、`render_mode`/时长、`f0_method`、`index_rate`、`rms_mix_rate`、`protect`、`inst_volume_db`、`vocal_volume_db` 任意一项都会换 key，重新跑。
- **报「分离没有产出人声：…\sep_vocal」**：这是 0.8.0 及以前的含糊说法，真正常见的原因是
  **显存不足**（RVC 的 `pymss_separate` 会把异常吞成文字，旧代码看不出来）。升 0.8.1 后会直接说
  「显存不足（CUDA out of memory）」并带原始 OOM 原文；按提示关掉 OBS/直播/Broadcast 或停 GPT-SoVITS 再重试。
- **WebUI 里有人在用**：不行。RVC 单实例、有全局状态，中间层在跑的时候别在 WebUI 上手动操作。
- **同时开了两个中间层 / 两个客户端抢 7897**：RVC WebUI 用一份全局模型状态，两边交替调 `/infer_change_voice`
  会互相把 pipeline 换掉，可能收到一个空错误（`ValueError:` 后面什么都没有）。同一时间只跑一个中间层；
  本服务自己的请求已经由常驻 worker 串行化，但管不住别的客户端。

## 分离模型（0.6.0 起）

| 环节 | 模型 | 说明 |
| --- | --- | --- |
| 整首分离 | becruily_deux.ckpt | 输出 Vocals / Instrumental（键名大写） |
| 去混响 | dereverb_bs_roformer_anvuew_sdr_22.5050.ckpt | RVC 里按 bs_roformer 加载 |
| 变声 | assets/weights 里的 .pth | 插件配置 rvc_default_model 可指定 |

两个模型文件放在 RVC 的 assets\\pymss_weights 里，预设清单在 tools\\pymss_webui.py。
