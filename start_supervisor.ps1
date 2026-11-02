<#
  chorus-mw 守护启动器
  --------------------
  用循环看住 app.py：进程退出（包括 torch/LLVM 内存崩）就自动重启，
  每崩一次往 supervisor.log 记一行，关掉本窗口即停止。

  独立运行，不并入 QQ 机器人的一键启动脚本。
#>
[CmdletBinding()]
param(
    [int]$Port = 3333
)

$ErrorActionPreference = 'Continue'
try { $Host.UI.RawUI.WindowTitle = '翻唱中间层3333' } catch {}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = 'C:\Users\21903\AppData\Local\Programs\Python\Python312\python.exe'
$RvcRoot = 'D:\PYMSS-RVC\RVC20260723Nvidia50x0\RVC20260718Nvidia50x0'
$LogDir = Join-Path $Here '_work'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$SupervisorLog = Join-Path $LogDir 'supervisor.log'

$env:CHORUS_RVC_ROOT = $RvcRoot
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTORCH_CUDA_ALLOC_CONF = 'expandable_segments:True'

function Test-Port([int]$p) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Say([string]$text, [string]$color = 'Gray') {
    $stamp = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$stamp] $text" -ForegroundColor $color
    Add-Content -Path $SupervisorLog -Value "[$stamp] $text" -Encoding UTF8
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
