# chorus-mw

RVC 翻唱中间层，给 AstrBot 的翻唱插件当后端用：
丢一首歌进去，出来一段翻唱成品（默认 45 秒副歌，也可以自定长度、指定起点或整首翻唱）。

当前版本：`0.9.14`。配套插件 `astrbot_plugin_chorus_cover` ≥ `0.19.13`（清唱端点）／`0.19.15`（串行化说明）。

| 版本 | 这一版加了什么 |
| --- | --- |
| `0.9.14` | 翻唱任务全局串行化：`/convert` 和 `/convert_vocal_only` 共用一把锁，后到的排队；可选「优先标记」插队 |
| `0.9.13` | 清唱端点 `/convert_vocal_only`；每次请求打参数与开始/完成日志；守护启动器把中间层日志转印到自己的窗口 |
| `0.9.12` | 取歌改走本地网易云 API（3003），搜索结果按原唱优先打分 |
| `0.9.11` | 修整机内存耗尽导致的崩溃：常驻 worker 每 4 首换进程、分离前查可用内存、加守护启动器 |
| `0.9.9` | 混音默认加人声链（高通 + 轻压缩 + 伴奏侧链让位） |
| `0.9.7` | 混音改成只做峰值归一，不再用 loudnorm 动态压缩 |
| `0.9.5` | 默认「提主旋律」分离（karaoke 模型先剥主唱，再去混响） |
| `0.9.0` | 音色模型目录 `/model_info` 与 `CHORUS_WEIGHTS_DIR` |
| `0.8.1` | 显存保护：分离前预检、OOM 换新进程重试、失败原因透传 |
| `0.8.0` | 推理与混音参数真正生效（不再写死默认值） |
| `0.7.0` | 先切段再分离（默认只分离 51 秒），`render_mode` 三种时长 |

默认后端插件是自维护分支 `astrbot_plugin_chorus_cover`（也可接上游 `astrbot_plugin_matsuko_cover`，接口签名一致，仅作兼容参考）。

整条链路：**下歌 → 整首混音上挑候选窗口 → 只切这一段 → 只分离这一段 → 人声里精挑 → 去混响 ∥ 伴奏切段移调 → RVC 变声 → 混音（或清唱跳过）→ 出 wav**。

0.7.0 之前是「整首分离 → 再切 45 秒」，三四分钟的歌光分离就要二三十秒；
现在默认只分离 51 秒左右，同一首 4:39 的歌实测 **72.2 秒 → 32.6 秒**。

当前默认模型：分离用「提主旋律」，去混响用「去混响（anvuew 22.505）」；换模型见文末「分离模型」。

## 目录

