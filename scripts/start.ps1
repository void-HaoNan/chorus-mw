<#
  chorus-mw 启停脚本
  -------------------
  用法：
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1            # 启动
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Stop      # 停止（连旧守护、常驻 worker 一起收）
    powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Status    # 看状态（含谁在管）

  必填环境变量：
    CHORUS_RVC_ROOT   RVC 整合包根目录（里面有 runtime\python.exe、ffmpeg.exe、assets\weights）
  可选：
    CHORUS_PYTHON     跑服务端的 Python，默认从 PATH 找 python
    CHORUS_PORT       监听端口，默认 3333
    CHORUS_HOST       监听地址，默认 127.0.0.1
    CHORUS_PARENT_PID 父进程看护；翻唱插件拉起时自动带 AstrBot 的 pid

  0.9.16 起：-Stop 会先收掉旧版守护脚本 start_supervisor.ps1（否则它会在 5~60 秒后
  把 app.py 又拉起来），再 taskkill /T 整棵中间层进程树，最后确认端口真的空出来。
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$port = if ($env:CHORUS_PORT) { [int]$env:CHORUS_PORT } else { 3333 }
$logDir = Join-Path $root '_work'
$log = Join-Path $logDir 'mw.log'
$err = Join-Path $logDir 'mw.err'
$CREATE_NO_WINDOW = 0x08000000

function Test-Listen([int]$Port) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Get-Owner([int]$Port) {
    return (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1).OwningProcess
}

function Get-Python {
    if ($env:CHORUS_PYTHON -and (Test-Path $env:CHORUS_PYTHON)) { return $env:CHORUS_PYTHON }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch '\\WindowsApps\\') { return $cmd.Source }
    return $null
}

function Get-ProcessTable {
    # 只读一次进程表：查守护、查祖先、查残留都在这份数据上做
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop)
    } catch {
        return @()
    }
}

function Get-ProcessById($Table, [int]$Id) {
    foreach ($item in $Table) {
        if ([int]$item.ProcessId -eq $Id) { return $item }
    }
    return $null
}

function Test-Descendant($Table, [int]$ChildPid, [int]$RootPid) {
    $cursor = $ChildPid
    for ($i = 0; $i -lt 32 -and $cursor -gt 0; $i++) {
        if ($cursor -eq $RootPid) { return $true }
        $proc = Get-ProcessById $Table $cursor
        if (-not $proc) { return $false }
        $cursor = [int]$proc.ParentProcessId
    }
    return $false
}

