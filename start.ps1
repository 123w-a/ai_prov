param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendPort = 8010
$FrontendStartPort = 5178
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$FrontendDir = Join-Path $Root "frontend\web"
$BackendOut = Join-Path $Root ".backend.stdout.log"
$BackendErr = Join-Path $Root ".backend.stderr.log"
$FrontendOut = Join-Path $Root ".frontend.stdout.log"
$FrontendErr = Join-Path $Root ".frontend.stderr.log"

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

$FrontendPort = $null
for ($Port = $FrontendStartPort; $Port -le ($FrontendStartPort + 20); $Port++) {
    if (-not (Test-Port $Port)) {
        $FrontendPort = $Port
        break
    }
}
if (-not $FrontendPort) {
    Fail "找不到空闲前端端口（$FrontendStartPort-$($FrontendStartPort + 20)）。"
}

Write-Host "[OK] 预检通过：Python、Node、目录、.env 和端口均可用。"
if ($PreflightOnly) {
    exit 0
}

if (-not $BackendRunning) {
    $Backend = Start-Process -FilePath $Python -ArgumentList "run.py" `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr
    if (-not (Wait-Http "http://127.0.0.1:$BackendPort/api/health/ready" 60)) {
        Fail "后端启动或 readiness 超时，请查看 $BackendErr。"
    }
    Write-Host "[OK] 后端已启动：http://127.0.0.1:$BackendPort（PID $($Backend.Id)，单 worker）"
} else {
    Write-Host "[OK] 后端已运行：http://127.0.0.1:$BackendPort"
}

$Frontend = Start-Process -FilePath $Npm.Path -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "$FrontendPort", "--strictPort") `
    -WorkingDirectory $FrontendDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $FrontendOut -RedirectStandardError $FrontendErr
if (-not (Wait-Http "http://127.0.0.1:$FrontendPort" 30)) {
    Fail "前端启动超时，请查看 $FrontendErr。"
}

Write-Host "[OK] 前端已启动：http://127.0.0.1:$FrontendPort（PID $($Frontend.Id)）"
Write-Host "按 Ctrl+C 不会自动关闭后台进程；需要停止时使用 Stop-Process -Id <PID>。"
