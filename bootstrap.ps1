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

    $probeFile = Join-Path $env:TEMP ("jarvis_py_probe_" + [guid]::NewGuid().ToString("N") + ".py")
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

        $v = $probeOutput.ToString().Trim()
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

    $installerFile = Join-Path $env:TEMP ("python-3.11.9-amd64-" + [guid]::NewGuid().ToString("N") + ".exe")
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
        $installerFile = Join-Path $env:TEMP ("OllamaSetup-" + [guid]::NewGuid().ToString("N") + ".exe")

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
    try {
        Invoke-RestMethod -Method Get -Uri "http://localhost:11434/api/tags" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Ensure-OllamaRunning {
    param([string]$OllamaPath)

    if (Test-OllamaApi) {
        Write-Step "API de Ollama activa."
        return
    }

    Write-Step "Iniciando Ollama (ollama serve)..."
    Start-Process -FilePath $OllamaPath -ArgumentList "serve" -WindowStyle Hidden | Out-Null

    for ($i = 0; $i -lt 45; $i++) {
        if (Test-OllamaApi) {
            Write-Step "Ollama listo."
            return
        }
        Start-Sleep -Seconds 1
    }

    throw "Ollama no respondio en http://localhost:11434 despues de 45 segundos."
}

function Install-PythonDependencies {
    param([string]$VenvPython)

    $requirementsPath = Join-Path $ProjectRoot "requirements.txt"
    $requirementsHash = (Get-FileHash -Path $requirementsPath -Algorithm SHA256).Hash
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
        $tagsResponse = Invoke-RestMethod -Method Get -Uri "http://localhost:11434/api/tags" -TimeoutSec 4
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

    $wavCount = @(Get-ChildItem -Path $voiceDir -Filter *.wav -File -ErrorAction SilentlyContinue).Count
    if ($wavCount -lt 1) {
        $message = "No hay archivos WAV en voice_samples. Revisa voice_samples\README.md."
        if ($Run) {
            throw $message
        }
        Write-Warning $message
        return
    }

    Write-Step "Muestras de voz detectadas: $wavCount"
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

    Validate-VoiceSamples

    if ($Run) {
        Write-Step "Arrancando Jarvis..."
        & $venvPython (Join-Path $ProjectRoot "main.py")
        Assert-LastExitCode "Ejecucion de main.py"
    } else {
        Write-Step "Preparacion completa."
        Write-Host "Para arrancar Jarvis ahora: .\run_jarvis.ps1" -ForegroundColor Green
    }
} catch {
    Write-Host "[AUTO][ERROR] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
