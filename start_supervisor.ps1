<#
  chorus-mw 守护启动器（0.9.11 起的老办法，0.9.16 起只在插件关闭自动托管时才用）
  --------------------
  用循环看住 app.py：进程退出（包括 torch/LLVM 内存崩）就自动重启，
  每崩一次往 supervisor.log 记一行，关掉本窗口即停止。

  ⚠️ 插件 astrbot_plugin_chorus_cover 0.19.17 起会自己拉起/关闭中间层（auto_middleware 默认开）。
     插件托管时**不要**再开这个守护：两边会互相拉起，关掉 AstrBot 也关不干净。
     只有把插件设置 auto_middleware 关掉、或者你用别的客户端调中间层时，才用它。

  用法（全部走环境变量，不写死本机路径）：
    $env:CHORUS_RVC_ROOT = 'D:\你的路径\RVC20260718Nvidia50x0'   # 必填
    $env:CHORUS_PYTHON   = 'C:\...\python.exe'                  # 可选，默认找 PATH 里的 python
    powershell -ExecutionPolicy Bypass -File start_supervisor.ps1

  也可以直接传参数覆盖：-RvcRoot 'D:\...' -Python 'C:\...\python.exe'
#>
[CmdletBinding()]
param(
    [int]$Port = 3333,
    [string]$RvcRoot = "",
    [string]$Python = ""
)

$ErrorActionPreference = 'Continue'
try { $Host.UI.RawUI.WindowTitle = '翻唱中间层3333' } catch {}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Here '_work'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$SupervisorLog = Join-Path $LogDir 'supervisor.log'

function Say([string]$text, [string]$color = 'Gray') {
    $stamp = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$stamp] $text" -ForegroundColor $color
    Add-Content -Path $SupervisorLog -Value "[$stamp] $text" -Encoding UTF8
}

# 插件在托管时不要跟它抢：插件写 middleware.json 记归属，astrbot_pid 还活着就说明它在管
$markerCandidates = @()
if ($env:CHORUS_MARKER_FILE) { $markerCandidates += $env:CHORUS_MARKER_FILE }
if ($env:ASTRBOT_DATA_DIR) {
    $markerCandidates += (Join-Path $env:ASTRBOT_DATA_DIR 'plugin_data\astrbot_plugin_chorus_cover\middleware.json')
}
if ($env:USERPROFILE) {
    $markerCandidates += (Join-Path $env:USERPROFILE 'data\plugin_data\astrbot_plugin_chorus_cover\middleware.json')
}
foreach ($marker in ($markerCandidates | Select-Object -Unique)) {
    if (-not (Test-Path $marker)) { continue }
    try {
        $data = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        $owner = [int]$data.astrbot_pid
        if ($owner -gt 0 -and (Get-Process -Id $owner -ErrorAction SilentlyContinue)) {
            Say ("插件（AstrBot pid {0}）正在托管中间层，本守护不启动，避免两边互相拉起。" -f $owner) 'Yellow'
            Say '  要改成手动管理：先在插件设置里关掉 auto_middleware，再重开本窗口。' 'DarkGray'
            Start-Sleep -Seconds 8
            exit 0
        }
    } catch {
        # 标记损坏就当没有；不能因为读不懂一个 json 拒绝启动
    }
}

# ---- 解释器：参数 > CHORUS_PYTHON > PATH 里的 python ----
if (-not $Python) { $Python = $env:CHORUS_PYTHON }
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch '\\WindowsApps\\') { $Python = $cmd.Source }
}
if (-not $Python -or -not (Test-Path $Python)) {
    Say '找不到 Python：设 CHORUS_PYTHON 指过去，或把 python 加进 PATH 再启动。' 'Red'
    Start-Sleep -Seconds 5
    exit 2
}

# ---- RVC 整合包：参数 > CHORUS_RVC_ROOT ----
if (-not $RvcRoot) { $RvcRoot = $env:CHORUS_RVC_ROOT }
if (-not $RvcRoot -or -not (Test-Path (Join-Path $RvcRoot 'runtime\python.exe'))) {
    Say 'CHORUS_RVC_ROOT 没设或指错了：它要指向 RVC 整合包根目录（里面要有 runtime\python.exe）。' 'Red'
    Say "  当前值：[$RvcRoot]" 'DarkGray'
    Say "  例：`$env:CHORUS_RVC_ROOT = 'D:\你的路径\RVC20260718Nvidia50x0'" 'Yellow'
    Start-Sleep -Seconds 5
    exit 2
}
Say ("python：{0}" -f $Python) 'DarkGray'
Say ("RVC  ：{0}" -f $RvcRoot) 'DarkGray'

$env:CHORUS_RVC_ROOT = $RvcRoot
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTORCH_CUDA_ALLOC_CONF = 'expandable_segments:True'

function Test-Port([int]$p) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1)
}

# 单实例：两个守护循环会各起一个 app.py，抢 3333 端口，日志里互相刷「已在监听」
$mutex = New-Object System.Threading.Mutex($false, 'chorus_mw_supervisor')
if (-not $mutex.WaitOne(0)) {
    Say '已经有一个中间层守护循环在跑，本窗口不重复启动' 'Yellow'
    Start-Sleep -Seconds 5
    exit 0
}

$crashes = 0
while ($true) {
    if (Test-Port $Port) {
        Say "端口 $Port 已在监听，可能已经有中间层在跑；等 10 秒再看。" 'Yellow'
        Start-Sleep -Seconds 10
        continue
    }

    Say '启动中间层…' 'Cyan'
    $out = Join-Path $LogDir 'mw.log'
    $err = Join-Path $LogDir 'mw.err'
    $proc = Start-Process -FilePath $Python -ArgumentList @('-X', 'utf8', 'app.py') `
        -WorkingDirectory $Here -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    # app 的日志落在 _work\mw.log；这里起一个跟随进程把它转印到本标签，
    # 这样标签里能看到实时转换日志，文件里也留着备查。
    # 标题用 cmd 的 title 设，别让 $Host 被外层展开成对象（之前那串红字就是这么来的）；
    # Get-Content 必须显式 -Encoding UTF8，否则按 GBK 读 UTF-8 日志，中文全是乱码
    Start-Process -FilePath 'powershell' -ArgumentList @(
        '-NoProfile', '-Command',
        "& { `$Host.UI.RawUI.WindowTitle = '翻唱中间层3333'; Get-Content -LiteralPath '$out' -Wait -Tail 0 -Encoding UTF8 }"
    ) -NoNewWindow | Out-Null

    # 等它把端口监听起来（最多 60 秒）
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if ($proc.HasExited) { break }
        if (Test-Port $Port) { $ready = $true; break }
    }

    if ($ready) {
        Say "中间层就绪 http://127.0.0.1:$Port（pid $($proc.Id)）" 'Green'
        $crashes = 0
        Wait-Process -Id $proc.Id -ErrorAction SilentlyContinue
        Say '中间层进程退出，准备重启。' 'Yellow'
    } else {
        $crashes++
        Say "中间层没起来（第 $crashes 次）。看 _work\mw.err 末尾。" 'Red'
    }

    # 崩溃间隔太短就多等一会儿，避免死循环刷屏
    $wait = [Math]::Min(60, 5 + $crashes * 5)
    Say "等 $wait 秒再重启…" 'DarkGray'
    Start-Sleep -Seconds $wait
}
