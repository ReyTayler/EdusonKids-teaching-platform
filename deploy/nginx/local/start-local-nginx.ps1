<#
.SYNOPSIS
    Запускает локальный nginx Eduson Kids перед Django runserver (Windows, без Docker).

.DESCRIPTION
    nginx раздаёт статику фронта и проксирует /api + /health на runserver :8100.
    Конфиг: deploy/nginx/local/nginx.conf (общий сниппет статики с продом).

    Порты не пересекаются со стендом KOTOKOD на той же машине (у него nginx :8080,
    runserver :8000): Eduson — nginx :8180, CSP-сток :19976, runserver :8100.
    Рабочий каталог (.runtime/: pid, логи, temp) у каждого стенда свой, поэтому
    -Stop этого скрипта останавливает только nginx Eduson.

    Перед запуском подними Django отдельно:
        cd journal_django
        ../.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8100

    Затем:  ./deploy/nginx/local/start-local-nginx.ps1
    Открыть: http://localhost:8180/

.PARAMETER Stop
    Остановить nginx (nginx -s stop).

.PARAMETER Reload
    Перечитать конфиг без остановки (nginx -s reload) — например после правки сниппета.

.PARAMETER Test
    Только проверить синтаксис конфига (nginx -t) и выйти.

.PARAMETER NginxExe
    Явный путь к nginx.exe (если автопоиск не нашёл).

.PARAMETER Prefix
    Рабочий каталог nginx (-p): туда пишутся logs/ и temp/. По умолчанию
    .runtime/ рядом со скриптом — создаётся сам.

.PARAMETER AppRoot
    Каталог journal_django/. По умолчанию вычисляется от расположения скрипта
    (deploy/nginx/local → корень репозитория → journal_django). Пригодится,
    если конфиг запускают против кода, лежащего в другом месте.

.NOTES
    Установка nginx (без админ-прав): официальный zip с https://nginx.org/en/download.html,
    распаковать в %USERPROFILE%\nginx\ (скрипт сам найдёт nginx-*\nginx.exe).
    Альтернатива: `winget install nginxinc.nginx`.
    Один nginx.exe годится для обоих стендов: экземпляры различаются префиксом
    (-p) и конфигом (-c), а не бинарём.
    Пути машины в nginx.conf НЕ хардкодятся: скрипт пишет их в .runtime/paths.conf
    рядом с конфигом, а тот подключает файл относительным include. Поэтому один и
    тот же nginx.conf работает на любой машине без правок.
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Reload,
    [switch]$Test,
    [string]$NginxExe,
    [string]$Prefix,
    [string]$AppRoot
)

$ErrorActionPreference = 'Stop'

# Порт берём одной константой: он же в nginx.conf (listen) и в сообщении о запуске.
$ListenUrl = 'http://localhost:8180/'

# Абсолютный путь к нашему конфигу (рядом со скриптом).
$ConfigPath = Join-Path $PSScriptRoot 'nginx.conf'
if (-not (Test-Path $ConfigPath)) {
    throw "Не найден конфиг: $ConfigPath"
}

# Найти nginx.exe: явный параметр → PATH → %USERPROFILE%\nginx\nginx-*\ → C:\nginx\.
if (-not $NginxExe) {
    $cmd = Get-Command nginx -ErrorAction SilentlyContinue
    if ($cmd) {
        $NginxExe = $cmd.Source
    } else {
        $candidates = @(
            "$env:USERPROFILE\nginx",
            'C:\nginx'
        ) | Where-Object { Test-Path $_ } |
            ForEach-Object { Get-ChildItem -Path $_ -Recurse -Filter nginx.exe -ErrorAction SilentlyContinue } |
            Sort-Object FullName -Descending
        if ($candidates) { $NginxExe = $candidates[0].FullName }
    }
}
if (-not $NginxExe -or -not (Test-Path $NginxExe)) {
    throw "nginx.exe не найден. Скачай zip с https://nginx.org/en/download.html в %USERPROFILE%\nginx\, или укажи -NginxExe <путь>."
}

