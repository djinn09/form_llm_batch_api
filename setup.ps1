#requires -version 5
<#
.SYNOPSIS
    A script to set up the Python development environment for this project on Windows.
.DESCRIPTION
    This script performs the following actions:
    1. Checks for a Python installation.
    2. Creates a virtual environment in the ./.venv directory using 'uv' if available, otherwise 'venv'.
    3. Installs required Python packages from pyproject.toml.
    4. Installs pre-commit hooks for code quality checks.
#>

Write-Host "--- Starting Project Setup ---"

# --- Check for Python ---
# Verifies that Python is installed and accessible via the system's PATH.
# The project requires Python 3.9 or higher.
$pythonPath = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonPath) {
    Write-Host "ERROR: python could not be found. Please install Python 3.9 or higher and ensure it's in your PATH."
    Exit 1
}
Write-Host "Python found at: $($pythonPath.Source)"

# --- Create Virtual Environment ---
# Creates a virtual environment in the '.\.venv' directory to isolate project dependencies.
# It prefers using 'uv' for its speed, but falls back to Python's built-in 'venv' module if 'uv' isn't installed.
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
# Specifies the path to the activation script for PowerShell.
$activateScript = Join-Path $venvDir "Scripts\Activate.ps1"
# Executes the activation script in the current scope.
& $activateScript

# Installs dependencies using 'uv' if available, otherwise defaults to 'pip'.
# The '-e .[dev]' syntax installs the project in editable mode along with development extras.
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "Using uv to install dependencies from pyproject.toml..."
    uv pip install -e ".[dev]"
} else {
    Write-Host "Using pip to install dependencies from pyproject.toml..."
    pip install -e ".[dev]"
}
Write-Host "Dependencies installed successfully."

# --- Set up pre-commit hooks ---
# Installs pre-commit hooks which automatically run code quality checks before each commit.
Write-Host "Installing pre-commit hooks..."
pre-commit install
Write-Host "Pre-commit hooks installed."

# --- Final Instructions ---
Write-Host ""
Write-Host "--- Project Setup Complete ---"
Write-Host "The virtual environment is ready and dependencies are installed."
Write-Host "To activate the virtual environment in your PowerShell session, run:"
Write-Host "$($activateScript)"
Write-Host "--------------------------------"
