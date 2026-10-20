<#
.SYNOPSIS
  Arranca, para y consulta la oficina de agentes (habblaud) que alimenta la pestana Oficina de PST.

.DESCRIPTION
  habblaud es un proyecto aparte (MIT, https://github.com/marmottajr/habblaud) que vive FUERA del repo,
  por defecto como carpeta hermana: ..\..\habblaud respecto a este script. No se versiona dentro de PST
  porque trae su propio node_modules y se actualiza con su propio git pull.

  PST no lo necesita para arrancar: si habblaud esta apagado, el proxy devuelve 503 y la pestana Oficina
  lo pinta como "apagada". Esto es una comodidad, no una dependencia.

.PARAMETER Action
  start   levanta habblaud en segundo plano (no hace nada si ya escucha)
  stop    cierra el proceso que ocupe el puerto
  status  dice si responde, su version y que cuentas ve
  logs    muestra la salida reciente del arranque

.EXAMPLE
  .\Tools\habblaud.ps1 start
  .\Tools\habblaud.ps1 status
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'logs')]
    [string]$Action = 'status',

    [int]$Port = $(if ($env:HABBLAUD_PORT) { [int]$env:HABBLAUD_PORT } else { 4747 }),

    [string]$HabblaudDir
)

$ErrorActionPreference = 'Stop'
$logFile = Join-Path $env:TEMP 'habblaud-pst.log'

# La ruta por defecto se resuelve aqui y no en param(): en PowerShell 5.1 $PSScriptRoot llega
# vacio al evaluar los valores por defecto del bloque param(), y Split-Path revienta.
if (-not $HabblaudDir) {
    if ($env:HABBLAUD_DIR) {
        $HabblaudDir = $env:HABBLAUD_DIR
    } else {
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # <repo>\Tools
        $repoRoot  = Split-Path -Parent $scriptDir                      # <repo>
        $HabblaudDir = Join-Path (Split-Path -Parent $repoRoot) 'habblaud'
    }
}

function Test-Listening {
    param([int]$P)
    $null -ne (Get-NetTCPConnection -LocalPort $P -State Listen -ErrorAction SilentlyContinue)
}

function Get-Health {
    param([int]$P)
    try {
        return Invoke-RestMethod -Uri "http://127.0.0.1:$P/api/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

switch ($Action) {

    'start' {
        if (Test-Listening -P $Port) {
            Write-Host "habblaud ya escucha en el puerto $Port. Nada que hacer." -ForegroundColor Yellow
            break
        }
        if (-not (Test-Path (Join-Path $HabblaudDir 'package.json'))) {
            Write-Host "No encuentro habblaud en: $HabblaudDir" -ForegroundColor Red
            Write-Host "Clonalo con:  git clone https://github.com/marmottajr/habblaud.git `"$HabblaudDir`""
            Write-Host "Luego:        cd `"$HabblaudDir`"; npm install; npm run build"
            Write-Host "O indica otra ruta con la variable de entorno HABBLAUD_DIR."
            exit 1
        }
        if (-not (Test-Path (Join-Path $HabblaudDir 'dist\server\index.js'))) {
            Write-Host "habblaud esta sin construir. Ejecuta primero:  cd `"$HabblaudDir`"; npm install; npm run build" -ForegroundColor Red
            exit 1
        }

        Write-Host "Arrancando habblaud desde $HabblaudDir ..." -ForegroundColor Cyan
        # Se lanza con node directamente y no con npm: un npm.cmd intermedio deja un proceso
        # padre extra que luego complica el stop.
        $proc = Start-Process -FilePath 'node' `
                              -ArgumentList 'dist/server/index.js' `
                              -WorkingDirectory $HabblaudDir `
                              -RedirectStandardOutput $logFile `
                              -RedirectStandardError "$logFile.err" `
                              -WindowStyle Hidden `
                              -PassThru

        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Listening -P $Port) { break }
        }

        if (Test-Listening -P $Port) {
            $h = Get-Health -P $Port
            Write-Host "habblaud v$($h.version) escuchando en http://127.0.0.1:$Port (PID $($proc.Id))" -ForegroundColor Green
            Write-Host "La pestana Oficina de PST ya deberia pintarla."
        } else {
            Write-Host "No llego a escuchar en el puerto $Port. Mira el log:" -ForegroundColor Red
            Write-Host "  $logFile"
            if (Test-Path "$logFile.err") { Get-Content "$logFile.err" -Tail 15 }
            exit 1
        }
    }

    'stop' {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if (-not $conns) {
            Write-Host "Nada escuchando en el puerto $Port." -ForegroundColor Yellow
            break
        }
        foreach ($c in $conns) {
            Write-Host "Cerrando PID $($c.OwningProcess) (puerto $Port)" -ForegroundColor Cyan
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
        if (Test-Listening -P $Port) { Write-Host "Sigue ocupado." -ForegroundColor Red } else { Write-Host "habblaud detenido." -ForegroundColor Green }
    }

    'status' {
        if (-not (Test-Listening -P $Port)) {
            Write-Host "habblaud: APAGADO (nada escucha en $Port)" -ForegroundColor Yellow
            Write-Host "Para arrancarlo:  .\Tools\habblaud.ps1 start"
            break
        }
        $h = Get-Health -P $Port
        if (-not $h) {
            Write-Host "El puerto $Port esta ocupado pero /api/health no responde." -ForegroundColor Red
            break
        }
        Write-Host "habblaud: ENCENDIDO  v$($h.version)  en http://127.0.0.1:$Port" -ForegroundColor Green
        Write-Host ("  terminal: {0} | permisos: {1} | mensajes: {2}" -f $h.terminal, $h.permissions, $h.messages)
        foreach ($s in $h.sources) {
            Write-Host ("  fuente {0}: {1} sesiones ({2})" -f $s.label, $s.sessions, $s.path)
        }
        $mirror = Get-NetTCPConnection -LocalPort 4748 -State Listen -ErrorAction SilentlyContinue
        if ($mirror) { Write-Host "  espejo de PST en 4748: activo (PST esta arrancado)" -ForegroundColor Green }
        else { Write-Host "  espejo de PST en 4748: inactivo (arranca PST para la pestana Oficina)" -ForegroundColor Yellow }
    }

    'logs' {
        foreach ($f in @($logFile, "$logFile.err")) {
            if (Test-Path $f) {
                Write-Host "--- $f ---" -ForegroundColor Cyan
                Get-Content $f -Tail 25
            }
        }
    }
}
