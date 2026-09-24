param(
    [switch]$Run,
    [switch]$SkipModelPull,
    [switch]$ForceDependencies,
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
        return ($v -eq "3.11")
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
        throw "El valor de -PythonPath no es valido o no es Python 3.11: $PythonPath"
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
            "No se pudo detectar Python 3.11 tras la instalacion. " +
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

function Stop-OrphanJarvisPython {
    $rootNormalized = $ProjectRoot.ToLowerInvariant().Replace("/", "\")
    $targets = @("-m jarvis")

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

    if ($Run) {
        Stop-OrphanJarvisPython

        Write-Step "Arrancando Jarvis..."
        # The bootstrap installs runtime dependencies, not the package itself,
        # so src is added explicitly for a fresh virtual environment.
        $srcPath = Join-Path $ProjectRoot "src"
        if ($env:PYTHONPATH) {
            $env:PYTHONPATH = "$srcPath;$($env:PYTHONPATH)"
        } else {
            $env:PYTHONPATH = $srcPath
        }
        & $venvPython -m jarvis --config (Join-Path $ProjectRoot "config.win.json") run
        Assert-LastExitCode "Ejecucion de jarvis"
    } else {
        Write-Step "Preparacion completa."
        Write-Host "Para arrancar Jarvis ahora: .\run_jarvis.ps1" -ForegroundColor Green
    }
} catch {
    Write-Host "[AUTO][ERROR] $($_.Exception.Message) (line $($_.InvocationInfo.ScriptLineNumber))" -ForegroundColor Red
    exit 1
}
