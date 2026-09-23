param(
    [switch]$Run,
    [switch]$SkipModelPull,
    [switch]$ForceDependencies,
    [switch]$NoMonitor,
    [switch]$WithMonitor,
    [string]$PythonPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

function Write-Step {
    param([string]$Message)
    Write-Host "[AUTO] $Message" -ForegroundColor Cyan
}

function Assert-LastExitCode {
    param([string]$Action)
    if ($LASTEXITCODE -ne 0) {
        throw "$Action fallo con codigo $LASTEXITCODE."
    }
}

function Test-CompatiblePython {
    param([string]$CandidatePath)

    if (-not $CandidatePath -or -not (Test-Path $CandidatePath)) {
        return $false
    }

    $probeFile = Join-Path $env:TEMP ("jarvis_py_probe_" + (New-Guid).Guid + ".py")
    $probeOutput = $null
    $originalPythonHome = $env:PYTHONHOME
    $originalPythonPath = $env:PYTHONPATH
    $candidateDir = Split-Path -Parent $CandidatePath

    $pushed = $false
    try {
        @"
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
"@ | Set-Content -Path $probeFile -Encoding ascii

        $env:PYTHONHOME = $null
        $env:PYTHONPATH = $null

        Push-Location $candidateDir
        $pushed = $true
        $probeOutput = & $CandidatePath $probeFile 2>$null
        $exitCode = $LASTEXITCODE

        if ($exitCode -ne 0 -or -not $probeOutput) {
            return $false
        }

        $v = ($probeOutput -join "") -replace '^\s+|\s+$', ''
        return ($v -eq "3.10" -or $v -eq "3.11")
    } catch {
        return $false
    } finally {
        if ($pushed) {
            Pop-Location
        }
        if (Test-Path $probeFile) {
            Remove-Item -Path $probeFile -Force -ErrorAction SilentlyContinue
        }
        if ($null -ne $originalPythonHome) {
            $env:PYTHONHOME = $originalPythonHome
        } else {
            Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        }
        if ($null -ne $originalPythonPath) {
            $env:PYTHONPATH = $originalPythonPath
        } else {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        }
    }
}

function Get-Python311Path {
    if ($PythonPath) {
        $resolved = Resolve-Path -Path $PythonPath -ErrorAction SilentlyContinue
        if ($resolved -and (Test-CompatiblePython -CandidatePath $resolved.Path)) {
            return $resolved.Path
        }
        throw "El valor de -PythonPath no es valido o no es Python 3.10/3.11: $PythonPath"
    }

    if ($env:PYTHON311_BIN -and (Test-CompatiblePython -CandidatePath $env:PYTHON311_BIN)) {
        return $env:PYTHON311_BIN
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd -and (Test-CompatiblePython -CandidatePath $pythonCmd.Source)) {
        return $pythonCmd.Source
    }

    $python311Cmd = Get-Command python3.11 -ErrorAction SilentlyContinue
    if ($python311Cmd -and (Test-CompatiblePython -CandidatePath $python311Cmd.Source)) {
        return $python311Cmd.Source
    }

    $commonCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python311\python.exe"),
        (Join-Path $env:ProgramFiles "Python311-32\python.exe")
    )
    foreach ($candidate in $commonCandidates) {
        if (Test-CompatiblePython -CandidatePath $candidate) {
            return $candidate
        }
    }

    $localPythonRoot = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $localPythonRoot) {
        $found = Get-ChildItem -Path $localPythonRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^Python3(10|11)$" } |
            Sort-Object Name -Descending
        foreach ($folder in $found) {
            $candidate = Join-Path $folder.FullName "python.exe"
            if (Test-CompatiblePython -CandidatePath $candidate) {
                return $candidate
            }
        }
    }

    return $null
}

