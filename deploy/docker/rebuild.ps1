# Пересобрать и перезапустить staging Eduson, затем убрать старые образы.
#
#   powershell -ExecutionPolicy Bypass -File deploy\docker\rebuild.ps1
#
# Зачем очистка здесь: старые образы появляются ровно при пересборке. Новый
# образ забирает себе имя eduson-staging-app:latest, а прежний остаётся
# безымянным (<none>) и лежит на диске — сам Docker такие не удаляет никогда.
# «docker image prune» без -a удаляет ТОЛЬКО безымянные образы, на которые не
# ссылается ни один контейнер: рабочие образы и базовые (postgres, nginx) не трогает.
#
# Кэш сборки чистит сам Docker, когда он превышает потолок builder.gc.defaultKeepStorage
# в %USERPROFILE%\.docker\daemon.json (Docker Desktop → Settings → Docker Engine).

$ErrorActionPreference = 'Stop'
$docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
$compose = Join-Path $PSScriptRoot 'compose.staging.yml'

& $docker compose -f $compose up -d --build
if ($LASTEXITCODE -ne 0) { throw "Сборка или запуск не удались (код $LASTEXITCODE)" }

& $docker compose -f $compose ps -a

Write-Host ''
Write-Host 'Удаляю безымянные (старые) образы...'
& $docker image prune -f
& $docker system df
