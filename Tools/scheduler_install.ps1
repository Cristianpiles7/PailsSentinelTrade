<#
.SYNOPSIS
  Registra (o quita) la tarea nocturna de la flota de agentes en el Programador de tareas.

.DESCRIPTION
  La tarea lanza `.agents/scheduler.py`, que decide por si mismo que agentes corren y
  cuales no. El planificador NO lanza nada a ciegas: cada agente tiene una puerta que se
  evalua con SQL (gratis) y un presupuesto de cuota leido del uso en vivo que escribe el
  mod de habblaud. Una noche sin datos nuevos no gasta ni un token.

  La hora por defecto son las 03:00: fuera de las horas de trabajo del usuario, para que
  por la manana la ventana de 5h este limpia.

.PARAMETER Action
  install    registra la tarea (si existe, la reemplaza)
  uninstall  la quita
  status     muestra si existe y cuando corre
  run        la ejecuta ahora mismo, para probar

.EXAMPLE
  .\Tools\scheduler_install.ps1 install
  .\Tools\scheduler_install.ps1 install -Hora 02:30
  .\Tools\scheduler_install.ps1 status
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'uninstall', 'status', 'run')]
    [string]$Action = 'status',

    [string]$Hora = '03:00'
)

$ErrorActionPreference = 'Stop'
$TaskName = 'PST-Flota-Agentes'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # <repo>\Tools
$repoRoot  = Split-Path -Parent $scriptDir                      # <repo>
$scheduler = Join-Path $repoRoot '.agents\scheduler.py'
$logFile   = Join-Path $repoRoot '.agents\out\scheduler.log'

function Get-PythonPath {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if (-not $p) { throw "No encuentro 'python' en el PATH." }
    return $p.Source
}

switch ($Action) {

    'install' {
        if (-not (Test-Path $scheduler)) { throw "No encuentro $scheduler" }
        $python = Get-PythonPath

        # Se redirige la salida a un log: una tarea programada no tiene consola, y sin log
        # una noche sin resultados es indistinguible de una tarea rota.
        $cmd = "/c cd /d `"$repoRoot`" && `"$python`" -u `".agents\scheduler.py`" >> `"$logFile`" 2>&1"

        $accion  = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmd
        $trigger = New-ScheduledTaskTrigger -Daily -At $Hora
        # No despierta el equipo ni corre con bateria: la flota es util, no urgente.
        # -AllowStartIfOnBatteries hace falta explicitamente: sin el, el valor por defecto
        # es NO arrancar con bateria, y en un portatil la flota no correria nunca.
        $ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                                                -AllowStartIfOnBatteries `
                                                -DontStopIfGoingOnBatteries `
                                                -ExecutionTimeLimit (New-TimeSpan -Hours 2)

        # LogonType S4U: la tarea corre aunque la sesion este bloqueada o cerrada, y SIN
        # guardar la contrasena del usuario. Con el valor por defecto (Interactive) solo
        # corre con la sesion iniciada, asi que una noche con el equipo bloqueado se
        # perderia en silencio.
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Write-Host "La tarea ya existia: se reemplaza." -ForegroundColor Yellow
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }

        # S4U necesita elevacion. Si no la hay, se registra con el principal por defecto
        # (Interactive) en vez de dejar al usuario SIN tarea: el script la desregistra antes
        # de recrearla, asi que un fallo aqui sin repliegue borraria la programacion entera.
        $desc = 'Flota de agentes PST: evalua puertas y presupuesto de cuota, y lanza los agentes que proceda.'
        $registrada = $false
        try {
            Register-ScheduledTask -TaskName $TaskName -Action $accion -Trigger $trigger `
                                   -Settings $ajustes -Principal $principal `
                                   -Description $desc -ErrorAction Stop | Out-Null
            $registrada = $true
            Write-Host "Modo S4U: correra aunque la sesion este bloqueada o cerrada." -ForegroundColor Green
        } catch {
            Write-Host "Sin permisos para el modo S4U (hace falta ejecutar como administrador)." -ForegroundColor Yellow
            Register-ScheduledTask -TaskName $TaskName -Action $accion -Trigger $trigger `
                                   -Settings $ajustes -Description $desc | Out-Null
            $registrada = $true
            Write-Host "Registrada en modo Interactive: SOLO correra con tu sesion iniciada." -ForegroundColor Yellow
            Write-Host "  Para que corra tambien con la sesion cerrada, relanza este script como administrador."
        }
        if (-not $registrada) { throw 'No se pudo registrar la tarea.' }

        Write-Host "Tarea '$TaskName' registrada para las $Hora cada dia." -ForegroundColor Green
        Write-Host "  python : $python"
        Write-Host "  log    : $logFile"
        Write-Host ""
        Write-Host "Pruebala sin esperar a manana:  .\Tools\scheduler_install.ps1 run"
    }

    'uninstall' {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Tarea '$TaskName' eliminada." -ForegroundColor Green
        } else {
            Write-Host "No estaba registrada." -ForegroundColor Yellow
        }
    }

    'status' {
        $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $t) {
            Write-Host "Tarea '$TaskName': NO registrada" -ForegroundColor Yellow
            Write-Host "Para registrarla:  .\Tools\scheduler_install.ps1 install"
            break
        }
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Tarea '$TaskName': registrada, estado $($t.State)" -ForegroundColor Green
        Write-Host "  ultima ejecucion : $($info.LastRunTime)  (resultado $($info.LastTaskResult))"
        Write-Host "  proxima          : $($info.NextRunTime)"
        if (Test-Path $logFile) {
            Write-Host ""
            Write-Host "--- ultimas lineas del log ---"
            Get-Content $logFile -Tail 15 -Encoding UTF8
        }
    }

    'run' {
        Write-Host "Ejecutando el planificador ahora (en seco primero)..." -ForegroundColor Cyan
        Push-Location $repoRoot
        try {
            $env:PYTHONIOENCODING = 'utf-8'
            & (Get-PythonPath) -u '.agents\scheduler.py' --dry-run
            Write-Host ""
            Write-Host "Eso es lo que HARIA. Para ejecutarlo de verdad:" -ForegroundColor Yellow
            Write-Host "  python .agents\scheduler.py"
        } finally { Pop-Location }
    }
}