```
chorus-mw/
├─ app.py             接口层（Gradio 4.44，监听 127.0.0.1:3333）
├─ pipeline.py        主流程：下载、挑窗口、切割、并行调度、混音、缓存
├─ chorus.py          副歌挑选（能量包络滑窗 + 换气点吸附 + 重复段落加权）
├─ pitch.py           按音域自动移调（0.9.3，默认关）
├─ rvc.py             分离/去混响/变声的调度（常驻 worker 优先，失败退回子进程）
├─ resident_worker.py 常驻工作进程（分离/去混响/变声，一行一个 JSON）
├─ sep_worker.py      旧的分离/去混响子进程（兜底，每次起一个）
├─ rvc_worker.py      旧的变声子进程（兜底，每次起一个）
├─ sep_guard.py       分离子进程守卫（sys.path、显存/内存环境变量）
├─ config.py          路径与默认参数（RVC 路径**不写死**，只认 CHORUS_RVC_ROOT）
├─ scripts/
│  ├─ start.ps1            启停 + 状态（读环境变量，会自动设 NO_PROXY、等端口就绪）
│  └─ fetch_rubberband.ps1 下载 rubberband.exe + sndfile.dll 到 bin/
├─ start_supervisor.ps1 / .bat   崩溃自动重启的守护启动器（0.9.11）
├─ tools/
│  └─ check_plugin_compat.py  插件升级后比对参数表
├─ bin/            rubberband.exe + sndfile.dll（音高变换）
├─ libs/           gradio_client 0.2.9（只给 worker 用，对 Gradio 3.14 的 ws 协议）
├─ libs_web/       Gradio 4.44 + 依赖（app.py 用，sse 协议）
├─ _work/          临时产物、mw.log、resident.log、supervisor.log、priority.flag
├─ output/         成品（wav + 同名 .lyric.json）
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

## 三种翻唱时长（`render_mode`）

| 模式 | 说明 | 分离多长 | 实测（4:39 的《失眠飞行》） |
| --- | --- | --- | --- |
| `chorus`（默认） | 只翻唱副歌片段，默认 45 秒 | 目标 + 6 秒余量 = 51 秒 | **32.6 秒**（改前整首分离 72.2 秒） |
| `custom` | 自定义 20~180 秒，用 `chorus_seconds` | 时长 + 6 秒 | `chorus_seconds=30` → **27.1 秒** |
| `full` | 整首翻唱：分离整首、变声整首 | 整首 | **明显慢得多，可能是副歌模式的 5~10 倍，显存占用也更高** |

> 余量（`CHORUS_MARGIN_SECONDS`）0.9.8 起默认是 **6 秒**：原来 15 秒是为了在更宽的人声里挑换气点，
> 但 8G 卡上 60 秒跨度会被 OOM，缩到 51 秒后峰值跟着降，稳定跑完。

`full` 不是「把 45 秒换成 279 秒」这么简单：RVC 变声和 BS-Roformer 分离的耗时随音频长度增长，长音频还会把
显存吃到接近上限后降速。4:39 的歌整首跑通常要几分钟到十几分钟，插件默认 `inference_timeout=300` 秒
很可能不够，要整首翻唱就把它和任务超时一起调大。

`chorus` / `custom` 的省时原理：先在**整首混音**上用能量包络挑一个「目标时长 + 6 秒」的候选窗口
（最长不超过整首），只切这一段去做分离，再在分离出的人声里精挑最终那段、把边界吸附到换气点。
新顺序任何一步报错都会自动退回「整首分离再切」，日志里记 `fallback=full_separation`。

**指定起点（0.9.4）**：调用方传 `chorus_start`（秒）时跳过自动挑段，直接从这一秒往后截；
`/convert` 的 24 号参数就是它，插件设置 `chorus_start_seconds` 对应。0 或空 = 自动。

## 音色与音量参数（0.8.0 起真的生效）

0.7.0 及以前 `/convert` 虽然收了这些参数，但 `pipeline` 里写死用 `config.py` 的默认值，
插件里改 `index_rate` 毫无效果。0.8.0 起全部按请求解析、一路传到 RVC 推理和混音：

| 参数 | 默认 | 范围 | 作用 | 调大 / 调小 |
| --- | --- | --- | --- | --- |
| `f0_method` | `rmvpe` | `rmvpe` / `pm` / `crepe` / `harvest` | 音高提取算法 | `rmvpe` 最稳；`pm` 最快、质量一般；`crepe` 更吃显卡 |
| `index_rate` | `0.75` | 0~1 | 特征检索比率，越高越贴模型音色 | 调大更贴音色但易生硬；调小更自由、可能走音 |
| `rms_mix_rate` | `0.35` | 0~1 | 人声音量包络贴合原唱的程度 | 调大更自然、人声可能被压小；调小更平更冲、易发闷 |
| `protect` | `0.33` | 0~0.5 | 保护清音/气声，减少撕裂 | 调大更安全、更少电流味；调小更贴音色、易沙 |
| `inst_volume_db` | `-6.0` | -20~0 | 伴奏音量（dB） | 调大伴奏更响；调小几乎只剩人声 |
| `vocal_volume_db` | `0.0` | -12~+12 | 人声音量（dB，0 为原样） | 调大人声更靠前；调小人声往后退 |

- 每次请求都会打一行 `[chorus-mw] params f0=... index_rate=... ...`，`done` 那行也会重复最终值，对着日志就能核对。
- 启动时另打一行「生效参数默认值」，那是没被请求覆盖时用的值（可用环境变量改，见下表）。
- **`filter_radius` 只收下并记日志（标注「暂未使用」）**：RVC WebUI 这一版的 `/infer_convert` 只有 9 个入参，
  没有 filter_radius，传不下去，所以它不会改变成品——别把它当成能调的东西。
- 这些参数、两条轨道的音量、`vocal_only`、`chorus_start`、混音链开关都进了缓存 key：改了就会重新跑，不会拿上次的成品糊弄你。

## 清唱端点（0.9.13）

`/convert_vocal_only` 和 `/convert` 参数表一致，内部强制 `vocal_only=True`：照常分离、去混响、变声，
**最后跳过混音**，直接把变声后的人声复制成成品。

- 单独开一个 api，是因为 Gradio 的 `/info` 参数名来自函数签名、按组件数截断，末尾新加的布尔位会被吞掉——
  单开端点，参数表才是干净的。插件探测到 `vocal_only` 不可见，打开开关时直接改调这个端点。
- 不混伴奏 → 伴奏既不做变速移调也不参与混音，少一段 CPU 和内存。
- 缓存键里 `vocal_only` 是 true，和普通翻唱互不命中。
- 日志里能看到 `vocal_only=True` 和 `只留人声（不混伴奏）`。

## 串行化与优先标记（0.9.14）

`/convert` 和 `/convert_vocal_only` 是同一个后端，以前两个端点可以并发进入 RVC——两个任务同时跑会抢显卡：
变慢、互相拖，撞到显存还会整条失败。0.9.14 起：

- `pipeline.run()` 外面包了一把全局优先级锁 `PriorityLock`，同一时刻只允许一个翻唱任务跑，两个端点共用；
- **优先标记**：调用方在提交前写一个标记文件（默认 `_work/priority.flag`，可用 `CHORUS_PRIORITY_FLAG` 换路径），
  该请求排队时插到普通请求前面；不写就按先来后到，行为和以前一致；
- 命中标记时日志写「优先请求，插队执行」；
- 实测：同时发两个任务（一个带优先标记、一个不带），带标记的 49.6 秒完成，另一个 81.0 秒完成（含排队等待），
  两条都成功，没有并发跑。

翻唱插件默认不写这个标记；需要实时出效果的交互式应用（例如直播中台）可以在提交前写一次。

## 取歌：本地网易云 API + 原唱优先打分（0.9.12）

调用方传歌名（而不是文件路径）时，中间层自己去下歌。

- 先走本地网易云 API：`CHORUS_NETEASE_API`（默认 `http://127.0.0.1:3003`）的 `/search` 与 `/song/url/v1`，
  能拿会员歌直链；cookie 默认从翻唱插件的配置里读 `netease_cookie`，也可以用 `CHORUS_NETEASE_COOKIE` 覆盖。
