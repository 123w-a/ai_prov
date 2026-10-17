param(
    [switch]$PreflightOnly,
    [switch]$RestartBackend
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = 8010
$FrontendStartPort = 5178
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $Root "frontend\web"

# 日志统一写到项目之外，别让仓库里长出 .log（可被 CHEF_LOG_DIR 覆盖）
if ($env:CHEF_LOG_DIR) {
    $LogDir = $env:CHEF_LOG_DIR
} elseif ($env:LOCALAPPDATA) {
    $LogDir = Join-Path $env:LOCALAPPDATA "XiaoShanGuanJia\logs"
} else {
    $LogDir = Join-Path $env:USERPROFILE ".xiaoshanguanjia\logs"
}
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$BackendOut = Join-Path $LogDir "backend.stdout.log"
$BackendErr = Join-Path $LogDir "backend.stderr.log"
$FrontendOut = Join-Path $LogDir "frontend.stdout.log"
$FrontendErr = Join-Path $LogDir "frontend.stderr.log"

function Fail([string]$Message) {
    Write-Host "[ERROR] $Message" -ForegroundColor Red
    exit 1
}

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds, [int]$ExpectedStatus = 200) {
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
            if ($Response.StatusCode -eq $ExpectedStatus) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Stop-BackendOnPort([int]$Port) {
    # 只杀"长得像本项目后端"的进程：可执行文件位于本项目 .venv 下，或命令行含 run.py。
    # 拿不准的一律中止（绝不误杀别人的服务）——与 run.py 的 is_our_backend 同一套保守口径。
    $VenvRoot = (Join-Path $Root ".venv").ToLowerInvariant()
    $Conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $Conns) {
        Write-Host "[OK] 端口 $Port 本来就空闲。"
        return $true
    }
    foreach ($Conn in $Conns) {
        $OwnerPid = $Conn.OwningProcess
        if (-not $OwnerPid) { continue }
        $Proc = Get-CimInstance Win32_Process -Filter "ProcessId=$OwnerPid" -ErrorAction SilentlyContinue
        if (-not $Proc) {
            Fail "端口 $Port 被 PID $OwnerPid 占用，但读不到该进程信息，已中止以免误杀。"
        }
        $ExePath = ""
        if ($Proc.ExecutablePath) { $ExePath = $Proc.ExecutablePath.ToLowerInvariant() }
        $CmdLine = ""
        if ($Proc.CommandLine) { $CmdLine = $Proc.CommandLine.ToLowerInvariant() }
        $LooksOurs = $ExePath.StartsWith($VenvRoot) -or (($CmdLine -match "\.venv") -and ($CmdLine -match "run\.py"))
        if (-not $LooksOurs) {
            Fail "端口 $Port 被非本项目进程占用（PID $OwnerPid，$ExePath），未做任何处理，请手动确认。"
        }
        Write-Host "[INFO] 正在关闭旧后端：PID $OwnerPid"
        Stop-Process -Id $OwnerPid -Force -ErrorAction SilentlyContinue
    }
    # 等端口真正释放（Windows 释放有延迟），最多 5 秒
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-Port $Port)) {
            Write-Host "[OK] 端口 $Port 已释放。"
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-Port $Port)
}

