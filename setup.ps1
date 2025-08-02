#requires -version 5
<#
.SYNOPSIS
    A script to set up the Python development environment for this project on Windows.
.DESCRIPTION
    This script performs the following actions:
    1. Checks for a Python installation.
    2. Creates a virtual environment in the ./.venv directory.
    3. Installs required Python packages from requirements.txt and requirements-dev.txt.
    4. Installs pre-commit hooks.
#>

Write-Host "--- Starting Project Setup ---"

# --- Check for Python ---
$pythonPath = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonPath) {
    Write-Host "ERROR: python could not be found. Please install Python 3.9 or higher and ensure it's in your PATH."
    Exit 1
}
Write-Host "Python found at: $($pythonPath.Source)"

# --- Create Virtual Environment ---
$venvDir = ".\.venv"
if (Test-Path $venvDir) {
    Write-Host "Virtual environment '$venvDir' already exists. Skipping creation."
} else {
    Write-Host "Creating virtual environment in '$venvDir'..."
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv venv
    } else {
        python -m venv $venvDir
    }
    Write-Host "Virtual environment created."
}

# --- Activate Virtual Environment and Install Dependencies ---
Write-Host "Activating virtual environment and installing dependencies..."
$activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
& $activateScript

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "Using uv to install dependencies from pyproject.toml..."
    uv pip install -e ".[dev]"
} else {
    Write-Host "Using pip to install dependencies from pyproject.toml..."
    pip install -e ".[dev]"
}
Write-Host "Dependencies installed successfully."

# --- Set up pre-commit hooks ---
Write-Host "Installing pre-commit hooks..."
pre-commit install
Write-Host "Pre-commit hooks installed."

Write-Host ""
Write-Host "--- Project Setup Complete ---"
Write-Host "The virtual environment is ready and dependencies are installed."
Write-Host "To activate the virtual environment in your PowerShell session, run:"
Write-Host "$($activateScript)"
Write-Host "--------------------------------"
