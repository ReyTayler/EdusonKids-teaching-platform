# register_refresh_task.ps1 — задача Планировщика Windows «EdusonRefreshFromProd»:
# ежедневно в 06:30 пересобирает локальную БД Eduson из снимка прода
# (scripts/refresh_from_prod.sh через bash из Git for Windows).
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_refresh_task.ps1              # включить
#   powershell -ExecutionPolicy Bypass -File scripts\register_refresh_task.ps1 -Unregister  # выключить
#
# Задача работает от текущего пользователя и только пока он вошёл в систему
# (Interactive): ей нужен его SSH-ключ к серверу. Пропущенный запуск (компьютер был
# выключен в 06:30) выполняется при первой возможности. Лог — refresh.log в каталоге
# снимков (EDUSON_SNAPSHOT_DIR). Подробно: docs/eduson/refresh-from-prod.md.
#
# Файл — UTF-8 с BOM и CRLF: Windows PowerShell 5.1 без BOM читает кириллицу как ANSI.
[CmdletBinding()]
param(
    [switch]$Unregister,
    [string]$Time = '06:30'
)
$ErrorActionPreference = 'Stop'
$TaskName = 'EdusonRefreshFromProd'

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача $TaskName удалена."
    } else {
        Write-Host "Задачи $TaskName нет — удалять нечего."
    }
    return
}

$Script = Join-Path $PSScriptRoot 'refresh_from_prod.sh'
if (-not (Test-Path $Script)) { throw "Не найден $Script" }

# bash.exe из Git for Windows: рядом с git.exe (…\Git\cmd\git.exe → …\Git\bin\bash.exe),
# иначе стандартные места установки. bash из WSL (System32\bash.exe) не подходит:
# у него другая файловая система, venv и psql Windows ему не видны.
$Candidates = @()
$Git = Get-Command git.exe -ErrorAction SilentlyContinue
if ($Git) {
    $GitRoot = Split-Path (Split-Path $Git.Source -Parent) -Parent
    $Candidates += (Join-Path $GitRoot 'bin\bash.exe')
}
foreach ($Base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
    if ($Base) { $Candidates += (Join-Path $Base 'Git\bin\bash.exe') }
}
$Bash = $Candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $Bash) { throw 'Не найден bash.exe Git for Windows (установите Git for Windows).' }

# Прямые слеши: bash Git for Windows понимает C:/…, а обратные слеши съел бы как экранирование.
$ScriptArg = '"' + ($Script -replace '\\', '/') + '"'
$Action = New-ScheduledTaskAction -Execute $Bash -Argument $ScriptArg -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Principal $Principal -Description 'Eduson: ежедневная пересборка локальной БД из снимка прода старой системы' `
    -Force | Out-Null
Write-Host "Задача $TaskName зарегистрирована: ежедневно в $Time, bash: $Bash"
Write-Host "Запустить сейчас: Start-ScheduledTask -TaskName $TaskName"