function Stop-FrontendOnPort([int]$Port) {
    # 只杀"长得像本项目前端 dev server"的进程：命令行同时含本项目前端目录与 vite。
    # 拿不准的一律中止（绝不误杀别人的服务）——与 Stop-BackendOnPort 同一套保守口径。
    # 注意：vite 绑 IPv4 还是 IPv6 由 --host 决定（--host 127.0.0.1 只绑 IPv4，不带则绑 localhost/::1），
    # 这里按端口取全部监听连接，不区分协议族，避免漏杀。
    $FrontendDirLower = $FrontendDir.ToLowerInvariant().Replace("/", "\")
    $Conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $Conns) {
        Write-Host "[OK] 端口 $Port 本来就空闲。"
        return $true
    }
    foreach ($Conn in $Conns) {
        $OwnerPid = $Conn.OwningProcess
        if (-not $OwnerPid) { continue }
        $Proc = Get-CimInstance Win32_Process -Filter "ProcessId=$OwnerPid" -ErrorAction SilentlyContinue
        if (-not $Proc) {
            Fail "端口 $Port 被 PID $OwnerPid 占用，但读不到该进程信息，已中止以免误杀。"
        }
        $ExePath = ""
        if ($Proc.ExecutablePath) { $ExePath = $Proc.ExecutablePath.ToLowerInvariant() }
        $CmdNorm = ""
        if ($Proc.CommandLine) { $CmdNorm = $Proc.CommandLine.ToLowerInvariant().Replace("/", "\") }
        $LooksOurs = $CmdNorm.Contains($FrontendDirLower) -and ($CmdNorm -match "vite")
        if (-not $LooksOurs) {
            Fail "端口 $Port 被非本项目进程占用（PID $OwnerPid，$ExePath），未做任何处理，请手动确认。"
        }
        Write-Host "[INFO] 正在关闭旧前端 dev server：PID $OwnerPid"
        Stop-Process -Id $OwnerPid -Force -ErrorAction SilentlyContinue
    }
    # 等端口真正释放（Windows 释放有延迟），最多 5 秒
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Test-Port $Port)) {
            Write-Host "[OK] 端口 $Port 已释放。"
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return -not (Test-Port $Port)
}

if (-not (Test-Path -LiteralPath (Join-Path $Root ".env"))) {
    Fail "缺少 .env。请先按 .env.example 配置模型与知识库参数。"
}
if (-not (Test-Path -LiteralPath $Python)) {
    Fail "缺少 Python 虚拟环境：$Python"
}
if (-not (Test-Path -LiteralPath $FrontendDir)) {
    Fail "缺少前端目录：$FrontendDir"
}
$Node = Get-Command node -ErrorAction SilentlyContinue
$Npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $Npm) {
    $Npm = Get-Command npm.exe -ErrorAction SilentlyContinue
}
if (-not $Node -or -not $Npm) {
    Fail "未找到 Node.js 或可执行的 npm.cmd/npm.exe，请先安装并加入 PATH。"
}

$BackendRunning = Test-Port $BackendPort
if ($BackendRunning -and $RestartBackend -and -not $PreflightOnly) {
    # -RestartBackend：不要"复用旧进程"，先把它关掉，让新代码真正生效。
    # 只在显式传开关时走到这里；不加开关时下面的 readiness 复用逻辑一字不动。
    Write-Host "[INFO] -RestartBackend 已指定，正在关闭 $BackendPort 上的旧后端…"
    if (-not (Stop-BackendOnPort -Port $BackendPort)) {
        Fail "无法释放后端端口 $BackendPort，请手动处理占用进程后重试。"
    }
    $BackendRunning = Test-Port $BackendPort
}
if ($BackendRunning) {
    try {
        $Ready = Invoke-RestMethod -Uri "http://127.0.0.1:$BackendPort/api/health/ready" -TimeoutSec 3
        if ($Ready.status -ne "ok") {
            Fail "后端端口 $BackendPort 已占用，但 readiness 未通过。"
        }
    } catch {
        Fail "后端端口 $BackendPort 被其他程序占用。"
    }
}

# 前端固定跑在 $FrontendStartPort（5178）：不再向上扫空闲端口——
# 否则旧实例占着 5178 时新实例会漂到 5179，浏览器就开错端口。
# 默认行为 = 先回收本项目旧前端（只杀自家 vite 进程），再在原端口启动，保证"始终只有一个前端"。
$FrontendPort = $FrontendStartPort
if ($PreflightOnly) {
    if (Test-Port $FrontendPort) {
        Write-Host "[INFO] 预检：前端端口 $FrontendPort 已被占用（启动时会先回收本项目旧实例）。"
    } else {
        Write-Host "[INFO] 预检：前端端口 $FrontendPort 空闲。"
    }
} elseif (Test-Port $FrontendPort) {
    Write-Host "[INFO] 端口 $FrontendPort 已被占用，正在回收本项目旧前端…"
    if (-not (Stop-FrontendOnPort -Port $FrontendPort)) {
        Fail "无法释放前端端口 $FrontendPort，请手动处理占用进程后重试。"
    }
}

