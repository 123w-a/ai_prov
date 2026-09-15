# Keep the machine awake for unattended runs, including when the laptop lid is closed.
$ErrorActionPreference = "Stop"
$SleepSubgroupGuid = "238c9fa8-0aad-41ed-83f4-97be242c8f20"
$ButtonSubgroupGuid = "4f971e89-eebd-4455-a8de-9e59040e7347"
$LidActionGuid = "5ca83367-6e45-459f-a27b-476b1d01c936"
$StandbyIdleGuid = "29f6c1db-86da-48c5-9fdb-f2b67b1f44da"
$HibernateIdleGuid = "9d7815a6-7ee4-497e-8888-515a05f02364"
$UnattendedSleepGuid = "7bc4a2f9-d8fc-4469-b07b-33eb785aaca0"

function Test-IsAdministrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
    return $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-PowerCfg {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & powercfg @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "powercfg failed: powercfg $($Arguments -join ' ')"
    }
}

function Set-OvernightPowerSettings {
    Invoke-PowerCfg @("/setacvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE", "0")
    Invoke-PowerCfg @("/setdcvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", "STANDBYIDLE", "0")
    Invoke-PowerCfg @("/setacvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", "HIBERNATEIDLE", "0")
    Invoke-PowerCfg @("/setdcvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", "HIBERNATEIDLE", "0")
    Invoke-PowerCfg @("/setacvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", $UnattendedSleepGuid, "0")
    Invoke-PowerCfg @("/setdcvalueindex", "SCHEME_CURRENT", "SUB_SLEEP", $UnattendedSleepGuid, "0")

    # 0 means "Do nothing" for both AC and battery power.
    Invoke-PowerCfg @("/setacvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", $LidActionGuid, "0")
    Invoke-PowerCfg @("/setdcvalueindex", "SCHEME_CURRENT", "SUB_BUTTONS", $LidActionGuid, "0")
    Invoke-PowerCfg @("/setactive", "SCHEME_CURRENT")
}

try {
    Set-OvernightPowerSettings
} catch {
    if (-not (Test-IsAdministrator)) {
        Write-Host "[INFO] Direct power update was denied; requesting administrator rights..."
        $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
        $Arguments = @(
            "-NoProfile"
            "-ExecutionPolicy", "Bypass"
            "-File", "`"$PSCommandPath`""
        )
        $Process = Start-Process -FilePath $PowerShell -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
        exit $Process.ExitCode
    }
    throw
}

$ActiveScheme = (& powercfg /getactivescheme | Out-String)
$SchemeMatch = [regex]::Match($ActiveScheme, "(?im)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
if (-not $SchemeMatch.Success) {
    throw "Could not determine the active Windows power scheme."
}

$SchemeGuid = $SchemeMatch.Groups[1].Value.ToLowerInvariant()
$PowerSettings = @(
    @{
        Name = "lid close"
        Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\$SchemeGuid\$ButtonSubgroupGuid\$LidActionGuid"
    },
    @{
        Name = "sleep idle"
        Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\$SchemeGuid\$SleepSubgroupGuid\$StandbyIdleGuid"
    },
    @{
        Name = "hibernate idle"
        Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\$SchemeGuid\$SleepSubgroupGuid\$HibernateIdleGuid"
    },
    @{
        Name = "unattended sleep"
        Path = "HKLM:\SYSTEM\CurrentControlSet\Control\Power\User\PowerSchemes\$SchemeGuid\$SleepSubgroupGuid\$UnattendedSleepGuid"
    }
)

foreach ($Setting in $PowerSettings) {
    try {
        $AcValue = Get-ItemPropertyValue -LiteralPath $Setting.Path -Name "ACSettingIndex" -ErrorAction Stop
        $DcValue = Get-ItemPropertyValue -LiteralPath $Setting.Path -Name "DCSettingIndex" -ErrorAction Stop
    } catch {
        throw "$($Setting.Name) could not be verified. Firmware or vendor software may override Windows power settings."
    }
    if ($AcValue -ne 0 -or $DcValue -ne 0) {
        throw "$($Setting.Name) verification failed: AC=$AcValue, DC=$DcValue (expected 0 for both)."
    }
}

Write-Host "[OK] Sleep, hibernation, and unattended sleep are disabled; lid close is set to Do nothing on AC and battery."