- 本地 API 连不上就回退旧路子（公共搜歌 + 解析站）；旧解析站证书过期时会先降级、再报人话，不再抛 SSL 堆栈。
- 搜索结果按原唱优先打分：同名 +100、包含 +70、一字之差 +55、歌手命中 +45；
  伴奏/DJ/Remix/现场/翻唱/童声/片段 −45，不足 60 秒 −40。
- 实测：本地解析《稻香》→ 45 秒副歌 → 35 秒出成品。

## 分离与去混响

默认走「提主旋律」（0.9.5）：karaoke 模型先把主唱从和声里剥出来，再用 anvuew 去混响，最后才进 RVC。
副歌叠双人声的歌，这一步能明显减少发糊和电音。想换回普通分离：

```powershell
$env:CHORUS_SEP_MODEL = '去伴奏'                    # BS-Roformer，通用分离
$env:CHORUS_SEP_MODEL = '去伴奏（becruily deux）'   # 更快，质量差一些
```

「提主旋律」失败时会自动退回「去伴奏」再试一次（0.9.8），日志记 `fallback`。

**两段式分离（实验，默认关）**：先通用分离再从人声里拆主唱，代码保留为 `CHORUS_TWO_STAGE=1`；
实测与单段无可闻差别，只是更慢（29 秒 → 43 秒），所以默认单段。

## 混音链（0.9.9 起默认开启）

成品最后一步不再是「人声 + 伴奏直接相加」，而是：

| 步骤 | 处理 | 参数 |
| --- | --- | --- |
| 1 | 人声高通 | 85 Hz，去掉低频闷声和隆隆声 |
| 2 | 人声轻压缩 | 2.5:1，阈值 -18 dB，attack 8ms / release 180ms |
| 3 | 伴奏侧链让位 | 1.8:1，人声出现时伴奏自动退一点 |
| 4 | 峰值归一 | 整体增益到 -1.5 dBFS，不做动态压缩 |

全在同一次 ffmpeg 渲染里完成，四十五秒片段总耗时基本不变（33 秒上下）。

开关：