Write-Host "[OK] 预检通过：Python、Node、目录、.env 和端口均可用。"
if ($PreflightOnly) {
    exit 0
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
if (-not $BackendRunning) {
    try {
        [System.IO.File]::WriteAllText($BackendOut, "", $Utf8NoBom)
        [System.IO.File]::WriteAllText($BackendErr, "", $Utf8NoBom)
    } catch {
        # 旧后端仍持有固定日志文件时，使用本次启动专属日志，避免日志锁阻断启动。
        $RunTag = Get-Date -Format "yyyyMMdd-HHmmss"
        $BackendOut = Join-Path $LogDir "backend.$RunTag.stdout.log"
        $BackendErr = Join-Path $LogDir "backend.$RunTag.stderr.log"
        [System.IO.File]::WriteAllText($BackendOut, "", $Utf8NoBom)
        [System.IO.File]::WriteAllText($BackendErr, "", $Utf8NoBom)
    }
    $PreviousPythonIOEncoding = $env:PYTHONIOENCODING
    $PreviousPythonUTF8 = $env:PYTHONUTF8
    try {
        # Windows 后台重定向不能依赖系统代码页，统一要求 Python 输出 UTF-8。
        $env:PYTHONIOENCODING = "utf-8"
        $env:PYTHONUTF8 = "1"
        $Backend = Start-Process -FilePath $Python -ArgumentList "run.py" `
            -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr
    } finally {
        if ($null -eq $PreviousPythonIOEncoding) {
            Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONIOENCODING = $PreviousPythonIOEncoding
        }
        if ($null -eq $PreviousPythonUTF8) {
            Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONUTF8 = $PreviousPythonUTF8
        }
    }
    # 就绪窗口必须覆盖冷启动预热：知识库预热实测 74.0s（reranker=on，见后端日志
    # 的 [warmup] 行）。原先这里只等 60 秒 —— 必然判超时，而脚本 Fail 退出时会把
    # 刚起好的后端进程一起回收（2026-10-14 实测：脚本报「后端启动或 readiness
    # 超时」之后，8010 端口从此完全不可达，看起来像"后端没起来"，实际是起来了又被收走）。
    # 180 秒 = 预热 74s + 首次加载向量模型与 Chroma 集合的余量。
    if (-not (Wait-Http "http://127.0.0.1:$BackendPort/api/health/ready" 180)) {
        Fail "后端启动或 readiness 超时（已等 180 秒），请查看 $BackendErr。知识库预热本身约需 74 秒；若日志停在预热那行，说明模型加载卡住了。"
    }
    Write-Host "[OK] 后端已启动：http://127.0.0.1:$BackendPort（PID $($Backend.Id)，单 worker）"
} else {
    $Reused = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $ReusedPid = if ($Reused) { $Reused.OwningProcess } else { '?' }
    $ReusedStart = ''
    if ($ReusedPid -ne '?') {
        $ReusedProc = Get-CimInstance Win32_Process -Filter "ProcessId=$ReusedPid" -ErrorAction SilentlyContinue
        if ($ReusedProc) { $ReusedStart = $ReusedProc.CreationDate }
    }
    Write-Host "[OK] 后端已运行：http://127.0.0.1:$BackendPort（复用现有进程 PID $ReusedPid，启动于 $ReusedStart）"
    Write-Host "     若刚改过代码要生效，请用：.\start.ps1 -RestartBackend（先杀旧进程再起新的）"
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($FrontendOut, "", $Utf8NoBom)
[System.IO.File]::WriteAllText($FrontendErr, "", $Utf8NoBom)
# 不传 --host：让 vite.config.ts 里的 host:true 生效（同时监听 0.0.0.0 与 ::）。
# 传 "--host 127.0.0.1" 会覆盖该配置，把开发服务器锁回本机 —— 那样手机连同一个
# WiFi 也打不开页面，「拍冰箱照片录食材」就只能在电脑上用（2026-10-15 实测踩到：
# vite 配置早就写了 host:true，却被这行参数盖掉，表现为"配置没错但手机就是连不上"）。
$Frontend = Start-Process -FilePath $Npm.Path -ArgumentList @("run", "dev", "--", "--port", "$FrontendPort", "--strictPort") `
    -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $FrontendOut -RedirectStandardError $FrontendErr
if (-not (Wait-Http "http://127.0.0.1:$FrontendPort" 30)) {
    Fail "前端启动超时，请查看 $FrontendErr。"
}

Write-Host "[OK] 前端已启动：http://127.0.0.1:$FrontendPort（PID $($Frontend.Id)）"
Write-Host "按 Ctrl+C 不会自动关闭后台进程；需要停止时使用 Stop-Process -Id <PID>。"