function Test-TreeHasRoot($Table, [int]$RootPid) {
    # 守护脚本用相对路径启动时命令行里没有目录，靠子孙进程（常驻 worker 是绝对路径）认亲
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($RootPid)
    $guard = 0
    while ($queue.Count -gt 0 -and $guard -lt 64) {
        $guard++
        $parent = [int]$queue.Dequeue()
        foreach ($proc in $Table) {
            if ([int]$proc.ParentProcessId -ne $parent) { continue }
            $cmd = [string]$proc.CommandLine
            if ($cmd.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
            $queue.Enqueue([int]$proc.ProcessId)
        }
    }
    return $false
}

function Get-SupervisorPids($Table, [int]$ListenerPid) {
    # 认「这一份目录的守护」：命令行带目录 / 祖先是带目录的启动器 / 子孙里有跑本目录的进程 / 拉起的就是当前监听者
    $found = @()
    foreach ($proc in $Table) {
        $name = [string]$proc.Name
        if ($name -notmatch '^(powershell|pwsh)') { continue }
        $cmd = [string]$proc.CommandLine
        if ($cmd -notmatch 'start_supervisor\.ps1') { continue }
        $mine = $cmd.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0
        if (-not $mine -and $ListenerPid -gt 0) {
            $mine = Test-Descendant $Table $ListenerPid ([int]$proc.ProcessId)
        }
        if (-not $mine) {
            $mine = Test-TreeHasRoot $Table ([int]$proc.ProcessId)
        }
        if (-not $mine) {
            # 控制台用 `cd /d <目录> && powershell -File start_supervisor.ps1` 起，目录在祖先命令行里
            $cursor = [int]$proc.ParentProcessId
            for ($i = 0; $i -lt 4 -and $cursor -gt 0; $i++) {
                $ancestor = Get-ProcessById $Table $cursor
                if (-not $ancestor) { break }
                if (([string]$ancestor.CommandLine).IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    $mine = $true
                    break
                }
                $cursor = [int]$ancestor.ParentProcessId
            }
        }
        if ($mine) { $found += [int]$proc.ProcessId }
    }
    return $found
}

function Stop-Tree([int]$ProcessId, [string]$Label) {
    if ($ProcessId -le 0) { return }
    try {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
        Write-Host ("  已停止{0}（pid {1}，含子进程）" -f $Label, $ProcessId) -ForegroundColor Green
    } catch {
        Write-Host ("  停止{0}失败（pid {1}）：{2}" -f $Label, $ProcessId, $_.Exception.Message) -ForegroundColor Yellow
    }
}

function Get-ListenerCommand($Table, [int]$ProcessId) {
    $proc = Get-ProcessById $Table $ProcessId
    if (-not $proc) { return '' }
    return [string]$proc.CommandLine
}

if ($Status) {
    $table = Get-ProcessTable
    if (Test-Listen $port) {
        $owner = Get-Owner $port
        Write-Host ("chorus-mw  运行中，http://127.0.0.1:{0}（pid {1}）" -f $port, $owner) -ForegroundColor Green
        $cmd = Get-ListenerCommand $table $owner
        if ($cmd) { Write-Host ("  进程：{0}" -f $cmd) -ForegroundColor DarkGray }
    } else {
        Write-Host 'chorus-mw  未运行' -ForegroundColor DarkGray
    }
    $supervisors = @(Get-SupervisorPids $table 0)
    if ($supervisors.Count -gt 0) {
        Write-Host ("  旧守护 start_supervisor.ps1 还在：pid {0}" -f ($supervisors -join ', ')) -ForegroundColor Yellow
        Write-Host '    （插件托管时不该有它；-Stop 会一起收掉）' -ForegroundColor DarkGray
    }
    $rvcRoot = '(未设置)'
    if ($env:CHORUS_RVC_ROOT) { $rvcRoot = $env:CHORUS_RVC_ROOT }
    Write-Host ("  CHORUS_RVC_ROOT = {0}" -f $rvcRoot)
    $watch = $env:CHORUS_PARENT_PID
    if ($watch) { Write-Host ("  CHORUS_PARENT_PID = {0}" -f $watch) -ForegroundColor DarkGray }
    exit 0
}

if ($Stop) {
    $table = Get-ProcessTable
    $listener = 0
    if (Test-Listen $port) { $listener = [int](Get-Owner $port) }

    # 只停这一份 chorus-mw：端口上是别的程序就拒绝，别误杀
    if ($listener -gt 0) {
        $cmd = Get-ListenerCommand $table $listener
        $isChorus = $cmd -match 'app\.py'
        if (-not $isChorus) {
            try {
                $info = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/info" -f $port) -UseBasicParsing -TimeoutSec 8
                $isChorus = ($info.Content -match '/convert') -and ($info.Content -match 'named_endpoints')
            } catch { }
        }
        if (-not $isChorus) {
            Write-Host ("端口 {0} 被别的程序占用（pid {1}），不归本脚本管，拒绝停止。" -f $port, $listener) -ForegroundColor Red
            Write-Host ("  进程：{0}" -f $cmd) -ForegroundColor DarkGray
            exit 2
        }
    }

    $supervisors = @(Get-SupervisorPids $table $listener)
    if ($supervisors.Count -gt 0) {
        Write-Host ("先收掉旧守护（否则它会把中间层再拉起来）：pid {0}" -f ($supervisors -join ', ')) -ForegroundColor Yellow
        foreach ($sid in $supervisors) { Stop-Tree $sid '旧守护' }
    }

    if ($listener -gt 0) {
        Stop-Tree $listener '中间层'
    } else {
        Write-Host 'chorus-mw 没在运行' -ForegroundColor DarkGray
    }

    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-Listen $port)) { break }
        Start-Sleep -Milliseconds 500
    }

    # 兜底：app.py 已经没了、worker 还挂着的残局（命令行里带本目录）
    $table2 = Get-ProcessTable
    foreach ($proc in $table2) {
        $cmd = [string]$proc.CommandLine
        if ($cmd -match 'resident_worker\.py|rvc_worker\.py|sep_worker\.py' -and
            $cmd.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            Stop-Tree ([int]$proc.ProcessId) '残留 worker'
        }
    }

    if (Test-Listen $port) {
        Write-Host ("端口 {0} 还被占着（pid {1}），可能有别的程序或别的守护在拉起它。" -f $port, (Get-Owner $port)) -ForegroundColor Yellow
        exit 1
    }
    Write-Host 'chorus-mw 已完全停止（守护 / 中间层 / 常驻 worker）' -ForegroundColor Green
    exit 0
}