- `CHORUS_VOCAL_CHAIN=0`：关掉整条人声链，回到纯相加 + 峰值归一；
- `CHORUS_VOCAL_DUCK=0`：只关伴奏让位，保留高通和压缩；
- `CHORUS_NORMALIZE=loudnorm`：回到旧的 loudnorm 响度归一（会压动态，仅用于对比）。

## 常驻 worker 与并行

- `app.py` 启动时在后台线程里拉起一个常驻进程 `resident_worker.py`（`_work/resident.log` 是它的输出），
  分离 / 去混响 / 变声都发到这个进程里串行做。以前每一步都起新进程、每次重新 import torch 和
  `tools.pymss_webui`（实测每次 3~5 秒，三步就是 10~15 秒），现在只付一次。
- 常驻进程没起来、中途崩了或超时（`_work\resident.log` 里有 traceback），自动退回旧的
  「每次起一个进程」，日志记 `worker=fallback op=...`；连续两次失败会停用 120 秒再试。
- 每跑 4 首（`CHORUS_RESIDENT_RECYCLE`）换一个新进程：torch/LLVM 的碎片内存不会自己还回去，攒多了就会
  `LLVM ERROR: out of memory` 然后 c10.dll 崩溃。
- 分离完成后，「去混响（只需人声片段）」和「伴奏切段 + 移调」用线程池并行跑，不再串行等待。
- 代价：常驻进程会一直占着一份分离/去混响模型的显存（实测常驻后 GPU 占用 3.8 GB 左右）。
  显存紧就设 `CHORUS_RESIDENT=0` 退回每次起进程。

## 显存与内存保护

8 GB 卡上这套东西很容易把显存顶满：常驻程序（GPT-SoVITS 2.0 GB、RVC WebUI 0.8 GB、
NVIDIA Broadcast + MediaSDK 0.6 GB、QQ/浏览器/ToDesk 等）平时就占着 5.8 GB，
而 PyMSS 分离峰值还要再吃 1.6 GB 左右，实测峰值 **7.39 / 8.15 GB**。差一点就会 OOM。

| 机制 | 行为 | 相关配置 |
| --- | --- | --- |
| 显存预检 | 查 `nvidia-smi` 的空闲显存，低于阈值就先等，并把「显存只剩 XXXMB」通过进度报给插件 | `CHORUS_VRAM_MIN_FREE_MB`（默认 1800）、`CHORUS_VRAM_WAIT_SECONDS`（默认 10） |
| 内存预检 | 分离/去混响前看可用物理内存，低于阈值先等最多 20 秒，等不到就报「内存不足（可用 xxxMB）」，不硬跑崩进程 | `CHORUS_MIN_FREE_RAM_MB`（默认 500） |
| OOM 自动重试 | 失败信息里认出 OOM 后，等几秒换**全新进程**重跑一次（常驻进程 OOM 后 CUDA 上下文可能已坏） | `CHORUS_OOM_RETRY_WAIT_SECONDS`（默认 3） |
| 常驻进程回收 | 每跑 4 首换新进程，释放碎片内存 | `CHORUS_RESIDENT_RECYCLE`（默认 4） |
| 失败原因透传 | 报「显存不足（…）现在空闲 XXXMB + 原始 OOM 原文」，而不是「分离没有产出人声」 | — |

日志里能对上几行：`vram free=...`、`worker=<resident|process> failed op=... reason=...`、
`oom op=... free=...MB，Ns 后换新进程重试一次`、`可用内存不足...`。

查不到显存（没独显、驱动异常、`nvidia-smi` 不在 PATH）时预检直接放行，不会挡住正常唱歌；
等 10 秒还是不够也照样往下跑——真正缺显存时由重试 + 明确报错兜底，而不是无限等。

**给用户的话**：OOM 时先关 OBS / 直播伴侣 / NVIDIA Broadcast，或者临时停掉 GPT-SoVITS
（它一个人占 2 GB），再重试；不用重启 AstrBot。

## 按音域自动移调（0.9.3，默认关闭）

调用方没传升降调（`key_shift=0`）时，先测源人声的中位基频，再挪到 `CHORUS_TARGET_F0`（默认 240Hz）
附近，人声和伴奏一起挪。实测同一首男声歌「不移调」听感更贴模型，所以默认关着：

```powershell
$env:CHORUS_AUTO_PITCH = '1'    # 想试再打开
$env:CHORUS_TARGET_F0  = '240'  # 目标音域
$env:CHORUS_MAX_AUTO_SHIFT = '12'  # 最多挪几个半音
```