# Prefix (-p) — рабочий каталог nginx: там он держит logs/ (с nginx.pid) и temp/.
#
# Каталог САМОЙ установки для этого не годится: winget кладёт в
# ...\WinGet\Links только шим-exe, без logs/ и temp/, и nginx падает ещё до
# чтения конфига («could not open error log file»). К тому же общий с KOTOKOD
# каталог означал бы общий nginx.pid — и -Stop одного стенда гасил бы другой.
# Поэтому по умолчанию работаем в .runtime/ рядом со скриптом.
if (-not $Prefix) {
    $Prefix = Join-Path $PSScriptRoot '.runtime'
}
foreach ($sub in @('', 'logs', 'temp')) {
    $dir = if ($sub) { Join-Path $Prefix $sub } else { $Prefix }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

# --- Пути этой машины -------------------------------------------------------
#
# Скрипт лежит в deploy/nginx/local/, значит корень репозитория — тремя
# уровнями выше. Отсюда и journal_django/, и сниппет статики, общий с продом.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $AppRoot) { $AppRoot = Join-Path $RepoRoot 'journal_django' }
if (-not (Test-Path $AppRoot)) {
    throw "Не найден каталог journal_django: $AppRoot. Укажи -AppRoot <путь>."
}
$StaticSnippet = Join-Path $RepoRoot 'deploy\nginx\snippets\journal-static.conf'
if (-not (Test-Path $StaticSnippet)) {
    throw "Не найден сниппет статики: $StaticSnippet"
}

# nginx на Windows понимает только форвард-слеши в путях конфига.
# -replace работает регулярным выражением, поэтому обратный слеш экранирован.
$slash = { param($p) ($p -replace '\\', '/') }
# paths.conf кладётся ВСЕГДА в .runtime/ рядом с конфигом, независимо от
# -Prefix: путь к нему записан в самом nginx.conf относительным include, а тот
# резолвится от каталога конфига. -Prefix управляет только logs/ и temp/.
$PathsDir = Join-Path $PSScriptRoot '.runtime'
if (-not (Test-Path $PathsDir)) { New-Item -ItemType Directory -Path $PathsDir -Force | Out-Null }
$PathsConf = Join-Path $PathsDir 'paths.conf'
$PathsBody = @"
# Пути ЭТОЙ машины. Файл создаётся start-local-nginx.ps1 при каждом запуске —
# править его бессмысленно, изменения затрутся. Он лежит в .runtime/ и в git не
# попадает: у каждой машины путь свой, а nginx.conf один на всех.
set `$app_root $(& $slash $AppRoot);
include $(& $slash $StaticSnippet);
"@
# UTF-8 БЕЗ BOM, и записью через .NET, а не Set-Content -Encoding UTF8:
# в Windows PowerShell 5.1 этот ключ ставит BOM, а nginx его не пропускает —
# три байта приклеиваются к первому '#', комментарий перестаёт быть
# комментарием, и разбор падает на «unknown directive» уже в paths.conf.
[System.IO.File]::WriteAllText(
    $PathsConf, $PathsBody + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

Write-Host "nginx:  $NginxExe"
Write-Host "prefix: $Prefix"
Write-Host "код:    $AppRoot"
Write-Host "config: $ConfigPath"

# -c обязателен и здесь: без него nginx перед отправкой сигнала читает конфиг
# по умолчанию (<prefix>/conf/nginx.conf), не находит его и падает, ничего не
# остановив. Код возврата проверяем: иначе скрипт печатал бы «nginx
# остановлен», пока сервер продолжает работать.
if ($Stop) {
    & $NginxExe -p "$Prefix" -c "$ConfigPath" -s stop
    if ($LASTEXITCODE -ne 0) { throw "nginx -s stop завершился с ошибкой ($LASTEXITCODE)." }
    Write-Host "nginx Eduson остановлен." -ForegroundColor Green
    return
}

if ($Reload) {
    & $NginxExe -p "$Prefix" -c "$ConfigPath" -s reload
    if ($LASTEXITCODE -ne 0) { throw "nginx -s reload завершился с ошибкой ($LASTEXITCODE)." }
    Write-Host "Конфиг перечитан." -ForegroundColor Green
    return
}

# Всегда сначала проверяем синтаксис.
& $NginxExe -p "$Prefix" -c "$ConfigPath" -t
if ($LASTEXITCODE -ne 0) {
    throw "nginx -t завершился с ошибкой ($LASTEXITCODE). Конфиг не запущен."
}

if ($Test) {
    Write-Host "Синтаксис ОК (только проверка, nginx не запущен)." -ForegroundColor Green
    return
}

# Запуск отдельным процессом, а не в текущей консоли.
#
# Сам nginx.exe на Windows уходит в фон сразу, но winget ставит не бинарь, а
# шим в ...\WinGet\Links — тот ЖДЁТ дочерний процесс и не возвращает управление
# консоли до остановки сервера. Start-Process отвязывает запуск от способа
# установки: скрипт завершается одинаково и с шимом, и с распакованным zip.
# pid-файл прошлого запуска убираем заранее: иначе проверка ниже прочитает
# его и решит, что всё поднялось, ещё до того как новый процесс запишет свой.
$StalePid = Join-Path $Prefix 'logs\nginx.pid'
if (Test-Path $StalePid) { Remove-Item $StalePid -Force }

Start-Process -FilePath $NginxExe `
              -ArgumentList @('-p', $Prefix, '-c', $ConfigPath) `
              -WindowStyle Hidden

# Проверяем НАШ процесс по pid-файлу, а не «отвечает ли :8180».
#
# Разница принципиальная: если порт уже занят чужим nginx (забытый запуск),
# проба порта радостно скажет «работает», и правки конфига будут молча уходить
# в никуда, потому что отвечает чужой процесс со своим старым конфигом. Наш
# при этом не поднялся вовсе: bind() провалился, и он вышел, оставив причину
# в error.log.
$PidFile = Join-Path $Prefix 'logs\nginx.pid'
$deadline = (Get-Date).AddSeconds(5)
$running = $null
while (-not $running -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 200
    if (Test-Path $PidFile) {
        $nginxPid = (Get-Content $PidFile -Raw).Trim()
        $running = Get-Process -Id $nginxPid -ErrorAction SilentlyContinue
    }
}

if ($running) {
    Write-Host "nginx Eduson запущен (pid $($running.Id)) → $ListenUrl  (остановка: -Stop, перезагрузка: -Reload)" -ForegroundColor Green
} else {
    $log = Join-Path $Prefix 'logs\error.log'
    $tail = if (Test-Path $log) { (Get-Content $log -Tail 5) -join "`n" } else { '(error.log пуст)' }
    throw "nginx не поднялся. Последние строки $log`:`n$tail"
}