# ---------- 启动 ----------
Write-Host '=== chorus-mw 启动 ===' -ForegroundColor Cyan

if (Test-Listen $port) {
    Write-Host ("  端口 {0} 已被占用（pid {1}），先跑 -Status 看看是不是已经在跑" -f $port, (Get-Owner $port)) -ForegroundColor Yellow
    exit 0
}

# 端口空着但旧守护还在退避等待：不先收掉它，5~60 秒后它会再起一个抢 3333
$table = Get-ProcessTable
$supervisors = @(Get-SupervisorPids $table 0)
if ($supervisors.Count -gt 0) {
    Write-Host ("  检测到旧守护 start_supervisor.ps1（pid {0}），先收掉再启动，避免两边抢端口" -f ($supervisors -join ', ')) -ForegroundColor Yellow
    foreach ($sid in $supervisors) { Stop-Tree $sid '旧守护' }
    Start-Sleep -Seconds 2
    if (Test-Listen $port) {
        Write-Host ("  旧守护退出前又拉起了中间层（pid {0}），按已在运行处理" -f (Get-Owner $port)) -ForegroundColor Yellow
        exit 0
    }
}

if (-not $env:CHORUS_RVC_ROOT) {
    Write-Host '  没有设置 CHORUS_RVC_ROOT，先把它设成 RVC 整合包根目录，例如：' -ForegroundColor Red
    Write-Host '    $env:CHORUS_RVC_ROOT = ''D:\your-path\RVC20260718Nvidia50x0''' -ForegroundColor Yellow
    exit 2
}
if (-not (Test-Path (Join-Path $env:CHORUS_RVC_ROOT 'runtime\python.exe'))) {
    Write-Host ("  {0} 底下找不到 runtime\python.exe，这个变量应该指向 RVC 整合包根目录" -f $env:CHORUS_RVC_ROOT) -ForegroundColor Red
    exit 2
}

$python = Get-Python
if (-not $python) {
    Write-Host '  找不到 python：设 CHORUS_PYTHON 指过去，或者把 python 加进 PATH' -ForegroundColor Red
    exit 2
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# 本机开着系统代理时，Gradio 4 探测 localhost 会走代理然后拒绝启动
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:no_proxy = $env:NO_PROXY

Write-Host ("  python : {0}" -f $python)
Write-Host ("  RVC    : {0}" -f $env:CHORUS_RVC_ROOT)
Write-Host ("  端口   : {0}" -f $port)
Write-Host ("  日志   : {0}" -f $log)
if ($env:CHORUS_PARENT_PID) {
    Write-Host ("  看护   : 父进程 pid {0}（它退出时本进程连 worker 一起退）" -f $env:CHORUS_PARENT_PID) -ForegroundColor DarkGray
}

Start-Process -FilePath $python `
    -ArgumentList '-X', 'utf8', 'app.py' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err

for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Listen $port) {
        Write-Host ("  就绪  http://127.0.0.1:{0}（{1} 秒）" -f $port, $i) -ForegroundColor Green
        exit 0
    }
}

Write-Host '  60 秒还没起来，看这两个文件：' -ForegroundColor Red
Write-Host ("    {0}" -f $log)
Write-Host ("    {0}" -f $err)
Get-Content $err -Tail 20 -ErrorAction SilentlyContinue
exit 1