`harvest` 这个整合包不支持（`infer/vc/pipeline.py` 只认 pm/rmvpe/fcpe），补装 pyworld 也没用，别往那条路折腾。

## 接口

| api_name | 说明 |
| --- | --- |
| `/convert` | 主入口，31 个业务参数（另有 `progress` 回调），按名字传 kwargs，返回 `(音频文件, "true"/"false" 是否命中缓存)` |
| `/convert_vocal_only` | **0.9.13 新增**：清唱，参数表与 `/convert` 一致，内部强制 `vocal_only=True`，只出人声 |
| `/show_model` | 列出音色模型目录里的 `.pth`（返回 list） |
| `/model_info` | 音色模型目录自检，返回目录、数量、模型清单与告警（见下） |
| `/show_msst_models` / `/select_msst_model` | 分离模型列表与切换（分离模型实际由 `CHORUS_SEP_MODEL` 启动配置决定） |
| `/cache_info` / `/clear_cache` | 缓存统计与清理 |

`/convert` 参数名和原中间层完全一致（`song_name_src, key_shift, vocal_vol, inst_vol,
model_dropdown, reverb_intensity, delay_intensity, f0_method, index_rate, filter_radius,
uvr5_*, msst_*, vocal_postprocess, shift_accompaniment`），末尾追加本机参数：
`chorus_seconds`（默认 45）、`chorus_start`（0.9.4，指定副歌起点）、`dereverb_model`、
`inst_volume_db`（默认 -6.0）、`render_mode`（0.7.0，`chorus` / `full` / `custom`）、
`rms_mix_rate`（0.8.0，默认 0.35）、`protect`（0.8.0，默认 0.33）、`vocal_volume_db`（0.8.0，默认 0.0）、
`vocal_only`（0.9.13）。

参数名以**函数签名**为准（Gradio 4 的 `/info` 从签名生成 `parameter_name`），所以按名字传 kwargs 就够；
`vocal_only` 排在最后、组件数不够，`/info` 里看不到它——要清唱请直接调 `/convert_vocal_only`。

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

插件的 `/刷新rvc模型` 就是拿这个接口回显「模型文件夹 + 数量」的。

## 移调怎么做的

`key_shift = +N` 时，人声和伴奏必须一起升 N 个半音，否则两条音轨调性打架：

- 人声：交给 RVC 的 `vc_transform0`
- 伴奏：`rubberband.exe -3 --pitch N inst.wav out.wav`（音高变、时长和采样率不变）

`shift_accompaniment = false` 时只动人声，伴奏保持原调——只适合清唱或你自己另有安排。

## 启动

先设置 RVC 整合包路径（代码里不写死任何本机路径）：

```bat
set CHORUS_RVC_ROOT=D:\你的路径\RVC20260718Nvidia50x0
python -X utf8 app.py
```

或者直接用脚本，它会做体检、拉起服务、等端口就绪：

```bat
powershell -ExecutionPolicy Bypass -File scripts\start.ps1           :: 启动
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Status   :: 看状态
powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop     :: 停掉
```

跑 `app.py` 的 Python 只要 3.12 + 能 import `libs_web` 里的东西即可；RVC 相关的重活都在
`config.RVC_PYTHON`（整合包自带运行时）里跑。

**守护启动（0.9.11）**：中间层可能因为整机内存耗尽被系统打崩（日志表现为 `LLVM ERROR: out of memory`
后接 `CUDA error: unknown error`，进程以 `0xC0000005` 退出）。要它崩了自己起来就用守护启动器：

- 双击 `start_supervisor.bat`，或 `powershell -File start_supervisor.ps1`；
- 进程退出自动重启，重启记录在 `_work/supervisor.log`；崩溃间隔太短会退避（5 秒起，最多 60 秒）；
- 同时只允许一个守护循环（命名互斥量），第二个窗口会提示后退出，不会两个循环各起一个 app 抢 3333；
- 窗口里实时显示中间层日志（把 `_work/mw.log` 转印过来，UTF-8 读取，标题「翻唱中间层3333」）。

> 注意：`start_supervisor.ps1` 顶部的 `$Python` 和 `$RvcRoot` 是本机路径，换机器要改这两行；
> `scripts\start.ps1` 则直接读 `CHORUS_PYTHON` / `CHORUS_RVC_ROOT`，不需要改文件。