function Install-Python311Direct {
    $installerUrl = $env:PYTHON311_INSTALLER_URL
    if (-not $installerUrl) {
        $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    }

    $installerFile = Join-Path $env:TEMP ("python-3.11.9-amd64-" + (New-Guid).Guid + ".exe")
    Write-Step "Descargando instalador de Python 3.11..."
    Invoke-WebRequest -Uri $installerUrl -OutFile $installerFile -UseBasicParsing

    Write-Step "Instalando Python 3.11 (silencioso)..."
    $proc = Start-Process `
        -FilePath $installerFile `
        -ArgumentList @(
            "/quiet",
            "InstallAllUsers=0",
            "PrependPath=1",
            "Include_pip=1",
            "Include_test=0",
            "Shortcuts=0"
        ) `
        -PassThru `
        -Wait

    for ($i = 0; $i -lt 5; $i++) {
        Remove-Item -Path $installerFile -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $installerFile)) {
            break
        }
        Start-Sleep -Milliseconds 400
    }
    if ($proc.ExitCode -ne 0) {
        throw "Instalacion silenciosa de Python fallo con codigo $($proc.ExitCode)."
    }
}

function Ensure-Python311 {
    $pythonPath = Get-Python311Path
    if ($pythonPath) {
        Write-Step "Python compatible detectado: $pythonPath"
        return $pythonPath
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Instalando Python 3.11 con winget..."
        & winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
        Assert-LastExitCode "Instalacion de Python 3.11"
    } else {
        Write-Step "winget no disponible. Intentando instalacion directa de Python 3.11..."
        Install-Python311Direct
    }

    Start-Sleep -Seconds 2
    $pythonPath = Get-Python311Path
    if (-not $pythonPath) {
        throw (
            "No se pudo detectar Python 3.10/3.11 tras la instalacion. " +
            "Instalalo manualmente y relanza con -PythonPath `"<ruta>\python.exe`"."
        )
    }

    Write-Step "Python instalado y detectado correctamente."
    return $pythonPath
}

function Ensure-Venv {
    param([string]$Python311Path)

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Step "Creando entorno virtual .venv..."
        & $Python311Path -m venv (Join-Path $ProjectRoot ".venv")
        Assert-LastExitCode "Creacion de .venv"
    } else {
        Write-Step "Entorno virtual detectado."
    }

    return $venvPython
}

function Get-OllamaPath {
    $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCmd) {
        return $ollamaCmd.Source
    }

    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe")
    }
    $candidates += "C:\Program Files\Ollama\ollama.exe"

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

function Ensure-OllamaInstalled {
    $ollamaPath = Get-OllamaPath
    if ($ollamaPath) {
        Write-Step "Ollama detectado: $ollamaPath"
        return $ollamaPath
    }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Step "Instalando Ollama con winget..."
        & winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
        Assert-LastExitCode "Instalacion de Ollama"
    } else {
        $ollamaUrl = $env:OLLAMA_INSTALLER_URL
        if (-not $ollamaUrl) {
            $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
        }
        $installerFile = Join-Path $env:TEMP ("OllamaSetup-" + (New-Guid).Guid + ".exe")

        Write-Step "winget no disponible. Descargando instalador de Ollama..."
        Invoke-WebRequest -Uri $ollamaUrl -OutFile $installerFile -UseBasicParsing

        Write-Step "Instalando Ollama..."
        $proc = Start-Process -FilePath $installerFile -ArgumentList "/S" -PassThru -Wait
        for ($i = 0; $i -lt 5; $i++) {
            Remove-Item -Path $installerFile -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path $installerFile)) {
                break
            }
            Start-Sleep -Milliseconds 400
        }
        if ($proc.ExitCode -ne 0) {
            throw "Instalacion de Ollama fallo con codigo $($proc.ExitCode)."
        }
    }

    Start-Sleep -Seconds 2
    $ollamaPath = Get-OllamaPath
    if (-not $ollamaPath) {
        throw "Ollama se instalo pero no fue detectado. Reinicia sesion y vuelve a ejecutar."
    }
    Write-Step "Ollama instalado correctamente."
    return $ollamaPath
}

function Test-OllamaApi {
    # Invoke-WebRequest es un cmdlet permitido en ConstrainedLanguage mode.
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:11434/api/tags" -Method Get `
            -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-OllamaProcess {
    param([string]$OllamaPath)

    # Start-Process es un cmdlet permitido en ConstrainedLanguage mode.
    try {
        $cmdArgs = "/c start /min `"`"  `"$OllamaPath`" serve"
        Start-Process -FilePath "cmd.exe" -ArgumentList $cmdArgs `
            -WindowStyle Hidden -ErrorAction Stop | Out-Null
        return $true
    } catch {}

    # Fallback: Start-Process estandar
    try {
        Start-Process -FilePath $OllamaPath -ArgumentList "serve" `
            -WindowStyle Minimized -ErrorAction Stop | Out-Null
        return $true
    } catch {}

    # Ultimo recurso: Invoke-Expression en background
    try {
        $job = Start-Job -ScriptBlock { param($p) & $p serve } -ArgumentList $OllamaPath
        [void]$job
        return $true
    } catch {}

    return $false
}

function Ensure-OllamaRunning {
    param([string]$OllamaPath)

    if (Test-OllamaApi) {
        Write-Step "API de Ollama ya activa."
        return
    }

    Write-Step "Iniciando Ollama (ollama serve)..."
    $launched = Start-OllamaProcess -OllamaPath $OllamaPath

    if (-not $launched) {
        Write-Warning "No se pudo lanzar Ollama automaticamente. Intentalo manualmente: ollama serve"
    }

    # Espera hasta 90 s con retroceso progresivo
    $maxWait  = 90
    $elapsed  = 0
    $interval = 2
    while ($elapsed -lt $maxWait) {
        $waitSeconds = $interval
        if ($waitSeconds -gt ($maxWait - $elapsed)) { $waitSeconds = $maxWait - $elapsed }
        Start-Sleep -Seconds $waitSeconds
        $elapsed += $waitSeconds
        if (Test-OllamaApi) {
            Write-Step "Ollama listo (tras $elapsed s)."
            return
        }
        # Cada 20 s incrementar el intervalo de sondeo para no saturar
        if ($elapsed % 20 -eq 0 -and $interval -lt 5) { $interval++ }
        Write-Step "Esperando Ollama... ($elapsed/$maxWait s)"
    }

    # Diagnóstico final
    $ollamaProcs = @(Get-Process -Name "ollama" -ErrorAction SilentlyContinue)
    if ($ollamaProcs.Count -gt 0) {
        throw ("Ollama esta en ejecucion (PID $($ollamaProcs[0].Id)) pero no responde en " +
               "http://localhost:11434. Puede haber un firewall bloqueando el puerto 11434, " +
               "o Ollama esta enlazado a otra interfaz. Comprueba la configuracion de Ollama.")
    } else {
        throw ("Ollama no arranco correctamente. Abre una terminal separada y ejecuta: " +
               "ollama serve --- luego vuelve a ejecutar run_jarvis.bat")
    }
}

function Get-FileSha256 {
    param([string]$FilePath)
    # Get-FileHash is a native cmdlet and works in ConstrainedLanguage mode.
    return (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash
}

function Install-PythonDependencies {
    param([string]$VenvPython)

    $requirementsPath = Join-Path $ProjectRoot "requirements.txt"
    $requirementsHash = Get-FileSha256 -FilePath $requirementsPath
    $depsStampFile = Join-Path $ProjectRoot ".venv\.requirements.sha256"

    if (-not $ForceDependencies -and (Test-Path $depsStampFile)) {
        $savedHash = (Get-Content -Path $depsStampFile -Raw).Trim()
        if ($savedHash -eq $requirementsHash) {
            Write-Step "Dependencias ya sincronizadas con requirements.txt."
            return
        }
    }

    Write-Step "Actualizando pip/setuptools/wheel..."
    & $VenvPython -m pip install --upgrade pip setuptools wheel --disable-pip-version-check
    Assert-LastExitCode "Actualizacion de herramientas Python"

    Write-Step "Instalando dependencias de requirements.txt..."
    & $VenvPython -m pip install --prefer-binary -r $requirementsPath --disable-pip-version-check
    Assert-LastExitCode "Instalacion de dependencias"

    Set-Content -Path $depsStampFile -Value $requirementsHash -Encoding ascii
}

# ---------------------------------------------------------------------------
# ffmpeg (necesario para convertir m4a/mp3/ogg -> WAV)
# ---------------------------------------------------------------------------

function Get-FfmpegPath {
    $inPath = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($inPath) { return $inPath.Source }

    $localFfmpeg = Join-Path $ProjectRoot ".tools\ffmpeg\bin\ffmpeg.exe"
    if (Test-Path $localFfmpeg) { return $localFfmpeg }

    return $null
}

function Install-FfmpegWinget {
    Write-Step "Instalando ffmpeg con winget..."
    try {
        # Invocar winget como proceso nativo; capturar resultado sin lanzar excepcion
        $proc = Start-Process `
            -FilePath "winget" `
            -ArgumentList @(
                "install", "--id", "Gyan.FFmpeg",
                "--exact", "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements"
            ) `
            -PassThru -Wait -NoNewWindow `
            -ErrorAction Stop

        if ($proc.ExitCode -ne 0) {
            Write-Step "winget termino con codigo $($proc.ExitCode) (puede ser 'ya instalado'). Verificando..."
        }

        # Refrescar PATH de la sesion actual para ver el nuevo binario
        $machinePath = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name Path -ErrorAction SilentlyContinue).Path
        $userPath    = (Get-ItemProperty -Path "HKCU:\Environment" -Name Path -ErrorAction SilentlyContinue).Path
        if ($machinePath -or $userPath) {
            $env:Path = "$machinePath;$userPath"
        }
        return $true
    } catch {
        Write-Step "winget no disponible o fallo: $($_.Exception.Message)"
        return $false
    }
}

function Install-FfmpegDirect {
    # Descarga el zip de la build esencial de BtbN (GitHub Releases)
    $ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
    $tmpZip    = Join-Path $env:TEMP ("ffmpeg_dl_" + (New-Guid).Guid + ".zip")
    $toolsDir  = Join-Path $ProjectRoot ".tools"
    $ffmpegDir = Join-Path $toolsDir "ffmpeg"

    try {
        Write-Step "Descargando ffmpeg (esto puede tardar un momento)..."
        Invoke-WebRequest -Uri $ffmpegUrl -OutFile $tmpZip -UseBasicParsing -ErrorAction Stop

        Write-Step "Descomprimiendo ffmpeg en .tools/ffmpeg ..."
        if (Test-Path $ffmpegDir) { Remove-Item $ffmpegDir -Recurse -Force -ErrorAction SilentlyContinue }
        Expand-Archive -Path $tmpZip -DestinationPath $toolsDir -Force -ErrorAction Stop

        # El zip contiene una subcarpeta con nombre largo; la renombramos a 'ffmpeg'
        $extracted = Get-ChildItem -Path $toolsDir -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "ffmpeg-*" } |
            Select-Object -First 1
        if ($extracted -and $extracted.FullName -ne $ffmpegDir) {
            Rename-Item -Path $extracted.FullName -NewName "ffmpeg" -Force -ErrorAction Stop
        }

        # Agregar al PATH de la sesion actual
        $binPath = Join-Path $ffmpegDir "bin"
        $env:Path = "$binPath;$env:Path"
        Write-Step "ffmpeg instalado localmente en .tools/ffmpeg/bin"
        return $true
    } catch {
        Write-Step "Descarga directa de ffmpeg fallo: $($_.Exception.Message)"
        return $false
    } finally {
        Remove-Item -Path $tmpZip -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-Ffmpeg {
    $ffmpegPath = Get-FfmpegPath
    if ($ffmpegPath) {
        Write-Step "ffmpeg detectado: $ffmpegPath"
        return
    }

    Write-Step "ffmpeg no encontrado. Instalando automaticamente..."

    # --- Intento 1: winget (solo si el ejecutable existe y es invocable) ----
    $wingetExe = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($wingetExe) {
        Install-FfmpegWinget | Out-Null
    }

    # --- Comprobacion tras winget ------------------------------------------
    $ffmpegPath = Get-FfmpegPath
    if ($ffmpegPath) {
        Write-Step "ffmpeg listo (via winget): $ffmpegPath"
        return
    }

    # --- Intento 2: descarga directa (siempre como fallback) ---------------
    Install-FfmpegDirect | Out-Null
    $ffmpegPath = Get-FfmpegPath

    if ($ffmpegPath) {
        Write-Step "ffmpeg listo (descarga directa): $ffmpegPath"
    } else {
        Write-Warning ("ffmpeg no pudo instalarse automaticamente. " +
            "La conversion de formatos de audio no estara disponible. " +
            "Instala ffmpeg manualmente desde https://ffmpeg.org/download.html")
    }
}

# ---------------------------------------------------------------------------
# Normalizacion de muestras de voz
# ---------------------------------------------------------------------------

function Invoke-NormalizeVoiceSamples {
    param([string]$VenvPython)

    $scriptPath  = Join-Path $ProjectRoot "legacy\normalize_voice_samples.py"
    $samplesDir  = Join-Path $ProjectRoot "voice_samples"

    if (-not (Test-Path $scriptPath)) {
        Write-Warning "normalize_voice_samples.py no encontrado. Saltando normalizacion."
        return
    }
    if (-not (Test-Path $samplesDir)) {
        Write-Warning "voice_samples/ no existe. Saltando normalizacion."
        return
    }

    # Comprobar si hay algo que normalizar (formatos no-WAV o nombres no canonicos)
    $audioFormats = @('*.mp3','*.m4a','*.aac','*.ogg','*.flac','*.opus','*.wma','*.aiff','*.aif')
    $nonWav = @()
    foreach ($pattern in $audioFormats) {
        $nonWav += @(Get-ChildItem -Path $samplesDir -Filter $pattern -File -ErrorAction SilentlyContinue)
    }
    $wavFiles  = @(Get-ChildItem -Path $samplesDir -Filter '*.wav' -File -ErrorAction SilentlyContinue)
    $badNames  = @($wavFiles | Where-Object { $_.Name -notmatch '^sample\d+\.wav$' })

    if ($nonWav.Count -eq 0 -and $badNames.Count -eq 0) {
        Write-Step "Muestras de voz ya normalizadas. Sin cambios necesarios."
        return
    }

    Write-Step "Normalizando muestras de voz (conversion a WAV + renombrado)..."
    & $VenvPython $scriptPath --dir $samplesDir
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "La normalizacion de muestras termino con errores (codigo $LASTEXITCODE). Revisa los mensajes anteriores."
    } else {
        Write-Step "Normalizacion de muestras completada."
    }
}

function Ensure-OllamaModel {
    param(
        [string]$OllamaPath,
        [string]$ModelName
    )

    if ($SkipModelPull) {
        Write-Step "Salto de descarga del modelo solicitado por parametro."
        return
    }

    try {
        $tagsResponse = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 4
        $installedNames = @($tagsResponse.models | ForEach-Object { $_.name })
        if ($installedNames -contains $ModelName) {
            Write-Step "Modelo '$ModelName' ya disponible."
            return
        }
    } catch {
        # If tag query fails, fallback to pull command.
    }

    Write-Step "Asegurando modelo Ollama '$ModelName'..."
    & $OllamaPath pull $ModelName
    Assert-LastExitCode "Descarga de modelo $ModelName"
}

function Validate-VoiceSamples {
    $voiceDir = Join-Path $ProjectRoot "voice_samples"
    if (-not (Test-Path $voiceDir)) {
        throw "No existe la carpeta voice_samples."
    }

    $wavCount = @(Get-ChildItem -Path $voiceDir -Filter 'sample*.wav' -File -ErrorAction SilentlyContinue).Count
    if ($wavCount -lt 1) {
        $message = "No hay archivos WAV en voice_samples. Agrega archivos de audio (wav, mp3, m4a...) y vuelve a ejecutar."
        if ($Run) {
            throw $message
        }
        Write-Warning $message
        return
    }

    Write-Step "Muestras de voz listas: $wavCount archivo(s) WAV canonical(es)."
}

function Stop-OrphanJarvisPython {
    $rootNormalized = $ProjectRoot.ToLowerInvariant().Replace("/", "\")
    $targets = @("main.py", "monitor_status.py", "-m jarvis")

    try {
        $pyProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
                $_.CommandLine
            }

        foreach ($proc in $pyProcs) {
            $cmd = $proc.CommandLine.ToLowerInvariant().Replace("/", "\")
            if ($cmd -notlike "*$rootNormalized*") {
                continue
            }

            $isTarget = $false
            foreach ($target in $targets) {
                if ($cmd -like "*$target*") {
                    $isTarget = $true
                    break
                }
            }
            if (-not $isTarget) {
                continue
            }

            if ($proc.ProcessId -eq $PID) {
                continue
            }

            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Step "Proceso huerfano cerrado: PID $($proc.ProcessId)"
        }
    } catch {
        Write-Warning "No se pudo limpiar procesos huerfanos de Jarvis: $($_.Exception.Message)"
    }
}

function Start-JarvisMonitor {
    param(
        [string]$VenvPython,
        [string]$LogPath
    )

    if ($NoMonitor -or -not $WithMonitor) {
        if ($NoMonitor) {
            Write-Step "Monitor desactivado por parametro (-NoMonitor)."
        } else {
            Write-Step "Monitor desactivado por defecto. Usa -WithMonitor para abrir ventana adicional."
        }
        return $null
    }

    $monitorScript = Join-Path $ProjectRoot "legacy\monitor_status.py"
    if (-not (Test-Path $monitorScript)) {
        Write-Warning "monitor_status.py no encontrado. Continuando sin monitor."
        return $null
    }

    try {
        $args = @(
            $monitorScript,
            "--project-root", $ProjectRoot,
            "--log-path", $LogPath,
            "--refresh-seconds", "2"
        )

        $monitorProcess = Start-Process `
            -FilePath $VenvPython `
            -ArgumentList $args `
            -PassThru `
            -WindowStyle Normal

        Write-Step "Monitor iniciado en ventana separada (PID $($monitorProcess.Id))."
        return $monitorProcess
    } catch {
        Write-Warning "No se pudo iniciar monitor: $($_.Exception.Message)"
        return $null
    }
}

function Stop-JarvisMonitor {
    param($MonitorProcess)

    if ($null -eq $MonitorProcess) {
        return
    }

    try {
        if (-not $MonitorProcess.HasExited) {
            Stop-Process -Id $MonitorProcess.Id -Force -ErrorAction SilentlyContinue
        }
    } catch {
        # Monitor best-effort cleanup.
    }
}

try {
    Write-Step "Inicio de bootstrap automatico para Jarvis..."
    $env:COQUI_TOS_AGREED = "1"
    Write-Step "COQUI_TOS_AGREED=1 configurado para evitar prompts interactivos de XTTS."

    $python311Path = Ensure-Python311
    $venvPython = Ensure-Venv -Python311Path $python311Path
    Install-PythonDependencies -VenvPython $venvPython

    $ollamaPath = Ensure-OllamaInstalled
    Ensure-OllamaRunning -OllamaPath $ollamaPath
    Ensure-OllamaModel -OllamaPath $ollamaPath -ModelName "mistral:7b-instruct"

    Ensure-Ffmpeg
    Invoke-NormalizeVoiceSamples -VenvPython $venvPython
    Validate-VoiceSamples

    if ($Run) {
        Stop-OrphanJarvisPython

        $logDir = Join-Path $ProjectRoot "logs"
        if (-not (Test-Path $logDir)) {
            New-Item -Path $logDir -ItemType Directory -Force | Out-Null
        }

        $jarvisLog = Join-Path $logDir "jarvis.log"
        if (Test-Path $jarvisLog) {
            Remove-Item -Path $jarvisLog -Force -ErrorAction SilentlyContinue
        }
        $env:JARVIS_LOG_FILE = $jarvisLog

        $monitorProcess = Start-JarvisMonitor -VenvPython $venvPython -LogPath $jarvisLog
        Write-Step "Arrancando Jarvis..."
        try {
            # Jarvis v2 lives in src/jarvis; the old root-level main.py no longer exists.
            # The bootstrap installs runtime dependencies, not the local package itself.
            # Add src explicitly so the launcher works from a fresh virtual environment.
            $srcPath = Join-Path $ProjectRoot "src"
            if ($env:PYTHONPATH) {
                $env:PYTHONPATH = "$srcPath;$($env:PYTHONPATH)"
            } else {
                $env:PYTHONPATH = $srcPath
            }
            & $venvPython -m jarvis --config (Join-Path $ProjectRoot "config.win.json") run
            Assert-LastExitCode "Ejecucion de jarvis"
        } finally {
            Stop-JarvisMonitor -MonitorProcess $monitorProcess
        }
    } else {
        Write-Step "Preparacion completa."
        Write-Host "Para arrancar Jarvis ahora: .\run_jarvis.ps1" -ForegroundColor Green
    }
} catch {
    Write-Host "[AUTO][ERROR] $($_.Exception.Message) (line $($_.InvocationInfo.ScriptLineNumber))" -ForegroundColor Red
    exit 1
}
