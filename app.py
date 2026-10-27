"""AstrBot 翻唱插件（matsuko_cover）的 RVC 中间层，N 卡版。

对外接口和 RVCSVC-API-MSST 保持一致（/convert、/show_model、/cache_info 等），
插件那边只要把 rvc_base_url 指向本服务，一行代码都不用改。

和原版中间层的唯一区别：拿到音频后不是整首直接唱，而是
「整首分离 → 按人声能量挑 45 秒副歌 → 软切 → 去混响 → RVC → 混音」。
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
# libs_web 里是 Gradio 4（sse 协议）。插件侧用 gradio_client 2.x，只认 sse；
# RVC 运行时自带的 Gradio 3.14 是 ws 协议，会被直接拒绝（Unknown protocol: ws）。
sys.path.insert(0, os.path.join(HERE, "libs_web"))
sys.path.insert(0, HERE)

import gradio as gr  # noqa: E402

import config  # noqa: E402
import pipeline  # noqa: E402
import rvc  # noqa: E402


def _patch_gradio_json_schema():
    """Gradio 4.44 在生成 /info 时会因为 gr.JSON 的 schema 里出现布尔值而崩：
    `TypeError: argument of type 'bool' is not iterable`。整条 /info 挂掉以后，
    新版 gradio_client 拿不到参数表，插件那边就没法用 kwargs 传参。
    这里给类型推断加个兜底：推不出来就报 Any，不影响真实调用。
    """
    try:
        import gradio_client.utils as client_utils
    except Exception:
        return
    original = getattr(client_utils, "json_schema_to_python_type", None)
    if original is None or getattr(original, "_chorus_safe", False):
        return

    def safe(schema):
        try:
            return original(schema)
        except Exception:
            return "Any"

    safe._chorus_safe = True
    client_utils.json_schema_to_python_type = safe


_patch_gradio_json_schema()


def _patch_localhost_probe():
    """本机开着系统代理（fake-ip），Gradio 4 启动时探测 localhost 会走代理然后认定
    「localhost 不可访问」，直接抛 ValueError 拒绝启动。服务明明就在本地，把探测
    结果钉成 True 即可。
    """
    try:
        import gradio.networking as networking
    except Exception:
        return
    networking.url_ok = lambda *args, **kwargs: True


_patch_localhost_probe()


def _patch_starlette_template_signature():
    """Starlette 1.x 把 Jinja2Templates.TemplateResponse 改成
    ``(request, name, context)`` 的新签名，并且不再兼容旧的 ``(name, context)``
    写法；Gradio 4.44 里仍然是老写法，于是 ``name`` 收到一个 dict，jinja2 的
    模板缓存键不可哈希，直接打开首页（GET /）就会 500：

        TypeError: unhashable type: 'dict'

    ``/info``、``/convert`` 不走模板，所以只有浏览器打开根路径会崩。
    这里按参数形状做一次归一化：第一个参数不是 Request 就当成模板名，request
    从 context 里取（Gradio 一定会在 context 里放 request）。
    """
    try:
        from starlette.requests import Request
        from starlette.templating import Jinja2Templates
    except Exception:
        return
    original = Jinja2Templates.TemplateResponse
    if getattr(original, "_chorus_normalized", False):
        return

    def normalized(self, *args, **kwargs):
        if args and not isinstance(args[0], Request):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.pop("context", None)
            request = (context or {}).get("request")
            if request is not None:
                return original(self, request, name, context, *args[2:], **kwargs)
        return original(self, *args, **kwargs)

    normalized._chorus_normalized = True
    Jinja2Templates.TemplateResponse = normalized


_patch_starlette_template_signature()


PYMSS_LABELS = [
    "去混响", "去混响（激进）", "去伴奏", "去伴奏（激进）", "提主旋律",
    "去伴奏（becruily deux）", "去混响（anvuew 22.505）",
]
_STATE = {"msst_model": config.SEP_MODEL}


def convert(
    song_name_src,
    key_shift,
    vocal_vol,
    inst_vol,
    model_dropdown,
    reverb_intensity=4,
    delay_intensity=0,
    f0_method="rmvpe",
    index_rate=0.75,
    filter_radius=3,
    uvr5_agg=10,
    uvr5_tta=False,
    uvr5_postprocess=False,
    uvr5_window_size=512,
    uvr5_high_end_process="mirroring",
    msst_batch_size=1,
    msst_num_overlap=4,
    msst_normalize=False,
    msst_use_tta=False,
    msst_model=None,
    vocal_postprocess=False,
    shift_accompaniment=True,
    chorus_seconds=None,
    dereverb_model=None,
    inst_volume_db=-6.0,
    progress=None,
):
    """兼容原中间层签名的入口。多出来的三个参数本机自用，插件不传也有默认值。"""
    try:
        shift = int(key_shift or 0)
        inst_gain = float(inst_volume_db if inst_volume_db not in (None, "") else -6.0)
        audio, cache_hit, _note = pipeline.run(
            song_name_src,
            key_shift=shift,
            model_dropdown=str(model_dropdown or ""),
            chorus_seconds=chorus_seconds,
            dereverb_model=dereverb_model,
            inst_volume_db=inst_gain,
            shift_instrumental=shift if shift_accompaniment else 0.0,
            progress=progress,
        )
        return audio, "true" if cache_hit else "false"
    except Exception as error:
        traceback.print_exc()
        raise gr.Error("翻唱失败：%s" % error)


def show_model():
    return rvc.list_models()


def cache_info():
    files, total = 0, 0
    for root in (config.CACHE_DIR, config.OUT_DIR):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                files += 1
                total += os.path.getsize(path)
    return {
        "service": "chorus-mw (RVC / NVIDIA)",
        # 别把绝对路径塞进返回值：Gradio 4 看到「存在的路径字符串」会当成文件去
        # 下载，客户端拿到 403。这里只报名字和数字。
        "areas": {
            "results": {
                "files": files,
                "bytes": total,
                "dir": os.path.basename(config.OUT_DIR),
            }
        },
        "total_files": files,
        "total_bytes": total,
    }


def clear_cache(scope="all"):
    deleted, freed = 0, 0
    for root in (config.CACHE_DIR, config.OUT_DIR):
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
                os.remove(path)
                deleted += 1
                freed += size
            except OSError:
                continue
    return {"deleted_files": deleted, "freed_bytes": freed, "scope": "all"}


def show_msst_models():
    current = _STATE["msst_model"]
    return [
        {"id": label, "name": label, "current": label == current}
        for label in PYMSS_LABELS
    ]


def select_msst_model(model_name):
    name = str(model_name or "").strip()
    if name not in PYMSS_LABELS:
        return {"success": False, "error": "不认识的分离模型：%s" % name}
    _STATE["msst_model"] = name
    return {"success": True, "id": name, "name": name}


def _hidden_inputs():
    """顺序必须和 convert() 的参数顺序一致，Gradio 按位置传参。

    visible=False 的组件如果没写 label，Gradio 3.x 在 /info 里会把参数名暴露成
    param_0/param_1…，新版 gradio_client（插件侧是 2.7.2）按参数名传 kwargs 就会报
    “Parameter `xxx` is not a valid key-word argument”。所以每个组件都显式写 label，
    名字必须和 convert() 的形参一模一样。
    """
    return [
        gr.Textbox(label="song_name_src", visible=False),
        gr.Number(label="key_shift", value=0, visible=False),
        gr.Number(label="vocal_vol", value=0, visible=False),
        gr.Number(label="inst_vol", value=0, visible=False),
        gr.Textbox(label="model_dropdown", value="", visible=False),
        gr.Slider(label="reverb_intensity", minimum=0, maximum=10, step=1, value=4, visible=False),
        gr.Slider(label="delay_intensity", minimum=0, maximum=10, step=1, value=0, visible=False),
        gr.Dropdown(label="f0_method", choices=["rmvpe", "pm"], value="rmvpe", visible=False),
        gr.Slider(label="index_rate", minimum=0, maximum=1, step=0.05, value=0.75, visible=False),
        gr.Slider(label="filter_radius", minimum=0, maximum=7, step=1, value=3, visible=False),
        gr.Slider(label="uvr5_agg", minimum=0, maximum=20, step=1, value=10, visible=False),
        gr.Checkbox(label="uvr5_tta", value=False, visible=False),
        gr.Checkbox(label="uvr5_postprocess", value=False, visible=False),
        gr.Dropdown(label="uvr5_window_size", choices=[256, 512, 1024], value=512, visible=False),
        gr.Dropdown(label="uvr5_high_end_process", choices=["mirroring", "none"],
                    value="mirroring", visible=False),
        gr.Number(label="msst_batch_size", value=1, visible=False),
        gr.Number(label="msst_num_overlap", value=4, visible=False),
        gr.Checkbox(label="msst_normalize", value=False, visible=False),
        gr.Checkbox(label="msst_use_tta", value=False, visible=False),
        gr.Textbox(label="msst_model", value="", visible=False),
        gr.Checkbox(label="vocal_postprocess", value=False, visible=False),
        gr.Checkbox(label="shift_accompaniment", value=True, visible=False),
        gr.Number(label="chorus_seconds", value=45, visible=False),
        gr.Textbox(label="dereverb_model", value="", visible=False),
        gr.Number(label="inst_volume_db", value=-6.0, visible=False),
    ]


def build():
    with gr.Blocks(title="chorus-mw") as demo:
        gr.Markdown(
            "## RVC 副歌翻唱中间层（N 卡）\n"
            "整首分离 → 挑 45 秒副歌 → 去混响 → RVC 变声 → 混音。"
        )
        api_out = gr.Audio(visible=False)
        api_flag = gr.Textbox(visible=False)
        show_json = gr.JSON(visible=False)

        inputs = _hidden_inputs()
        gr.Button("API Convert", visible=False).click(
            convert, inputs=inputs, outputs=[api_out, api_flag], api_name="convert"
        )
        gr.Button("API Show Model", visible=False).click(
            show_model, inputs=[], outputs=[show_json], api_name="show_model"
        )
        gr.Button("API Show MSST", visible=False).click(
            show_msst_models, inputs=[], outputs=[show_json], api_name="show_msst_models"
        )
        msst_box = gr.Textbox(visible=False)
        gr.Button("API Select MSST", visible=False).click(
            select_msst_model, inputs=[msst_box], outputs=[show_json],
            api_name="select_msst_model"
        )
        scope_box = gr.Textbox(value="all", visible=False)
        gr.Button("API Cache Info", visible=False).click(
            cache_info, inputs=[], outputs=[show_json], api_name="cache_info"
        )
        gr.Button("API Clear Cache", visible=False).click(
            clear_cache, inputs=[scope_box], outputs=[show_json], api_name="clear_cache"
        )
        gr.Markdown("接口说明见 README；浏览器直接打开本页只为自检，不用来点歌。")
    return demo


if __name__ == "__main__":
    config.ensure_dirs()
    problems = config.check_paths()
    if problems:
        print("启动前体检没过：", flush=True)
        for item in problems:
            print("  - " + item, flush=True)
        raise SystemExit(2)
    print("chorus-mw 启动，RVC=%s 端口=%d" % (config.RVC_ROOT, config.RVC_PORT), flush=True)
    build().queue().launch(
        server_name=os.environ.get("CHORUS_HOST", "127.0.0.1"),
        server_port=int(os.environ.get("CHORUS_PORT", "3333")),
        show_error=True,
    )