启动前会跑 `config.check_paths()`，缺 runtime / ffmpeg / 模型目录 / rubberband+dl / libs_web 会一次列全并退出，
不会带着半个环境跑到一半再炸。

## 环境变量一览

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `CHORUS_RVC_ROOT` | 无，**必填** | RVC 整合包根目录 |
| `CHORUS_PYTHON` | PATH 里的 python | 跑服务端的解释器（`scripts\start.ps1` 用） |
| `CHORUS_WEIGHTS_DIR` | `<RVC_RVC_ROOT>\assets\weights` | 音色模型目录（0.9.0）。改这里要重启中间层；启动时会把它作为 `weight_root` 传给 RVC 子进程 |
| `CHORUS_PORT` | 3333 | 监听端口 |
| `CHORUS_HOST` | 127.0.0.1 | 监听地址 |
| `CHORUS_RVC_PORT` | 7897 | RVC WebUI 端口 |
| `CHORUS_RVC_BOOT_TIMEOUT` | 180 | 等 RVC WebUI 起来的超时（秒） |
| `CHORUS_RUBBERBAND` | `bin\rubberband.exe` | 想用系统装的 rubberband 就指过去 |
| `CHORUS_SEP_MODEL` | 提主旋律 | 分离模型标签，要和 RVC 预设清单里的 label 完全一致 |
| `CHORUS_SEP_FIRST_MODEL` | 去伴奏 | 两段式的第一段模型 |
| `CHORUS_SEP_FALLBACK` | 去伴奏 | 提主旋律失败时退回的模型 |
| `CHORUS_TWO_STAGE` | 0 | 设 `1` 打开两段式分离（实验，更慢） |
| `CHORUS_DEREVERB_MODEL` | 去混响（anvuew 22.505） | 去混响模型标签 |
| `CHORUS_SECONDS` | 45 | 默认翻唱秒数（调用方不传 `chorus_seconds` 时用） |
| `CHORUS_MARGIN_SECONDS` | 6 | 先切后分离的余量：候选窗口 = 目标时长 + 这么多秒 |
| `CHORUS_F0` | rmvpe | 推理参数默认值（请求没传或传了非法值时用） |
| `CHORUS_INDEX_RATE` | 0.75 | 同上 |
| `CHORUS_RMS_MIX` | 0.35 | 同上 |
| `CHORUS_PROTECT` | 0.33 | 同上 |
| `CHORUS_FILTER_RADIUS` | 3 | 只用于日志/缺省解析；RVC WebUI 本版没有这个入参，暂不生效 |
| `CHORUS_INST_VOLUME_DB` | -6.0 | 伴奏默认电平（dB） |
| `CHORUS_VOCAL_VOLUME_DB` | 0.0 | 人声默认电平（dB） |
| `CHORUS_RESIDENT` | 1 | 设为 `0` 关掉常驻 worker，退回每次起进程（省显存、但慢 10~15 秒） |
| `CHORUS_RESIDENT_RECYCLE` | 4 | 常驻进程跑几首换一个新进程（0.9.11） |
| `CHORUS_MIN_FREE_RAM_MB` | 500 | 分离前要求的最低可用物理内存（MB）（0.9.11；0.9.14 起统一为 500） |
| `CHORUS_VRAM_MIN_FREE_MB` | 1800 | 分离前预检的空闲显存下限（MB）；本机常驻程序占得多，定太高会每首歌白等 |
| `CHORUS_VRAM_WAIT_SECONDS` | 10 | 显存不够时最多等多少秒；等不到也继续跑 |
| `CHORUS_OOM_RETRY_WAIT_SECONDS` | 3 | 真 OOM 后等几秒再换新进程重试一次 |
| `CHORUS_AUTO_PITCH` | 0 | 设 `1` 打开按音域自动移调（0.9.3） |
| `CHORUS_TARGET_F0` | 240 | 自动移调的目标音域（Hz） |
| `CHORUS_MAX_AUTO_SHIFT` | 12 | 自动移调最多挪几个半音 |
| `CHORUS_VOCAL_CHAIN` | 1 | 人声链（高通 + 压缩）开关，`0` 关 |
| `CHORUS_VOCAL_DUCK` | 1 | 伴奏侧链让位开关，`0` 只关让位 |
| `CHORUS_NORMALIZE` | peak | `peak` 只做峰值归一；`loudnorm` 回旧行为（会压动态，仅用于对比） |
| `CHORUS_PRIORITY_FLAG` | `_work\priority.flag` | 优先标记文件路径（0.9.14），存在即视为优先请求 |
| `CHORUS_NETEASE_API` | `http://127.0.0.1:3003` | 本地网易云 API（0.9.12） |
| `CHORUS_NETEASE_COOKIE` | 从插件配置读 | 本地网易云 API 的 cookie 覆盖（0.9.12） |

## 和 AstrBot 一起开机

### 推荐的启动顺序（2026-10-28 起）

一条前端两个后端加一个取歌服务，**顺序不要随意换**，尤其是 AstrBot 要放最后：

```
1) QQ             —— 先登录，后面 SnowLuma 要挂它
2) SnowLuma       —— 挂 QQ、开 5099，把 OneBot 接到 AstrBot（可以晚连，会自动重试）
3) GPT-SoVITS API —— 9880，模型加载最慢（实测 30~120 秒），早起点
4) RVC 中间层      —— 本服务，3333；它会自己拉 RVC WebUI（7897）
5) 网易云取歌      —— NeteaseCloudMusicApi，3003
6) AstrBot        —— 最后起，等前面都就绪，机器人一上线就能唱歌/点歌
```

为什么这么排：

- **QQ 必须最先**：SnowLuma 是挂到已登录的 QQ 进程上的，QQ 没起来它没东西可挂。
- **AstrBot 必须最后**：它一起来就会处理消息；如果 3333 / 3003 还没就绪，用户第一句话
  就可能碰到「翻唱失败 / 取歌失败」。放最后能保证它上线即能用。
- **SnowLuma 在 AstrBot 之前没关系**：它是 ws 客户端，连不上 6199 会每 5 秒重试，
  AstrBot 起来后自动接上。
- **本中间层在 7897 之前没关系**：`ensure_rvc()` 会自己拉起 RVC WebUI 并等端口。

Windows 下的现成实现是用户侧的一键启动脚本（例如 `D:\QQBOT\启动\start.ps1`，`-Status` / `-Stop` 也在里面），
它按上面顺序逐项检查、没起就拉起来、起完才往下走。**建议就用它启动**，
不要手动一个个开：手动开最容易漏 3003 或 3333，表现就是「机器人能聊天但点歌/唱歌失败」。

> 踩过的坑：启动器曾经用管道接网易云那一步的输出（`& powershell -File xxx.ps1 | ...`），
> 被拉起的 node 继承了管道句柄，脚本卡在 `[4/6]` 不动、后面的 GPT-SoVITS 没机会启动
> （服务其实已经起来了）。现在改成完全分离 + 只看端口，见 CHANGELOG 0.9.1。

本仓库只负责中间层自己（3333）；AstrBot、SnowLuma、GPT-SoVITS、网易云取歌都是用户侧组件，
启动器里怎么编排属于部署配置，不在本仓库内。

## 插件升级后怎么自查

本项目的代码全在插件目录以外（插件更新只覆盖 `data\plugins\astrbot_plugin_chorus_cover\`），
所以升级不会冲掉这里。唯一会断的是**接口签名**：插件每次升级都可能给 `/convert` 加参数，
多传一个我们不认识的参数，客户端会直接报 `Parameter xxx is not a valid key-word argument`。

升级后跑一条命令：

```bat
python tools\check_plugin_compat.py --plugin-main "C:\你的\data\plugins\astrbot_plugin_chorus_cover\main.py"
```

（不带 `--plugin-main` 时它会读脚本里写死的旧路径，基本一定找不到，建议每次传。）

输出里出现 `✘` 就照着补：`app.py` 的 `convert()` 加形参，`_hidden_inputs()` 加一个同名
`label` 的隐藏组件，重启中间层即可。

另外插件配置里这几个键是它自己也要用的，更新后顺手确认还在（缺了会被 AstrBot 丢掉）：
`rvc_base_url`、`rvc_models_keywords`、`rvc_models_dir`、`netease_cookie`。

## 常见问题

- **启动就报 `When localhost is not accessible...`**：本机开着系统代理（fake-ip），Gradio 4 探测 localhost 时走了代理。`app.py` 里已经把 `gradio.networking.url_ok` 钉成 True，正常不会再犯；自己另起 Gradio 4 服务时要么设 `NO_PROXY=127.0.0.1,localhost`，要么照抄这个补丁。
- **`Unknown protocol: ws`**：拿新版 gradio_client 直接连 RVC WebUI（3.14）就会这样。跟 WebUI 说话必须走 `sep_worker.py` / `rvc_worker.py`。
- **`/info` 500 / `argument of type 'bool' is not iterable`**：Gradio 4.44 处理 `gr.JSON` 的 schema 会崩，`app.py` 里给 `json_schema_to_python_type` 加了兜底。
- **混音卡住不动**：老版本用 ffmpeg 的 `asetrate` 做移调，采样率乘到十几万，重采样直接卡死。现在走 rubberband，45 秒 4~8 秒完成；还卡就查 `bin\` 里 exe 和 dll 是否齐全。
- **插件传 `render_mode` 报 `Parameter render_mode is not a valid key-word argument`**：中间层还是 0.6.0 之前的版本，`/info` 里没有这个参数。升到 0.7.0 并重启 3333；旧插件不传这个参数也能用，按 `chorus` 处理。
- **开了清唱却没生效**：清唱要插件打开 `vocal_only`，并且中间层 ≥ 0.9.13。插件会改调 `/convert_vocal_only`；如果用别的客户端，直接调这个端点，别指望在 `/convert` 里传 `vocal_only`（参数表里看不到它）。
- **两首同时提交，第二首卡着不动**：0.9.14 起是设计如此，全局锁同一时刻只跑一个任务；日志里能看到排队。要插队就写 `CHORUS_PRIORITY_FLAG` 指向的那个标记文件。
- **第一步就慢/每次 3~5 秒白等**：看 `_work\mw.log` 里是不是 `worker=process`；如果常驻 worker 没起来（`_work\resident.log` 有 traceback），会用旧路兜底，功能不受影响，只是慢。检查 `config.RVC_PYTHON` 能否跑 `resident_worker.py`。
- **变声报 `NoneType has no attribute pipeline`**：模型没加载。`rvc_worker.py` 每次都会先调 `/infer_change_voice` 预热，正常碰不到。
- **同一首歌重复跑**：命中 `cache/` 直接返回；改 `key_shift`、模型、`render_mode` / 时长 / 起点、`f0_method`、`index_rate`、`rms_mix_rate`、`protect`、`inst_volume_db`、`vocal_volume_db`、`vocal_only` 任意一项都会换 key，重新跑。
- **报「分离没有产出人声：…\sep_vocal」**：这是 0.8.0 及以前的含糊说法，真正常见的原因是
  **显存不足**（RVC 的 `pymss_separate` 会把异常吞成文字，旧代码看不出来）。升 0.8.1 后会直接说
  「显存不足（CUDA out of memory）」并带原始 OOM 原文；按提示关掉 OBS/直播/Broadcast 或停 GPT-SoVITS 再重试。
- **WebUI 里有人在用**：不行。RVC 单实例、有全局状态，中间层在跑的时候别在 WebUI 上手动操作。
- **同时开了两个中间层 / 两个客户端抢 7897**：RVC WebUI 用一份全局模型状态，两边交替调 `/infer_change_voice`
  会互相把 pipeline 换掉，可能收到一个空错误（`ValueError:` 后面什么都没有）。同一时间只跑一个中间层；
  本服务自己的请求已经由常驻 worker 和 0.9.14 的全局锁串行化，但管不住别的客户端。

## 分离模型

| 环节 | 模型（默认） | 说明 |
| --- | --- | --- |
| 分离 | 提主旋律 | karaoke，先把主唱从和声里剥出来；失败退回「去伴奏」 |
| 去混响 | 去混响（anvuew 22.505） | RVC 里按 bs_roformer 加载 |
| 变声 | assets/weights 里的 .pth | 插件配置 `rvc_default_model` 可指定 |

模型文件放在 RVC 的 `assets\pymss_weights` 里，预设清单在 `tools\pymss_webui.py`；
中文标签必须和清单里完全一致，写错会被当成「不认识的分离模型」。

## 产物与缓存

- 成品：`output\<歌曲名>_<模型名>_<hash6>.wav`。
- 每个成品旁边有一个同名 `.lyric.json`，写着这次实际唱的起止秒（`{"start": …, "end": …}`），
  下游按这个起点对齐歌词，不用猜。
- 缓存：`cache/` 按内容哈希命中；`/cache_info` 看占用，`/clear_cache` 清理。
